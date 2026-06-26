"""
配置管理 API
"""

import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from harness.core.database import (
    get_all_settings, update_settings, get_llm_config, is_llm_configured,
    is_builtin_skill, get_disabled_sdk_skills, set_skill_enabled,
    get_mcp_configs, set_mcp_configs,
    get_llm_providers, save_llm_providers, get_active_llm_provider,
    set_active_llm_provider,
)
from harness.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _mask_api_key(key: str) -> str:
    """脱敏 API Key"""
    if not key or len(key) < 8:
        return "****" if key else ""
    return key[:4] + "****" + key[-4:]


@router.get("")
async def list_settings():
    """获取所有配置（敏感字段脱敏）"""
    all_settings = get_all_settings()

    result = {}
    for key, value in all_settings.items():
        if "api_key" in key:
            result[key] = _mask_api_key(value)
        else:
            result[key] = value

    return {"code": 0, "data": result}


@router.get("/llm-status")
async def llm_status():
    """检查大模型是否已配置"""
    configured = is_llm_configured()
    config = get_llm_config() if configured else {}
    return {
        "code": 0,
        "data": {
            "configured": configured,
            "model": config.get("llm.model", ""),
        }
    }


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, str]


@router.put("")
async def save_settings(req: UpdateSettingsRequest):
    """批量更新配置"""
    try:
        update_settings(req.settings)
        logger.info(f"Settings updated: {list(req.settings.keys())}")
        return {"code": 0, "message": "配置已保存"}
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-connection")
async def test_connection():
    """测试大模型连接"""
    try:
        llm_config = get_llm_config()
        api_key = llm_config.get("llm.api_key", "")

        if not api_key:
            return {"code": 1, "message": "API Key 未配置"}

        if not llm_config.get("llm.model"):
            return {"code": 1, "message": "模型名称未配置"}

        from harness.services.glm_agent_client import create_message

        result = await create_message(
            messages=[{"role": "user", "content": "你好，请回复「连接成功」"}],
            timeout=30
        )

        if result and result.get("content"):
            return {"code": 0, "message": "连接成功"}
        else:
            return {"code": 1, "message": "连接失败：模型未返回响应"}

    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return {"code": 1, "message": f"连接失败：{str(e)}"}


# ===== 多厂商 LLM Provider 管理 =====

@router.get("/llm-providers")
async def list_llm_providers():
    """获取所有 LLM provider（api_key 脱敏）"""
    providers = get_llm_providers()
    active = get_active_llm_provider()
    active_id = active.get("id", "") if active else ""

    # 脱敏
    masked = []
    for p in providers:
        item = {**p}
        if item.get("api_key"):
            item["api_key_masked"] = _mask_api_key(item["api_key"])
        masked.append(item)

    return {"code": 0, "data": {"providers": masked, "active": active_id}}


class SaveProvidersRequest(BaseModel):
    providers: list[dict]


@router.put("/llm-providers")
async def save_providers(req: SaveProvidersRequest):
    """批量保存 providers"""
    for p in req.providers:
        if not p.get("id"):
            raise HTTPException(400, "每个 provider 必须包含 id")
        if not p.get("name"):
            raise HTTPException(400, "每个 provider 必须包含 name")

    save_llm_providers(req.providers)
    logger.info(f"LLM providers saved: {[p['id'] for p in req.providers]}")
    return {"code": 0, "message": "厂商配置已保存"}


class SetActiveProviderRequest(BaseModel):
    provider_id: str


@router.put("/llm-active")
async def set_active_provider(req: SetActiveProviderRequest):
    """切换激活的 provider"""
    providers = get_llm_providers()
    if not any(p.get("id") == req.provider_id for p in providers):
        raise HTTPException(400, f"Provider not found: {req.provider_id}")
    set_active_llm_provider(req.provider_id)
    logger.info(f"Active LLM provider set to: {req.provider_id}")
    return {"code": 0, "message": "已切换"}


# ===== SDK Skills 管理 =====

def _get_skills_dir() -> Path:
    """获取 .claude/skills 目录（始终在 backend/ 下）"""
    backend_dir = Path(__file__).parent.parent.parent.parent
    return backend_dir / ".claude" / "skills"


def _parse_skill_frontmatter(skill_md: Path) -> dict:
    """解析 SKILL.md 的 YAML frontmatter（只取顶层简单 key: value）"""
    metadata = {}
    try:
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                frontmatter = content[3:end].strip()
                for line in frontmatter.split("\n"):
                    stripped = line.strip()
                    # 跳过列表项、缩进行、空行
                    if not stripped or stripped.startswith("-") or line.startswith(" ") or line.startswith("\t"):
                        continue
                    if ":" in stripped:
                        key, _, value = stripped.partition(":")
                        key = key.strip()
                        # 只取每个 key 的第一个值
                        if key and key not in metadata:
                            value = value.strip().strip('"').strip("'")
                            metadata[key] = value
    except Exception as e:
        logger.warning(f"Failed to parse SKILL.md: {e}")
    return metadata


