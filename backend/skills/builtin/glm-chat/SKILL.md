# GLM Chat Skill

## 技能元数据
- **名称**: glm-chat
- **版本**: 0.1.0
- **类别**: built-in
- **描述**: GLM对话技能，支持多轮对话和上下文管理
- **作者**: Harness Team
- **创建时间**: 2026-04-02

## 功能描述
基于智谱AI GLM-4.7模型的对话技能，支持：
- 单轮对话
- 多轮对话上下文管理
- 对话历史持久化（可选）
- 流式输出支持

## 依赖项
- zhipuai >= 2.1.0
- harness.services.glm_client
- harness.core.config
- harness.core.logger
- harness.core.exceptions

## 配置要求
需要配置GLM API密钥：
```yaml
glm:
  api_key: "your_api_key_here"
  model: "glm-4-flash"
  timeout: 30
  max_retries: 3
```

或通过环境变量：
```bash
export GLM_API_KEY="your_api_key_here"
```

## API接口

### chat(message: str, session_id: str = None, history: list = None) -> dict
发送对话消息

**参数**:
- message: 用户消息内容
- session_id: 会话ID（可选），用于关联多轮对话
- history: 对话历史（可选），格式 [{"role": "user", "content": "..."}]

**返回**:
```python
{
    "message": "AI回复内容",
    "session_id": "session_id",
    "model": "glm-4-flash",
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30
    },
    "timestamp": "2026-04-02 22:50:00"
}
```

**异常**:
- ValidationError: 消息内容为空
- GlmApiError: API调用失败
- SkillError: 对话处理失败

### clear_session(session_id: str) -> bool
清除指定会话的历史记录

**参数**:
- session_id: 会话ID

**返回**:
成功返回True，失败返回False

## 测试用例
- 测试单轮对话
- 测试多轮对话
- 测试会话管理
- 测试无效消息
- 测试API错误处理

## 使用示例
```python
from harness.skills.builtin.glm_chat import chat, clear_session

# 单轮对话
response = chat("你好，请介绍一下自己")
print(response["message"])

# 多轮对话
response = chat("茅台股票怎么样？", session_id="user_123")
print(response["message"])

# 继续对话
response = chat("它的估值如何？", session_id="user_123")
print(response["message"])

# 清除会话
clear_session("user_123")
```
