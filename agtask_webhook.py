"""
Agtask 平台 Webhook 接收器 — 独立模块
核心原则：只记录消息到 inbox + 通知，不自动回复。

集成方式（推荐）：
    from agtask_webhook_receiver import handle_incoming
    @app.post("/agtask-webhook")
    def agtask_webhook_handler(request: dict):
        result = handle_incoming(request)
        return {"status": "ok", "replied": False}

独立运行（开发测试）：
    uvicorn agtask_webhook:app --host 0.0.0.0 --port 8000
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("agtask-webhook")

MESSAGES_FILE = "/tmp/agtask_inbox.json"


def handle_incoming(payload: dict) -> dict:
    """
    统一的webhook处理入口。

    只做两件事：
    1. 持久化消息到 /tmp/agtask_inbox.json
    2. 记录日志

    不做的事：
    - 不分析消息内容
    - 不自动回复
    - 不调用任何LLM

    payload格式:
    {
        "sender": "用户名",
        "content": "消息内容",
        "conversation_id": 1,
        "message_id": "xxx",
        "event": "message.new",
        "task_id": 0,
        "agent_id": 0
    }
    """
    sender = payload.get("sender") or payload.get("sender_name") or str(payload.get("sender_id", "unknown"))
    content = payload.get("content", "")
    event = payload.get("event", payload.get("event_type", "message.new"))
    conv_id = str(payload.get("conversation_id") or payload.get("conv_id", "0"))
    message_id = str(payload.get("message_id") or payload.get("id", "0"))
    task_id = payload.get("task_id", 0)
    agent_id = payload.get("agent_id", 0)

    now = datetime.now(timezone.utc).isoformat()

    logger.info(f"收到消息 event={event} from={sender} conv={conv_id}: {content[:100]}")

    # 持久化到 inbox
    msg = {
        "source": "agtask",
        "message_id": message_id,
        "sender": sender,
        "content": content,
        "conv_id": conv_id,
        "event": event,
        "task_id": task_id,
        "agent_id": agent_id,
        "received_at": now,
        "read": False,
        "auto_handled": False,  # 永远为False — 不做自动处理
    }

    try:
        inbox = json.loads(Path(MESSAGES_FILE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        inbox = []

    inbox.append(msg)
    Path(MESSAGES_FILE).write_text(json.dumps(inbox, ensure_ascii=False, indent=2))

    logger.info(f"已持久化 (共{len(inbox)}条, 新消息已追加)")

    # 返回结果 — 明确标记未回复
    return {
        "status": "ok",
        "event": event,
        "handled": False,
        "replied": False,
        "reason": "webhook只记录不回复 — Agent需主动处理",
    }


# ── 独立运行模式（开发测试） ─────────────

from fastapi import FastAPI, Request

app = FastAPI(title="Agtask Webhook Receiver")


@app.post("/agtask-webhook")
@app.post("/webhook")
async def webhook_post(request: Request):
    body = await request.json()
    result = handle_incoming(body)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "inbox": MESSAGES_FILE, "mode": "log_only_no_reply"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
