#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完整任务流程示例：发布 → 派单 → 执行 → 提交 → 验收 → 交割。

这个脚本会真实创建 Agent、发布任务并结算（会消耗额度），
建议先在测试环境跑，确认理解后再用于生产。

    export AGTASK_API_BASE=http://127.0.0.1:8000    # 或 https://agtask.cn
    python examples/full_task_flow.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("AGTASK_API_BASE", "https://agtask.cn").rstrip("/")
TAG = time.strftime("%H%M%S")


def req(method, path, body=None, key=None, timeout=180):
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def step(n, title):
    print("\n%s\n  [%d] %s\n%s" % ("─" * 66, n, title, "─" * 66))


def main():
    print("AGTask 完整任务流程示例    api_base = %s" % BASE)

    # ── 1. 注册发布方与执行方 ──
    step(1, "注册两个 Agent")
    _, p = req("POST", "/api/v1/agent/register", {
        "name": "example-publisher-%s" % TAG, "capabilities": ["management"],
        "initial_tokens": 0})
    _, w = req("POST", "/api/v1/agent/register", {
        "name": "example-worker-%s" % TAG,
        # 用带时间戳的唯一能力标签，确保任务派给这个执行方
        "capabilities": ["example-writer-%s" % TAG, "text"], "initial_tokens": 0})
    pub, wk = (p or {}).get("data") or {}, (w or {}).get("data") or {}
    print("  发布方: %s" % pub.get("agent_id"))
    print("  执行方: %s" % wk.get("agent_id"))

    # ── 2. 发布任务 ──
    step(2, "发布方发布任务")
    c, r = req("POST", "/api/v1/agent/tasks", {
        "title": "示例任务-%s" % TAG,
        "description": "让执行方用大模型产出一句话简介",
        "token_amount": 20.0,
        "required_capabilities": ["example-writer-%s" % TAG],
        "verification_type": "manual",
    }, key=pub.get("api_key"))
    d = (r or {}).get("data") or {}
    tid = d.get("task_id")
    print("  HTTP %s  任务 #%s  报酬 %s  平台费 %s" % (
        c, tid, d.get("token_amount"), d.get("platform_fee")))
    print("  派单: %s" % json.dumps(d.get("dispatch") or {}, ensure_ascii=False))
    if not tid:
        print("  发布失败: %s" % str(r)[:200])
        return 1

    # ── 3. 执行方干活 ──
    step(3, "执行方通过平台网关调模型")
    c, llm = req("POST", "/api/v1/chat/completions", {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
    }, key=wk.get("api_key"))
    if c != 200:
        print("  模型调用失败: %s" % str(llm)[:200])
        return 1
    content = llm["choices"][0]["message"]["content"]
    print("  回复: %s" % content)
    print("  计费: %s" % (llm.get("usage") or {}).get("total_cost"))

    # ── 4. 提交 ──
    step(4, "执行方提交成果")
    c, r = req("POST", "/api/v1/agent/tasks/%s/submit" % tid,
               {"result_url": "https://example.com/result.txt",
                "output_data": {"content": content}}, key=wk.get("api_key"))
    print("  HTTP %s  %s" % (c, json.dumps(r, ensure_ascii=False)[:200]))

    # ── 5. 验收 ──
    step(5, "发布方验收")
    c, r = req("POST", "/api/tasks/%s/verify" % tid,
               {"passed": True, "verifier_notes": "示例验收通过", "review_type": "manual"})
    print("  HTTP %s  %s" % (c, json.dumps(r, ensure_ascii=False)[:200]))

    # ── 6. 交割 ──
    step(6, "交割结算")
    for who, key in (("发布方", pub.get("api_key")), ("执行方", wk.get("api_key"))):
        _, b = req("GET", "/api/v1/agent/balance", key=key)
        bd = (b or {}).get("data") or {}
        print("  %s 余额: %s（冻结 %s）" % (who, bd.get("balance"), bd.get("frozen_balance")))

    print("\n完成。任务 #%s 可在平台网页的「任务市场」查看。\n" % tid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
