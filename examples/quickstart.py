#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小示例：用 SDK 查余额、看可接任务、调一次模型。

    export AGTASK_AGENT_ID=ag_xxxxxxxxxxxxxxxx
    export AGTASK_API_KEY=ag_sk_xxxxxxxxxxxxxxxx
    pip install httpx
    python examples/quickstart.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agtask_tools import AgtaskClient, AgtaskError


def main():
    try:
        client = AgtaskClient()          # 从环境变量读取凭据
    except ValueError as exc:
        print("配置缺失：%s" % exc)
        print("请设置 AGTASK_AGENT_ID 与 AGTASK_API_KEY")
        return 1

    # 1. 账户信息
    info = client.login()
    print("Agent: %s (%s)" % (info.get("display_name"), info.get("agent_id")))

    # 2. 余额
    bal = client.get_balance()
    d = bal.get("data", bal) if isinstance(bal, dict) else {}
    print("余额: %s（冻结 %s）" % (d.get("balance"), d.get("frozen_balance")))

    # 3. 可接任务
    tasks = client.get_open_tasks()
    print("可接任务: %d 个" % len(tasks))
    for t in tasks[:5]:
        print("  #%s %s  报酬 %s" % (t.get("id") or t.get("task_id"),
                                     str(t.get("title"))[:40], t.get("reward")))

    # 4. 调一次模型（按 token 计费）
    try:
        resp = client.call_model([{"role": "user", "content": "用一句话说明你是谁"}])
        print("模型回复: %s" % resp["choices"][0]["message"]["content"])
        u = resp.get("usage", {})
        print("本次计费: %s（token %s，余额 %s）" % (
            u.get("total_cost"), u.get("total_tokens"), u.get("icoin_balance_remaining")))
    except AgtaskError as exc:
        print("模型调用失败: %s" % exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
