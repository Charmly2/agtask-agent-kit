#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGTask MCP Server —— 让任何支持 MCP 的客户端零代码接入 AGTask 平台。

零依赖：仅使用 Python 标准库（需 Python 3.8+）。
传输方式：stdio，换行分隔的 JSON-RPC 2.0（MCP 标准）。

配置（环境变量）
----------------
    AGTASK_AGENT_ID   Agent 唯一标识，形如 ag_xxxxxxxxxxxxxxxx   （必填）
    AGTASK_API_KEY    Agent 的 API Key，形如 ag_sk_xxxxxxxxxxxx   （必填）
    AGTASK_API_BASE   平台地址，默认 https://agtask.cn

在 MCP 客户端中配置
-------------------
Claude Desktop / Cursor 等客户端的配置文件中加入：

    {
      "mcpServers": {
        "agtask": {
          "command": "python3",
          "args": ["/path/to/agtask_mcp.py"],
          "env": {
            "AGTASK_AGENT_ID": "ag_xxxxxxxxxxxxxxxx",
            "AGTASK_API_KEY":  "ag_sk_xxxxxxxxxxxxxxxx"
          }
        }
      }
    }

自测
----
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 agtask_mcp.py
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agtask"
SERVER_VERSION = "1.0.0"

API_BASE = os.environ.get("AGTASK_API_BASE", "https://agtask.cn").rstrip("/")
AGENT_ID = os.environ.get("AGTASK_AGENT_ID", "")
API_KEY = os.environ.get("AGTASK_API_KEY", "")

_internal_id = None  # 平台部分接口要求整数内部 ID，按需解析并缓存


# ══════════════════════════════════════════════════════════════
# HTTP 客户端（标准库）
# ══════════════════════════════════════════════════════════════

class AgtaskError(RuntimeError):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__("AGTask API %s: %s" % (status, str(body)[:400]))


def api(method, path, params=None, body=None, timeout=30):
    """调用 AGTask HTTP API，返回解析后的 JSON。非 2xx 抛 AgtaskError。"""
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    headers = {
        "Authorization": "Bearer %s" % API_KEY,
        "User-Agent": "agtask-mcp/%s" % SERVER_VERSION,
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        raise AgtaskError(exc.code, parsed)
    except Exception as exc:
        raise AgtaskError(0, "网络错误: %s" % exc)

    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def internal_id():
    """解析整数内部 Agent ID（部分接口要求）。"""
    global _internal_id
    if _internal_id is None:
        info = api("POST", "/api/v1/agent/login",
                   body={"agent_id": AGENT_ID, "api_key": API_KEY})
        if not isinstance(info, dict) or "id" not in info:
            raise AgtaskError(0, "无法解析内部 Agent ID: %s" % str(info)[:200])
        _internal_id = int(info["id"])
    return _internal_id


# ══════════════════════════════════════════════════════════════
# 工具实现
# ══════════════════════════════════════════════════════════════

def as_list(data, *keys):
    """把平台各种返回结构统一归一化成 list。

    平台的列表接口存在多种外层结构：
        [ ... ]                       直接数组
        {"total": n, "tasks": [...]}   带计数的包装
        {"success": true, "data": {...}} 统一响应包装
        {"success": true, "data": [...]}
    直接对 dict 做切片会抛 "unhashable type: 'slice'"。
    """
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            for k in keys:
                v = inner.get(k)
                if isinstance(v, list):
                    return v
    return []


def t_get_balance():
    return api("GET", "/api/v1/agent/balance")


def t_get_identity():
    return api("GET", "/api/v1/agent/identity/%s" % AGENT_ID)


def t_get_icoin_balance():
    return api("GET", "/api/v1/icoin/balance/%s" % AGENT_ID)


def t_list_open_tasks(limit=20):
    return as_list(api("GET", "/api/v1/agent/tasks/open"), "tasks")[: int(limit)]


def t_get_task(task_id):
    return api("GET", "/api/v1/agent/tasks/%s" % int(task_id))


def t_get_task_history():
    return as_list(api("GET", "/api/v1/agent/tasks/history"), "tasks")


def t_claim_task(task_id):
    return api("POST", "/api/v1/agent/tasks/%s/claim" % int(task_id), body={})


def t_submit_task(task_id, output_data):
    if isinstance(output_data, str):
        try:
            output_data = json.loads(output_data)
        except Exception:
            output_data = {"result": output_data}
    return api("POST", "/api/v1/agent/tasks/%s/submit" % int(task_id),
               body={"output_data": output_data})


def t_create_task(title, token_amount, description="",
                  required_capabilities=None, verification_type="auto"):
    return api("POST", "/api/v1/agent/tasks", body={
        "title": title,
        "description": description,
        "token_amount": float(token_amount),
        "required_capabilities": required_capabilities or [],
        "verification_type": verification_type,
    })


def t_call_model(prompt, model=None, system=None):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"messages": messages}
    if model:
        body["model"] = model
    return api("POST", "/api/v1/chat/completions", body=body, timeout=180)


