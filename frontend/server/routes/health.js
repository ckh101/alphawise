/**
 * 健康检查和应用信息路由
 */

module.exports = async function (fastify, opts) {
  const { backendDir, configDir } = opts;

  // 加载配置（简单读取 YAML）
  let appConfig = { name: 'harness-investment-research', version: '0.1.0', environment: 'production', description: '灵智投研助手' };
  try {
    const yaml = require('js-yaml');
    const fs = require('fs');
    const path = require('path');
    const env = process.env.HARNESS_ENV || (isDev() ? 'dev' : 'prod');
    const configFile = path.join(configDir, `config.${env}.yaml`);
    if (fs.existsSync(configFile)) {
      const raw = yaml.load(fs.readFileSync(configFile, 'utf8'));
      if (raw?.app) appConfig = { ...appConfig, ...raw.app };
    }
  } catch (e) {
    // 配置加载失败用默认值
  }

  fastify.get('/health', async () => ({
    status: 'healthy',
    app: appConfig.name,
    version: appConfig.version,
    environment: appConfig.environment,
  }));

  fastify.get('/api/v1/info', async () => ({
    name: appConfig.name,
    version: appConfig.version,
    description: appConfig.description,
    environment: appConfig.environment,
    debug: appConfig.debug || false,
  }));
};

function isDev() {
  return process.env.NODE_ENV === 'development';
}
