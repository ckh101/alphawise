"""
Worker 入口

轻量 FastAPI，监听 127.0.0.1:9999。
启动时不做重计算初始化，handler 内部延迟 import。
启动时写 PID 文件，退出时删除（供 Node.js 端检测存活和清理僵尸进程）。
"""

import os
import sys

# === 编码强制 UTF-8（必须在所有其他 import / IO 之前）===
# Windows 默认 cp936(GBK)，会导致 LLM 返回中文、subprocess 输出、日志全部乱码。
# 这里统一强制 UTF-8，subprocess 子进程通过 PYTHONUTF8 继承。
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path

# 路径设置：与 main.py 保持一致
_backend_dir = Path(__file__).parent
_project_root = _backend_dir.parent if (_backend_dir.parent / "harness").is_dir() else _backend_dir
sys.path.insert(0, str(_project_root))

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

# PID 文件目录：优先用 HARNESS_LOG_DIR（Node.js 注入），否则项目 logs/
_PID_DIR = Path(os.environ.get("HARNESS_LOG_DIR") or (_project_root / "logs"))
_PID_DIR.mkdir(parents=True, exist_ok=True)
_PID_FILE = _PID_DIR / "worker.pid"


def _write_pid_file():
    """写入当前进程 PID 到 PID 文件"""
    try:
        _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _remove_pid_file():
    """删除 PID 文件"""
    try:
        if _PID_FILE.exists():
            _PID_FILE.unlink()
    except Exception:
        pass


app = FastAPI(title="Harness Worker")

_ready = False  # Agent 初始化完成后才设为 True


@app.on_event("startup")
async def startup():
    """Worker 启动时初始化 Agent 系统，注册所有技能"""
    global _ready
    _write_pid_file()
    from harness.agent import initialize_agent
    await initialize_agent()
    _ready = True


@app.on_event("shutdown")
async def shutdown():
    """退出时清理 PID 文件"""
    _remove_pid_file()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """功能就绪检查：Agent 初始化完成后才返回 ok"""
    if _ready:
        return {"status": "ok"}
    return {"status": "initializing"}


@app.post("/rpc")
async def rpc(request: dict):
    """JSON-RPC 入口"""
    from worker.rpc import dispatch
    method = request.get("method", "")
    params = request.get("params", {})
    result = await dispatch(method, params)
    return {"jsonrpc": "2.0", "result": result, "id": request.get("id")}


@app.post("/rpc/stream")
async def rpc_stream(request: dict):
    """JSON-RPC 流式入口，返回 SSE"""
    from worker.rpc import dispatch_stream
    method = request.get("method", "")
    params = request.get("params", {})

    async def event_generator():
        async for chunk in dispatch_stream(method, params):
            yield f"data: {chunk}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.close()
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="info")
