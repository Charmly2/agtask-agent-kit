#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGTask Webhook 接收端 —— 验证「Agent 自主参与」闭环

这是一个最小可用的自主 Agent：
  1. 暴露 /agtask-webhook 接收平台推送
  2. 收到 task.assigned 时，自动走模型网关干活并提交成果
  3. 暴露 /received 便于外部检查收到了哪些事件

    python3 webhook_receiver.py [port]
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
API = os.environ.get("AGTASK_API_BASE", "http://127.0.0.1:8000")
AGENT_ID = os.environ.get("AGTASK_AGENT_ID", "")
API_KEY = os.environ.get("AGTASK_API_KEY", "")

RECEIVED = []          # 收到的事件
ACTIONS = []           # 自主执行的动作
LOCK = threading.Lock()


def api(method, path, body=None, timeout=180):
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = "Bearer " + API_KEY
    d = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=d, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            raw = x.read().decode()
            return x.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def autonomous_work(task_id):
    """被唤醒后自主完成：查详情 → 调模型 → 提交。"""
    log = {"task_id": task_id, "steps": []}
    try:
        c, t = api("GET", "/api/v1/agent/tasks/%s" % task_id)
        td = (t or {}).get("data") or t or {}
        log["steps"].append("查询任务: HTTP %s status=%s" % (c, td.get("status")))
        if str(td.get("status")) in ("passed", "submitted"):
            log["steps"].append("已完成，跳过")
            return log

        c, llm = api("POST", "/api/v1/chat/completions", {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是自主执行任务的 Agent，回答简短。"},
                {"role": "user", "content": "任务标题：%s。用一句话说明你已完成。"
                                            % td.get("title")},
            ]})
        if c != 200:
            log["steps"].append("模型调用失败 HTTP %s: %s" % (c, str(llm)[:120]))
            return log
        content = llm["choices"][0]["message"]["content"]
        log["steps"].append("模型产出: %s" % content[:80])
        log["output"] = content

        c, r = api("POST", "/api/v1/agent/tasks/%s/submit" % task_id,
                   {"result_url": "https://example.com/auto.txt",
                    "output_data": {"content": content}})
        log["steps"].append("提交: HTTP %s %s" % (c, json.dumps(r, ensure_ascii=False)[:120]))
    except Exception as e:
        log["steps"].append("异常: %s" % e)
    return log


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/received"):
            with LOCK:
                self._send(200, {"events": RECEIVED, "actions": ACTIONS,
                                 "count": len(RECEIVED)})
        elif self.path.startswith("/health"):
            self._send(200, {"status": "ok", "agent_id": AGENT_ID})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else "{}"
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw[:500]}

        event = payload.get("event") or payload.get("event_type") or "unknown"
        with LOCK:
            RECEIVED.append({"at": time.strftime("%H:%M:%S"), "event": event,
                             "payload": payload})
        print("[webhook] 收到事件: %s  载荷=%s" % (event, json.dumps(payload, ensure_ascii=False)[:200]),
              flush=True)

        # 先应答再干活 —— 顺序很关键：
        # 若先派生工作线程再应答，单线程 HTTPServer 下平台侧会读到
        # "upstream prematurely closed connection"（nginx 502）。
        self._send(200, {"status": "ok", "event": event, "handled": True})

        if event == "task.assigned":
            tid = payload.get("task_id") or (payload.get("task") or {}).get("id")
            if tid:
                threading.Thread(target=lambda: self._work(tid), daemon=True).start()

    def _work(self, tid):
        log = autonomous_work(tid)
        with LOCK:
            ACTIONS.append(log)
        print("[webhook] 自主执行完成: %s" % json.dumps(log, ensure_ascii=False)[:300],
              flush=True)

    def log_message(self, *a):
        pass


def main():
    print("AGTask Webhook 接收端启动  port=%s" % PORT, flush=True)
    print("  Agent: %s" % (AGENT_ID or "(未配置)"), flush=True)
    print("  回调地址: http://127.0.0.1:%s/agtask-webhook" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
