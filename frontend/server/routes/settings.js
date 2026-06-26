/**
 * Settings API 路由
 *
 * 迁移自 backend/harness/api/routers/settings.py
 * 请求/响应格式与 Python 后端完全一致。
 */

const path = require('path');
const fs = require('fs');
const os = require('os');
const AdmZip = require('adm-zip');

const BUILTIN_SKILL_PREFIXES = ['mx-'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function maskApiKey(key) {
  if (!key) return '';
  if (key.length < 8) return '****';
  return key.slice(0, 4) + '****' + key.slice(-4);
}

function isBuiltinSkill(name) {
  return BUILTIN_SKILL_PREFIXES.some(p => name.startsWith(p));
}

function getSkillsDir(backendDir) {
  return path.join(backendDir, '.claude', 'skills');
}

/**
 * 解析 SKILL.md 的 YAML frontmatter（只取顶层简单 key: value）
 */
function parseSkillFrontmatter(skillMdPath) {
  const metadata = {};
  try {
    const content = fs.readFileSync(skillMdPath, 'utf8');
    if (!content.startsWith('---')) return metadata;
    const end = content.indexOf('---', 3);
    if (end < 0) return metadata;
    const frontmatter = content.slice(3, end).trim();
    for (const line of frontmatter.split('\n')) {
      const stripped = line.trim();
      if (!stripped || stripped.startsWith('-') || line.startsWith(' ') || line.startsWith('\t')) continue;
      const colonIdx = stripped.indexOf(':');
      if (colonIdx < 0) continue;
      const key = stripped.slice(0, colonIdx).trim();
      if (!key || key in metadata) continue;
      let value = stripped.slice(colonIdx + 1).trim().replace(/^["']|["']$/g, '');
      metadata[key] = value;
    }
  } catch {
    // ignore parse errors
  }
  return metadata;
}

// ---------------------------------------------------------------------------
// Route registration
// ---------------------------------------------------------------------------

module.exports = async function (fastify, opts) {
  const { backendDir } = opts;

  // db functions — passed via opts or required directly
  const db = opts.db || require('../lib/db');

  // ---------------------------------------------------------------------------
  // YAML 配置 fallback：当数据库中缺少 llm.* 时，从 YAML 读取
  // ---------------------------------------------------------------------------
  function getYamlLlmFallback() {
    try {
      const yaml = require('js-yaml');
      const configDir = path.join(backendDir, 'resources', 'config');
      const env = process.env.HARNESS_ENV || 'dev';
      const yamlPath = path.join(configDir, `config.${env}.yaml`);
      if (!fs.existsSync(yamlPath)) return {};
      const cfg = yaml.load(fs.readFileSync(yamlPath, 'utf8'));
      const llm = cfg?.llm || cfg?.glm || cfg?.iwencai || {};
      return {
        'llm.api_key': llm.api_key || '',
        'llm.base_url': llm.base_url || '',
        'llm.model': llm.model || '',
        'llm.timeout': String(llm.timeout || 60),
      };
    } catch {
      return {};
    }
  }

  // -----------------------------------------------------------------------
  // GET / — 获取所有设置（敏感字段脱敏）
  // -----------------------------------------------------------------------
  fastify.get('/', async () => {
    const allSettings = await db.getAllSettings();
    const result = {};
    for (const [key, value] of Object.entries(allSettings)) {
      result[key] = key.includes('api_key') ? maskApiKey(value) : value;
    }
    // YAML fallback：如果数据库中没有 llm.api_key，从配置文件补充
    if (!result['llm.api_key']) {
      const fallback = getYamlLlmFallback();
      for (const [k, v] of Object.entries(fallback)) {
        if (!result[k]) {
          result[k] = k.includes('api_key') ? maskApiKey(v) : v;
        }
      }
    }
    return { code: 0, data: result };
  });

  // -----------------------------------------------------------------------
  // PUT / — 批量更新配置
  // -----------------------------------------------------------------------
  fastify.put('/', async (request, reply) => {
    const { settings } = request.body;
    try {
      await db.updateSettings(settings);
      request.log.info(`Settings updated: ${Object.keys(settings).join(', ')}`);
      return { code: 0, message: '配置已保存' };
    } catch (e) {
      request.log.error(`Failed to save settings: ${e}`);
      reply.code(500);
      return { detail: String(e) };
    }
  });

  // -----------------------------------------------------------------------
  // POST /test-connection — 测试 LLM 连接
  // -----------------------------------------------------------------------
  fastify.post('/test-connection', async (request) => {
    try {
      const llmConfig = db.getLlmConfig();
      const apiKey = llmConfig['llm.api_key'] || '';

      if (!apiKey) return { code: 1, message: 'API Key 未配置' };
      if (!llmConfig['llm.model']) return { code: 1, message: '模型名称未配置' };

      // 委托给后端 LLM 服务完成连接测试
      const glmClient = require('../lib/glm-client');
      const result = await glmClient.chat({
        messages: [{ role: 'user', content: '你好，请回复「连接成功」' }],
        timeout: 30000,
      }, llmConfig);

      if (result && result.content) {
        return { code: 0, message: '连接成功' };
      }
      return { code: 1, message: '连接失败：模型未返回响应' };
    } catch (e) {
      request.log.error(`Connection test failed: ${e}`);
      return { code: 1, message: `连接失败：${e.message || e}` };
    }
  });

  // -----------------------------------------------------------------------
  // GET /llm-status — 获取 LLM 状态
  // -----------------------------------------------------------------------
  fastify.get('/llm-status', async () => {
    // 优先从 active provider 判断，无 provider 时检查旧扁平配置
    const llmConfig = db.getLlmConfig();
    const configured = !!(llmConfig['llm.api_key'] && llmConfig['llm.model']);
    return {
      code: 0,
      data: {
        configured,
        model: configured ? (llmConfig['llm.model'] || '') : '',
      },
    };
  });

  // -----------------------------------------------------------------------
  // GET /llm-providers — 列出所有 LLM 厂商（api_key 脱敏）
  // -----------------------------------------------------------------------
  fastify.get('/llm-providers', async () => {
    const providers = db.getLlmProviders();
    const active = db.getActiveLlmProvider();
    const activeId = active ? active.id : '';

    const masked = providers.map(p => {
      const item = { ...p };
      if (item.api_key) item.api_key_masked = maskApiKey(item.api_key);
      return item;
    });

    return { code: 0, data: { providers: masked, active: activeId } };
  });

  // -----------------------------------------------------------------------
  // PUT /llm-providers — 批量保存 LLM 厂商
  // -----------------------------------------------------------------------
  fastify.put('/llm-providers', async (request) => {
    const { providers } = request.body || {};
    if (!Array.isArray(providers)) {
      return { code: 1, message: 'providers 必须是数组' };
    }
    for (const p of providers) {
      if (!p.id) return { code: 1, message: '每个 provider 必须包含 id' };
      if (!p.name) return { code: 1, message: '每个 provider 必须包含 name' };
    }
    db.saveLlmProviders(providers);
    return { code: 0, message: '厂商配置已保存' };
  });

  // -----------------------------------------------------------------------
  // PUT /llm-active — 切换激活的 provider
  // -----------------------------------------------------------------------
  fastify.put('/llm-active', async (request) => {
    const { provider_id } = request.body || {};
    if (!provider_id) return { code: 1, message: 'provider_id 必填' };
    const providers = db.getLlmProviders();
    if (!providers.some(p => p.id === provider_id)) {
      return { code: 1, message: `Provider not found: ${provider_id}` };
    }
    db.setActiveLlmProvider(provider_id);
    return { code: 0, message: '已切换' };
  });

  // -----------------------------------------------------------------------
  // GET /env-vars — 列出所有自定义环境变量
  // -----------------------------------------------------------------------
  fastify.get('/env-vars', async () => {
    const vars = db.getEnvVars();
    return { code: 0, data: vars };
  });

  // -----------------------------------------------------------------------
  // PUT /env-vars — 全量保存环境变量（覆盖）
  // -----------------------------------------------------------------------
  fastify.put('/env-vars', async (request) => {
    const { env_vars } = request.body || {};
    if (!env_vars || typeof env_vars !== 'object') {
      return { code: 1, message: 'env_vars 必填（object）' };
    }
    // 先删除旧的全部，再写入新的
    const oldKeys = Object.keys(db.getEnvVars());
    if (oldKeys.length > 0) db.deleteEnvVars(oldKeys);
    if (Object.keys(env_vars).length > 0) db.saveEnvVars(env_vars);
    return { code: 0, message: '已保存（重启 Worker 后生效）' };
  });

  // -----------------------------------------------------------------------
  // GET /skills — 列出 Skills
  // -----------------------------------------------------------------------
  fastify.get('/skills', async () => {
    const skillsDir = getSkillsDir(backendDir);
    const disabledSkills = await db.getDisabledSdkSkills();

    const builtinSkills = [];
    const customSkills = [];

    if (!fs.existsSync(skillsDir)) {
      return { code: 0, data: { builtin: builtinSkills, custom: customSkills } };
    }

    const entries = fs.readdirSync(skillsDir).sort();
    for (const name of entries) {
      const skillPath = path.join(skillsDir, name);
      if (!fs.statSync(skillPath).isDirectory()) continue;
      const isDisabledDir = name.endsWith('.disabled');

      const skillMd = path.join(skillPath, 'SKILL.md');
      const meta = fs.existsSync(skillMd) ? parseSkillFrontmatter(skillMd) : {};
      const skillName = meta.name || name.replace(/\.disabled$/, '');

      const info = {
        name: skillName,
        dir_name: name,
        display_name: meta.display_name || meta.title || skillName,
        description: meta.description || '',
        version: meta.version || '0.0.0',
        author: meta.author || '',
        enabled: !isDisabledDir && !disabledSkills.includes(skillName),
        builtin: isBuiltinSkill(skillName),
      };

      if (isBuiltinSkill(skillName)) {
        builtinSkills.push(info);
      } else {
        customSkills.push(info);
      }
    }

    return { code: 0, data: { builtin: builtinSkills, custom: customSkills } };
  });

  // -----------------------------------------------------------------------
  // POST /skills/upload — 上传 Skill（zip 文件）
  // -----------------------------------------------------------------------
  fastify.post('/skills/upload', async (request, reply) => {
    const data = await request.file();
    if (!data || !data.filename || !data.filename.endsWith('.zip')) {
      reply.code(400);
      return { detail: '只支持 .zip 格式' };
    }

    const skillsDir = getSkillsDir(backendDir);
    fs.mkdirSync(skillsDir, { recursive: true });

    // 保存上传文件到临时目录
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-upload-'));
    try {
      const zipPath = path.join(tmpDir, 'upload.zip');
      const buffer = await data.toBuffer();
      fs.writeFileSync(zipPath, buffer);

      let zip;
      try {
        zip = new AdmZip(zipPath);
      } catch {
        reply.code(400);
        return { detail: '无效的 ZIP 文件' };
      }

      // 解压到临时子目录
      const extractDir = path.join(tmpDir, 'extracted');
      zip.extractAllTo(extractDir, true);

      // 查找 SKILL.md（可能在根目录或子目录中）
      let skillMd = null;
      let skillRoot = extractDir;
      function findSkillMd(dir) {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name);
          if (entry.isFile() && entry.name === 'SKILL.md') {
            skillMd = fullPath;
            skillRoot = dir;
            return true;
          }
          if (entry.isDirectory()) {
            if (findSkillMd(fullPath)) return true;
          }
        }
        return false;
      }
      findSkillMd(extractDir);

      if (!skillMd) {
        reply.code(400);
        return { detail: 'ZIP 中未找到 SKILL.md 文件' };
      }

      // 解析 skill name
      const meta = parseSkillFrontmatter(skillMd);
      const skillName = meta.name || path.basename(skillRoot);

      // 不允许覆盖内置 skill
      if (isBuiltinSkill(skillName)) {
        reply.code(403);
        return { detail: `不允许覆盖内置技能: ${skillName}` };
      }

      // 检查是否存在同名 .disabled 目录
      const disabledDir = path.join(skillsDir, `${skillName}.disabled`);
      if (fs.existsSync(disabledDir)) {
        fs.rmSync(disabledDir, { recursive: true, force: true });
      }

      // 如果已存在，覆盖更新
      const targetDir = path.join(skillsDir, skillName);
      if (fs.existsSync(targetDir)) {
        fs.rmSync(targetDir, { recursive: true, force: true });
      }

      // 复制到目标目录
      fs.cpSync(skillRoot, targetDir, { recursive: true });

      request.log.info(`Skill uploaded: ${skillName}`);

      return {
        code: 0,
        message: '技能上传成功',
        data: {
          name: skillName,
          display_name: meta.display_name || meta.title || skillName,
          description: meta.description || '',
          version: meta.version || '0.0.0',
          author: meta.author || '',
        },
      };
    } finally {
      // 清理临时目录
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  // -----------------------------------------------------------------------
  // DELETE /skills/:name — 删除 Skill
  // -----------------------------------------------------------------------
  fastify.delete('/skills/:name', async (request, reply) => {
    const { name: skillName } = request.params;

    if (isBuiltinSkill(skillName)) {
      reply.code(403);
      return { detail: `不允许删除内置技能: ${skillName}` };
    }

    const skillsDir = getSkillsDir(backendDir);
    const targetDir = path.join(skillsDir, skillName);

    if (!fs.existsSync(targetDir)) {
      // 检查是否被禁用（.disabled 后缀）
      const disabledDir = path.join(skillsDir, `${skillName}.disabled`);
      if (fs.existsSync(disabledDir)) {
        fs.rmSync(disabledDir, { recursive: true, force: true });
        return { code: 0, message: `技能已删除: ${skillName}` };
      }
      reply.code(404);
      return { detail: `技能不存在: ${skillName}` };
    }

    fs.rmSync(targetDir, { recursive: true, force: true });
    request.log.info(`Skill deleted: ${skillName}`);
    return { code: 0, message: `技能已删除: ${skillName}` };
  });

  // -----------------------------------------------------------------------
  // PUT /skills/status — 更新 Skill 状态（启用/禁用）
  // -----------------------------------------------------------------------
  fastify.put('/skills/status', async (request, reply) => {
    const { skills } = request.body; // { name: boolean }

    for (const [name, enabled] of Object.entries(skills)) {
      if (isBuiltinSkill(name)) {
        reply.code(403);
        return { detail: `不允许禁用内置技能: ${name}` };
      }

      const skillsDir = getSkillsDir(backendDir);
      const normalDir = path.join(skillsDir, name);
      const disabledDir = path.join(skillsDir, `${name}.disabled`);

      if (enabled) {
        // 启用：重命名 .disabled → 正常目录
        if (fs.existsSync(disabledDir) && !fs.existsSync(normalDir)) {
          fs.renameSync(disabledDir, normalDir);
        }
      } else {
        // 禁用：重命名正常目录 → .disabled
        if (fs.existsSync(normalDir) && !fs.existsSync(disabledDir)) {
          fs.renameSync(normalDir, disabledDir);
        }
      }

      await db.setSkillEnabled(name, enabled);
    }

    return { code: 0, message: '技能状态已更新' };
  });

  // -----------------------------------------------------------------------
  // GET /mcp — 获取 MCP 配置
  // -----------------------------------------------------------------------
  fastify.get('/mcp', async () => {
    const configs = await db.getMcpConfigs();
    return { code: 0, data: { configs } };
  });

  // -----------------------------------------------------------------------
  // PUT /mcp — 更新 MCP 配置
  // -----------------------------------------------------------------------
  fastify.put('/mcp', async (request, reply) => {
    const { configs } = request.body; // array of objects

    for (const cfg of configs) {
      if (!cfg.id) {
        reply.code(400);
        return { detail: '每个 MCP 配置必须包含 id' };
      }
      if (!cfg.name) {
        reply.code(400);
        return { detail: '每个 MCP 配置必须包含 name' };
      }
    }

    await db.setMcpConfigs(configs);
    return { code: 0, message: 'MCP 配置已保存' };
  });

  // -----------------------------------------------------------------------
  // GET /logs — 获取日志内容
  // Query: source (electron|server|worker|all), level (info|warn|error|all), lines (默认200)
  // -----------------------------------------------------------------------
  fastify.get('/logs', async (request) => {
    try {
      const source = request.query.source || 'all';
      const level = request.query.level || 'all';
      const lines = parseInt(request.query.lines) || 200;

      const logDir = process.env.HARNESS_LOG_DIR
        || path.join(path.dirname(path.dirname(__dirname)), '..', 'logs');

      if (!fs.existsSync(logDir)) {
        return { code: 0, data: { logs: '', total: 0 } };
      }

      const files = [];
      if (source === 'all' || source === 'electron') files.push('electron.log');
      if (source === 'all' || source === 'server') files.push('server.log');
      if (source === 'all' || source === 'worker') files.push('worker.log');

      let allLines = [];
      for (const f of files) {
        const fp = path.join(logDir, f);
        if (fs.existsSync(fp)) {
          const content = fs.readFileSync(fp, 'utf8');
          const fileLines = content.split('\n').filter(Boolean);
          allLines.push(...fileLines.map(l => ({ source: f.replace('.log', ''), text: l })));
        }
      }

      allLines.sort((a, b) => a.text.localeCompare(b.text));

      if (level !== 'all') {
        const upper = level.toUpperCase();
        allLines = allLines.filter(l => l.text.includes(`[${upper}]`));
      }

      const result = allLines.slice(-lines);

      return {
        code: 0,
        data: {
          logs: result.map(l => l.text).join('\n'),
          total: result.length,
        },
      };
    } catch (e) {
      request.log.error(`Failed to read logs: ${e}`);
      return { code: 1, message: String(e) };
    }
  });

  // -----------------------------------------------------------------------
  // GET /logs/download — 打包日志目录为 zip
  // -----------------------------------------------------------------------
  fastify.get('/logs/download', async (request, reply) => {
    try {
      const logDir = process.env.HARNESS_LOG_DIR
        || path.join(path.dirname(path.dirname(__dirname)), '..', 'logs');

      if (!fs.existsSync(logDir)) {
        reply.code(404);
        return { code: 1, message: '日志目录不存在' };
      }

      const AdmZip = require('adm-zip');
      const zip = new AdmZip();
      const files = ['electron.log', 'server.log', 'worker.log', 'errors.log'];
      for (const f of files) {
        const fp = path.join(logDir, f);
        if (fs.existsSync(fp)) {
          zip.addLocalFile(fp);
        }
      }

      const buf = zip.toBuffer();
      reply.header('Content-Type', 'application/zip');
      reply.header('Content-Disposition', 'attachment; filename="harness-logs.zip"');
      reply.send(buf);
    } catch (e) {
      request.log.error(`Failed to download logs: ${e}`);
      reply.code(500);
      return { detail: String(e) };
    }
  });

  // -----------------------------------------------------------------------
  // GET /logs/path — 返回日志目录路径
  // -----------------------------------------------------------------------
  fastify.get('/logs/path', async () => {
    const logDir = process.env.HARNESS_LOG_DIR
      || path.join(path.dirname(path.dirname(__dirname)), '..', 'logs');

    return { code: 0, data: { path: logDir } };
  });
};
