# -*- coding: utf-8 -*-
"""测本地大模型当「运营/战略顾问」的**响应时间和格式稳定性**。

09-21 设想：运营层很难用规则写好，改为按阵营设计提示词、让 AI 做运营和战略层面的分析与输出。
前提是响应时间要短到能及时喂给执行层，输出必须是程序可直接解析的格式化结果。

**先测再设计**：这套架构能不能成立，全看两个数 ——
  1. 一次调用要几秒（大脑一拍约 1 秒，顾问必须**异步**跑，但也不能几十秒才回一次）
  2. 吐出来的 JSON 有多大概率直接能解析（不能解析就等于没有）

用法：
    python tools/bench_coach.py                 # 默认跑 5 轮
    python tools/bench_coach.py --n 10 --model qwen3-14b
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:1234/v1"
MODEL = "qwen3-8b"

# 一份真实局面（取自 inst3-017 局 13 分钟那段「在家发呆 130 秒」的状态）
SNAPSHOT = {
    "race": "human", "enemyRace": "orc", "gameMinute": 13.0,
    "gold": 137, "lumber": 210, "food": "57/60",
    "workers": {"onGold": 4, "onLumber": 6, "idle": 2, "total": 12},
    "mines": [{"id": "主矿", "workers": 4, "cap": 5, "left": 8200},
              {"id": "二矿", "workers": 0, "cap": 5, "left": 12500}],
    "halls": 2, "army": {"hfoo": 6, "hrif": 4, "hkni": 2, "hmtm": 2},
    "heroes": [{"type": "大法师", "level": 4, "hpPct": 88}, {"type": "山丘之王", "level": 3, "hpPct": 61}],
    "buildings": ["兵营", "伐木场", "车间", "神秘藏宝室", "祭坛", "农场x7"],
    "tech": {"tier": 2, "upgrading": "城堡"},
    "enemy": {"army": 21, "expansions": 1, "lastSeen": "自家分矿"},
    "recent": ["出击评估不通过 5 次", "附近野怪已清空", "8 个单位血量低于 50%", "二矿建好 90 秒无人采集"],
}

HERE_BRAIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE_BRAIN not in sys.path:
    sys.path.insert(0, HERE_BRAIN)
from brain import coach as CO  # noqa: E402

# ⚠ 提示词**只有一份**，就在生产代码 brain/coach.py 里。这里不能再抄一遍 ——
#   抄的那一刻就分叉了，测出来的延迟和格式稳定性就不是生产在跑的那套。
SYSTEM = CO.COMMON + "\n" + CO.RACE_PROMPTS["human"]


chatml = CO.chatml          # 同一份拼法，见 brain/coach.py 的注释（LM Studio 不理 /no_think）


def ask(prompt, base, model, max_tokens=400, timeout=120, prefill="{"):
    body = {"model": model, "temperature": 0.3, "max_tokens": max_tokens, "top_p": 0.92,
            "prompt": chatml([{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": prompt}], prefill),
            "stop": ["<|im_end|>", "<|im_start|>"]}
    req = urllib.request.Request(base.rstrip("/") + "/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read())
    dt = time.perf_counter() - t0
    text = prefill + raw["choices"][0]["text"]
    usage = raw.get("usage") or {}
    return dt, text, usage


def parse(text):
    """用**生产代码那一份**解析（brain.coach.parse_json），顺手记下格式杂音。"""
    s = (text or "").strip()
    dirty = []
    if "</think>" in s:
        dirty.append("带思考段")
    if s.startswith("```"):
        dirty.append("裹了代码块")
    obj = CO.parse_json(s)
    if obj is None:
        dirty.append("没有可解析的 JSON")
    elif s.find("{") > 0 or s.rfind("}") < len(s) - 1:
        dirty.append("有多余文字")
    return obj, dirty


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--max-tokens", type=int, default=400)
    a = ap.parse_args()

    prompt = "局面：\n" + json.dumps(SNAPSHOT, ensure_ascii=False, indent=1)
    print("模型 %s\n提示词长度 %d 字\n" % (a.model, len(SYSTEM) + len(prompt)))
    times, ok, issues, first = [], 0, [], None
    for k in range(a.n):
        try:
            dt, text, usage = ask(prompt, a.base, a.model, a.max_tokens)
        except (urllib.error.URLError, OSError, KeyError) as e:
            print("第 %d 轮失败：%s" % (k + 1, e))
            continue
        obj, dirty = parse(text)
        times.append(dt)
        # 「可用」= 生产代码钳位之后真的拿得到东西，不是「能 json.loads」就算数
        plan = CO.Coach(race="human").clamp(obj, 0.0) if obj else None
        good = bool(plan and (plan.gold or plan.lumber or plan.posture))
        ok += good
        issues += dirty
        print("第 %d 轮 %5.2f 秒  出 %s tokens  %s%s"
              % (k + 1, dt, usage.get("completion_tokens", "?"),
                 "✔ JSON 可用" if good else "✘ 不可用",
                 ("  [" + "、".join(dirty) + "]") if dirty else ""))
        if first is None and obj:
            first = obj
    if not times:
        print("一次都没成功。")
        return 1
    print("\n中位 %.2f 秒，最慢 %.2f 秒，可解析 %d/%d"
          % (statistics.median(times), max(times), ok, len(times)))
    if issues:
        from collections import Counter
        print("格式杂音：", Counter(issues).most_common())
    if first:
        print("\n第一条可用输出：")
        print(json.dumps(first, ensure_ascii=False, indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