@router.get("/skills")
async def list_sdk_skills():
    """列出 .claude/skills/ 下所有 skills"""
    skills_dir = _get_skills_dir()
    disabled_skills = get_disabled_sdk_skills()
    builtin_skills = []
    custom_skills = []

    if not skills_dir.exists():
        return {"code": 0, "data": {"builtin": builtin_skills, "custom": custom_skills}}

    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir():
            continue
        # 跳过 .disabled 后缀的目录
        if skill_path.name.endswith(".disabled"):
            continue

        skill_md = skill_path / "SKILL.md"
        meta = _parse_skill_frontmatter(skill_md) if skill_md.exists() else {}
        name = meta.get("name", skill_path.name)

        info = {
            "name": name,
            "dir_name": skill_path.name,
            "display_name": meta.get("display_name", meta.get("title", name)),
            "description": meta.get("description", ""),
            "version": meta.get("version", "0.0.0"),
            "author": meta.get("author", ""),
            "enabled": name not in disabled_skills,
            "builtin": is_builtin_skill(name),
        }

        if is_builtin_skill(name):
            builtin_skills.append(info)
        else:
            custom_skills.append(info)

    return {"code": 0, "data": {"builtin": builtin_skills, "custom": custom_skills}}


@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...)):
    """上传 zip 包并解压到 .claude/skills/"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "只支持 .zip 格式")

    skills_dir = _get_skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件到临时目录
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        with open(zip_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # 校验 zip 完整性
        if not zipfile.is_zipfile(zip_path):
            raise HTTPException(400, "无效的 ZIP 文件")

        # 解压到临时子目录
        extract_dir = Path(tmp_dir) / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # 查找 SKILL.md（可能在根目录或子目录中）
        skill_md = None
        skill_root = extract_dir
        for md_path in extract_dir.rglob("SKILL.md"):
            skill_md = md_path
            skill_root = md_path.parent
            break

        if not skill_md:
            raise HTTPException(400, "ZIP 中未找到 SKILL.md 文件")

        # 解析 skill name
        meta = _parse_skill_frontmatter(skill_md)
        skill_name = meta.get("name", skill_root.name)

        # 不允许覆盖内置 skill
        if is_builtin_skill(skill_name):
            raise HTTPException(403, f"不允许覆盖内置技能: {skill_name}")

        # 检查是否存在同名 .disabled 目录
        disabled_dir = skills_dir / f"{skill_name}.disabled"
        if disabled_dir.exists():
            shutil.rmtree(disabled_dir)

        # 如果已存在，覆盖更新
        target_dir = skills_dir / skill_name
        if target_dir.exists():
            shutil.rmtree(target_dir)

        # 复制到目标目录
        shutil.copytree(skill_root, target_dir)

        logger.info(f"Skill uploaded: {skill_name}")

        return {
            "code": 0,
            "message": "技能上传成功",
            "data": {
                "name": skill_name,
                "display_name": meta.get("display_name", meta.get("title", skill_name)),
                "description": meta.get("description", ""),
                "version": meta.get("version", "0.0.0"),
                "author": meta.get("author", ""),
            }
        }


@router.delete("/skills/{skill_name}")
async def delete_skill(skill_name: str):
    """删除自定义 skill（不允许删除内置 skill）"""
    if is_builtin_skill(skill_name):
        raise HTTPException(403, f"不允许删除内置技能: {skill_name}")

    skills_dir = _get_skills_dir()
    target_dir = skills_dir / skill_name

    if not target_dir.exists():
        # 检查是否被禁用（.disabled 后缀）
        disabled_dir = skills_dir / f"{skill_name}.disabled"
        if disabled_dir.exists():
            shutil.rmtree(disabled_dir)
            return {"code": 0, "message": f"技能已删除: {skill_name}"}
        raise HTTPException(404, f"技能不存在: {skill_name}")

    shutil.rmtree(target_dir)
    logger.info(f"Skill deleted: {skill_name}")
    return {"code": 0, "message": f"技能已删除: {skill_name}"}


class UpdateSkillStatusRequest(BaseModel):
    skills: dict[str, bool]


@router.put("/skills/status")
async def update_skill_status(req: UpdateSkillStatusRequest):
    """启用/禁用 SDK skill"""
    for name, enabled in req.skills.items():
        if is_builtin_skill(name):
            raise HTTPException(403, f"不允许禁用内置技能: {name}")

        skills_dir = _get_skills_dir()
        normal_dir = skills_dir / name
        disabled_dir = skills_dir / f"{name}.disabled"

        if enabled:
            # 启用：重命名 .disabled → 正常目录
            if disabled_dir.exists() and not normal_dir.exists():
                disabled_dir.rename(normal_dir)
        else:
            # 禁用：重命名正常目录 → .disabled
            if normal_dir.exists() and not disabled_dir.exists():
                normal_dir.rename(disabled_dir)

        set_skill_enabled(name, enabled)

    return {"code": 0, "message": "技能状态已更新"}


# ===== MCP 服务配置 =====

@router.get("/mcp")
async def list_mcp_configs():
    """获取 MCP 服务配置"""
    configs = get_mcp_configs()
    return {"code": 0, "data": {"configs": configs}}


class UpdateMcpConfigsRequest(BaseModel):
    configs: list[dict]


@router.put("/mcp")
async def update_mcp_configs(req: UpdateMcpConfigsRequest):
    """更新 MCP 服务配置"""
    for cfg in req.configs:
        if not cfg.get("id"):
            raise HTTPException(400, "每个 MCP 配置必须包含 id")
        if not cfg.get("name"):
            raise HTTPException(400, "每个 MCP 配置必须包含 name")
    set_mcp_configs(req.configs)
    return {"code": 0, "message": "MCP 配置已保存"}