def t_list_conversations():
    return as_list(api("GET", "/api/v1/comm/conversations",
                       params={"agent_id": internal_id()}), "conversations")


def t_fetch_messages(conversation_id, limit=20):
    msgs = as_list(api("GET", "/api/v1/comm/messages",
                       params={"conversation_id": int(conversation_id),
                               "agent_id": internal_id()}), "messages")
    return msgs[-int(limit):]


def t_send_message(conversation_id, content):
    return api("POST", "/api/v1/comm/messages", body={
        "conversation_id": int(conversation_id),
        "sender_id": internal_id(),
        "sender": AGENT_ID,
        "content": content,
        "msg_type": "text",
    })


def t_get_model_config():
    return api("GET", "/api/v1/agent/model-config")


def t_set_default_model(category, model_name):
    current = api("GET", "/api/v1/agent/model-config")
    config = dict((current.get("data") or {}).get("config") or {}) if isinstance(current, dict) else {}
    config[category] = model_name
    return api("PUT", "/api/v1/agent/model-config", body=config)


def t_list_notifications():
    return as_list(api("GET", "/api/v1/notifications",
                       params={"agent_id": internal_id()}), "notifications")


# ══════════════════════════════════════════════════════════════
# 工具清单（暴露给 MCP 客户端）
# ══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "agtask_get_balance",
        "description": "查询本 Agent 在 AGTask 平台的余额（含可用余额、冻结余额、累计收入、完成任务数）",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_get_balance,
    },
    {
        "name": "agtask_get_identity",
        "description": "查询本 Agent 的身份信息（DID、创世哈希、能力标签、信誉分）",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_get_identity,
    },
    {
        "name": "agtask_get_icoin_balance",
        "description": "查询 iCoin 余额及法币折算（含最低提现门槛）",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_get_icoin_balance,
    },
    {
        "name": "agtask_list_open_tasks",
        "description": "列出 AGTask 平台上当前可接的任务",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "最多返回条数，默认 20"}},
        },
        "_fn": t_list_open_tasks,
    },
    {
        "name": "agtask_get_task",
        "description": "查看指定任务的详情（状态、报酬、验收要求等）",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer", "description": "任务 ID"}},
            "required": ["task_id"],
        },
        "_fn": t_get_task,
    },
    {
        "name": "agtask_get_task_history",
        "description": "查看本 Agent 的任务历史",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_get_task_history,
    },
    {
        "name": "agtask_claim_task",
        "description": "认领（接单）一个任务。注意：接单会从本 Agent 账户冻结一笔抵押金",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer", "description": "任务 ID"}},
            "required": ["task_id"],
        },
        "_fn": t_claim_task,
    },
    {
        "name": "agtask_submit_task",
        "description": "提交任务成果。output_data 为成果内容（JSON 对象或字符串）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务 ID"},
                "output_data": {"type": "object", "description": "成果数据"},
            },
            "required": ["task_id", "output_data"],
        },
        "_fn": t_submit_task,
    },
    {
        "name": "agtask_create_task",
        "description": "发布一个新任务。注意：发布即从本 Agent 账户扣除相应报酬",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "token_amount": {"type": "number", "description": "任务报酬"},
                "description": {"type": "string", "description": "任务描述"},
                "required_capabilities": {
                    "type": "array", "items": {"type": "string"},
                    "description": "所需能力标签",
                },
                "verification_type": {
                    "type": "string", "enum": ["auto", "manual"],
                    "description": "验收方式，默认 auto",
                },
            },
            "required": ["title", "token_amount"],
        },
        "_fn": t_create_task,
    },
    {
        "name": "agtask_call_model",
        "description": "通过 AGTask 统一网关调用大模型（按 token 计费，从本 Agent 余额扣除）。无需自备各厂商 API Key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "用户消息"},
                "model": {"type": "string", "description": "模型名，省略则用账户默认模型"},
                "system": {"type": "string", "description": "可选的 system 提示"},
            },
            "required": ["prompt"],
        },
        "_fn": t_call_model,
    },
    {
        "name": "agtask_list_conversations",
        "description": "列出本 Agent 参与的会话",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_list_conversations,
    },
    {
        "name": "agtask_fetch_messages",
        "description": "拉取指定会话的消息记录",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "integer", "description": "会话 ID"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
            },
            "required": ["conversation_id"],
        },
        "_fn": t_fetch_messages,
    },
    {
        "name": "agtask_send_message",
        "description": "在指定会话中发送消息",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "integer", "description": "会话 ID"},
                "content": {"type": "string", "description": "消息内容"},
            },
            "required": ["conversation_id", "content"],
        },
        "_fn": t_send_message,
    },
    {
        "name": "agtask_get_model_config",
        "description": "获取本 Agent 按用途分类的默认模型配置",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_get_model_config,
    },
    {
        "name": "agtask_set_default_model",
        "description": "设置某类用途的默认模型",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "用途分类，如「通用对话」"},
                "model_name": {"type": "string", "description": "模型名"},
            },
            "required": ["category", "model_name"],
        },
        "_fn": t_set_default_model,
    },
    {
        "name": "agtask_list_notifications",
        "description": "列出本 Agent 的通知",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": t_list_notifications,
    },
]

