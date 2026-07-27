/**
 * Worker HTTP 客户端
 *
 * 封装对 http://127.0.0.1:9999 的 JSON-RPC 调用。
 */

const http = require('http');

const WORKER_HOST = '127.0.0.1';
const WORKER_PORT = 9999;

class WorkerClient {
  /**
   * 就绪检查 GET /ready（Agent 初始化完成后才返回 ok）
   * /health 只检查进程存活，/ready 检查功能就绪
   */
  async healthCheck() {
    try {
      const result = await this._get('/ready');
      return result && result.status === 'ok';
    } catch {
      return false;
    }
  }

  /**
   * POST /rpc — 普通 JSON-RPC 调用
   */
  async request(method, params = {}) {
    const body = JSON.stringify({ jsonrpc: '2.0', method, params, id: 1 });
    const data = await this._post('/rpc', body);
    if (data.error) {
      throw new Error(`RPC error: ${data.error.message || JSON.stringify(data.error)}`);
    }
    return data.result;
  }

  /**
   * POST /rpc/stream — 流式 JSON-RPC 调用
   * 返回 async generator，每次 yield 一个解析后的 JSON 对象
   */
  async *requestStream(method, params = {}) {
    const body = JSON.stringify({ jsonrpc: '2.0', method, params, id: 1 });
    const response = await this._postRaw('/rpc/stream', body);

    let buffer = '';
    for await (const chunk of response) {
      buffer += chunk.toString();
      // 按 SSE 事件边界拆分
      const parts = buffer.split('\n\n');
      buffer = parts.pop(); // 保留未完成部分
      for (const part of parts) {
        const dataLine = part.split('\n').find((l) => l.startsWith('data: '));
        if (dataLine) {
          const json = dataLine.slice(6); // 去掉 'data: '
          try {
            yield JSON.parse(json);
          } catch {
            // 非 JSON 数据原样返回
            yield json;
          }
        }
      }
    }
  }

  // --- 内部方法 ---

  _get(path) {
    return new Promise((resolve, reject) => {
      const req = http.get(
        { hostname: WORKER_HOST, port: WORKER_PORT, path },
        (res) => {
          let data = '';
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              resolve(JSON.parse(data));
            } else {
              reject(new Error(`HTTP ${res.statusCode}: ${data}`));
            }
          });
        },
      );
      req.on('error', reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error('timeout')); });
    });
  }

  _post(path, body) {
    return new Promise((resolve, reject) => {
      const req = http.request(
        {
          hostname: WORKER_HOST,
          port: WORKER_PORT,
          path,
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        },
        (res) => {
          let data = '';
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              resolve(JSON.parse(data));
            } else {
              reject(new Error(`HTTP ${res.statusCode}: ${data}`));
            }
          });
        },
      );
      req.on('error', reject);
      req.write(body);
      req.end();
    });
  }

  _postRaw(path, body) {
    return new Promise((resolve, reject) => {
      const req = http.request(
        {
          hostname: WORKER_HOST,
          port: WORKER_PORT,
          path,
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        },
        (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(res);
          } else {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => reject(new Error(`HTTP ${res.statusCode}: ${data}`)));
          }
        },
      );
      req.on('error', reject);
      req.write(body);
      req.end();
    });
  }
}

module.exports = WorkerClient;
