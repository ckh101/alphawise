"""
PDF 生成 Handler

使用 fpdf2 从 markdown 生成 PDF，包含元信息页眉。
注意：前端优先使用 Electron Chromium 渲染（完美渲染），此 Handler 作为后端备用。
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from html import escape

logger = logging.getLogger(__name__)


def _get_chinese_font_path() -> str | None:
    """查找系统中的中文字体"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
        r"C:\Windows\Fonts\simhei.ttf",     # 黑体
        r"C:\Windows\Fonts\simsun.ttc",     # 宋体
        "/System/Library/Fonts/PingFang.ttc",   # macOS
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _strip_html(text: str) -> str:
    """去除 HTML 标签，得到纯文本"""
    clean = re.sub(r"<[^>]+>", "", text)
    # 解码常见实体
    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    clean = clean.replace("&quot;", "\"").replace("&#39;", "'")
    return clean


def _markdown_to_plaintext(markdown: str) -> str:
    """简单的 markdown → 纯文本，保留结构"""
    if not markdown:
        return ""

    text = markdown

    # 代码块 → 缩进
    text = re.sub(r"```\w*\n(.*?)```", r"\n[代码]\n\1\n", text, flags=re.DOTALL)
    # 行内代码 → 标出
    text = re.sub(r"`([^`]+)`", r"[\1]", text)
    # 标题 → 保留文本 + 下划线
    text = re.sub(r"^#{1,3}\s+(.+)$", r"\n\1\n" + "-" * 30, text, flags=re.MULTILINE)
    # 粗体 → 去标记
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # 链接 → 只保留文本
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # HTML 标签 → 去除
    text = re.sub(r"<[^>]+>", "", text)
    # 表格 → 保留分隔符
    text = re.sub(r"^\|(.+)\|$", r"  \1", text, flags=re.MULTILINE)
    # 水平线 → 保留
    text = re.sub(r"^---+$", "-" * 40, text, flags=re.MULTILINE)
    # 连续空行 → 压缩
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


async def handle_generate_pdf(method: str, params: dict) -> dict:
    """
    生成 PDF（fpdf2 后端备用方案）

    参数:
        markdown: str - markdown 内容
        stock_name: str - 股票名称
        stock_symbol: str - 股票代码
        generated_at: str - 生成时间（可选）
    """
    markdown = params.get("markdown", "")
    metadata = {
        "stock_name": params.get("stock_name", ""),
        "stock_symbol": params.get("stock_symbol", ""),
        "generated_at": params.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }

    if not markdown:
        return {"status": "error", "message": "缺少 markdown 内容", "code": 400}

    font_path = _get_chinese_font_path()
    if not font_path:
        return {
            "status": "error",
            "message": "系统中未找到中文字体文件",
            "code": 500,
        }

    try:
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_font("cjk", "", font_path)
        pdf.set_auto_page_break(auto=True, margin=15)

        # ===== 封面 / 页眉 =====
        pdf.add_page()
        pdf.set_font("cjk", "", 18)
        pdf.cell(0, 12, "灵智投研助手 - 深度分析报告", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("cjk", "", 9)
        stock_label = metadata["stock_name"] or metadata["stock_symbol"]
        header_text = f"股票: {stock_label} ({metadata['stock_symbol']})  |  生成: {metadata['generated_at']}"
        pdf.cell(0, 6, header_text, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # 分隔线
        y = pdf.get_y()
        pdf.line(10, y, 200, y)
        pdf.ln(6)

        # ===== 正文 =====
        pdf.set_font("cjk", "", 11)
        plain = _markdown_to_plaintext(markdown)

        # 按段落输出
        for para in plain.split("\n\n"):
            para = para.strip()
            if not para:
                continue

            # 标题行（下划线）
            if para.endswith("-" * 30):
                title = para.replace("-" * 30, "").strip()
                pdf.set_font("cjk", "", 14)
                pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("cjk", "", 11)
                pdf.ln(2)
                continue

            # 正常段落
            for line in para.split("\n"):
                line = line.strip()
                if line.startswith("- "):
                    pdf.cell(5, 5, "•")
                    pdf.multi_cell(0, 5, line[2:], new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.multi_cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")

            pdf.ln(1)

        # 输出 PDF
        pdf_bytes = pdf.output()
        import base64

        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        filename = f"{metadata['stock_symbol']}_{metadata['stock_name']}_分析报告.pdf" if metadata['stock_symbol'] else "分析报告.pdf"

        return {
            "status": "success",
            "data": {
                "pdf_base64": pdf_base64,
                "filename": filename,
            },
        }

    except ImportError:
        return {
            "status": "error",
            "message": "PDF 生成服务不可用，请安装 fpdf2",
            "code": 500,
        }
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)
        return {"status": "error", "message": f"PDF 生成失败: {str(e)}", "code": 500}
