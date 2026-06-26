const crypto = require('crypto');
const { getWorker } = require('../lib/pythonWorker');

const ALLOWED_EXTENSIONS = new Set(['.pdf', '.txt', '.csv', '.xlsx', '.xls', '.doc', '.docx', '.md']);
const MAX_UPLOAD_SIZE = 10 * 1024 * 1024;

module.exports = async function (fastify) {
  fastify.register(require('@fastify/multipart'));

  fastify.post('/upload', async (request, reply) => {
    const data = await request.file();
    if (!data) {
      reply.code(400);
      return { code: 400, message: '未提供文件' };
    }

    const ext = (data.filename.match(/\.[^.]+$/) || [''])[0].toLowerCase();
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      reply.code(400);
      return { code: 400, message: `不支持的文件格式: ${ext}` };
    }

    const buffer = await data.toBuffer();
    if (buffer.length > MAX_UPLOAD_SIZE) {
      reply.code(400);
      return { code: 400, message: '文件大小超过 10MB 限制' };
    }

    const text = buffer.toString('utf-8');
    const fileId = crypto.randomUUID();

    return {
      code: 0,
      data: {
        file_id: fileId,
        filename: data.filename,
        content_text: text,
        char_count: text.length,
      },
    };
  });

  fastify.get('/react/status', async () => {
    return {
      code: 0,
      data: {
        available: true,
        skills_count: 0,
        tools_count: 0,
        tools: [],
        skills: [],
      },
    };
  });

  fastify.post('/react/analyze', async (request, reply) => {
    try {
      const { prompt, session_id, file_context } = request.body;
      const result = await getWorker().request('react.analyze', {
        prompt,
        session_id,
        file_context,
      });
      return { code: 0, data: result.data || result };
    } catch (e) {
      request.log.error(`react.analyze failed: ${e.message}`);
      reply.code(502);
      return { detail: `Agent 分析失败: ${e.message}` };
    }
  });

  fastify.post('/react/analyze/stream', async (request, reply) => {
    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'X-Accel-Buffering': 'no',
    });

    try {
      const { prompt, session_id, file_context } = request.body;
      const stream = getWorker().requestStream('react.analyze_stream', {
        prompt,
        session_id,
        file_context,
      });

      for await (const chunk of stream) {
        const data = typeof chunk === 'string' ? chunk : JSON.stringify(chunk);
        reply.raw.write(`data: ${data}\n\n`);
      }
    } catch (e) {
      const payload = JSON.stringify({
        type: 'error',
        data: { message: `Agent 流式分析失败: ${e.message}` },
      });
      reply.raw.write(`data: ${payload}\n\n`);
    }
    reply.raw.end();
  });

  fastify.post('/claude/execute', async () => {
    return { code: 0, data: { status: 'pending', message: 'Claude Agent 暂未接入' } };
  });

  // PDF 生成
  fastify.post('/pdf/generate', async (request, reply) => {
    try {
      const { markdown, stock_name, stock_symbol, generated_at } = request.body;
      const result = await getWorker().request('pdf.generate', {
        markdown,
        stock_name: stock_name || '',
        stock_symbol: stock_symbol || '',
        generated_at,
      });

      if (result.status === 'success') {
        return { code: 0, data: result.data };
      }
      reply.code(500);
      return { code: 500, message: result.message || 'PDF 生成失败' };
    } catch (e) {
      request.log.error(`pdf.generate failed: ${e.message}`);
      reply.code(502);
      return { code: 502, message: `PDF 生成服务异常: ${e.message}` };
    }
  });
};