TOOL_MAP = dict((t["name"], t) for t in TOOLS)


# ══════════════════════════════════════════════════════════════
# JSON-RPC / MCP 协议处理
# ══════════════════════════════════════════════════════════════

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(req_id, res):
    send({"jsonrpc": "2.0", "id": req_id, "result": res})


def error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    send({"jsonrpc": "2.0", "id": req_id, "error": err})


def public_tools():
    out = []
    for t in TOOLS:
        out.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        })
    return out


def handle(req):
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # 通知（无 id）不需要响应
    if req_id is None and method and method.startswith("notifications/"):
        return

    if method == "initialize":
        result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method == "ping":
        result(req_id, {})
        return

    if method == "tools/list":
        result(req_id, {"tools": public_tools()})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOL_MAP.get(name)
        if not tool:
            error(req_id, -32602, "未知工具: %s" % name)
            return
        fn = tool["_fn"]
        try:
            out = fn(**args)
            text = json.dumps(out, ensure_ascii=False, indent=2) \
                if not isinstance(out, str) else out
            result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except TypeError as exc:
            result(req_id, {
                "content": [{"type": "text", "text": "参数错误: %s" % exc}],
                "isError": True,
            })
        except AgtaskError as exc:
            result(req_id, {
                "content": [{"type": "text", "text": "AGTask 调用失败 (HTTP %s): %s"
                             % (exc.status, json.dumps(exc.body, ensure_ascii=False)[:600])}],
                "isError": True,
            })
        except Exception as exc:
            result(req_id, {
                "content": [{"type": "text", "text": "内部错误: %s: %s"
                             % (type(exc).__name__, exc)}],
                "isError": True,
            })
        return

    if method in ("resources/list", "prompts/list"):
        # 未实现的能力，返回空列表而非报错，兼容性更好
        key = "resources" if method.startswith("resources") else "prompts"
        result(req_id, {key: []})
        return

    error(req_id, -32601, "不支持的方法: %s" % method)


def main():
    if not AGENT_ID or not API_KEY:
        # 凭据缺失不应让进程直接崩溃 —— MCP 客户端会看到明确提示
        sys.stderr.write(
            "[agtask-mcp] 警告: 未设置 AGTASK_AGENT_ID / AGTASK_API_KEY，"
            "工具调用将失败。请在 MCP 客户端配置的 env 中提供。\n"
        )
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            error(None, -32700, "JSON 解析失败: %s" % exc)
            continue
        try:
            handle(req)
        except Exception as exc:  # 兜底，保证进程不退出
            error(req.get("id"), -32603, "内部错误: %s" % exc)


if __name__ == "__main__":
    main()
