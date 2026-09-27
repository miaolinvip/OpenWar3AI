# -*- coding: utf-8 -*-
"""玩伴说什么（09-24）：按事件挑固定台词；给了本地大模型就先问它（超时/出错退回固定台词）。

    talk = Talk(persona="活泼的小圣骑士，说话带点傻气，爱给主人加油")
    talk = Talk(llm=LocalLLM("http://127.0.0.1:1234/v1/chat/completions", model="qwen3"))   # LM Studio / Ollama 等 OpenAI 兼容接口
    talk.lines["hello"] = ["我来啦！"]          # 改台词：直接改这张表
    text = talk.line("master_low", master="剑圣")

事件（Companion 会在对应时刻问 talk.line）：
  hello 刚来 / poke 主人右键点了玩伴 / fight 开打 / kill 打死一个 / healed 给主人加了血 / master_low 主人残血
  master_levelup 主人升级 / master_died 主人倒下 / master_back 主人复活 / buddy_low 玩伴自己残血
  buddy_died 玩伴倒下 / buddy_back 玩伴回来 / idle 闲着没事聊两句 / item 主人捡到东西
台词里可以用 {master} {me} {map} {enemy} {level} {item} {respawn}，缺的变量留空。
"""
from __future__ import annotations

import json
import random
import threading
import time
import urllib.request

DEFAULT_LINES = {
    "hello": ["{me}来啦！{master}，这回我陪你闯{map}～", "嘿！我是{me}，跟着你一起冒险！", "别怕，有我在呢～"],
    "poke": ["在呢在呢！", "嗯？叫我吗～", "我一直跟着你呢！", "有什么吩咐？"],
    "fight": ["上！一起揍它！", "我来帮你！", "看我的！", "别让它跑了！"],
    "kill": ["干掉一个{enemy}！", "{enemy}，就这？", "漂亮！"],
    "healed": ["给你补一口血～", "圣光与你同在！", "撑住，我给你加血！"],
    "master_low": ["{master}小心！血不多了！", "别硬撑，往我这边退！", "快快快，先撤一下！"],
    "master_levelup": ["{level} 级啦！越来越厉害了～", "升级了！{level} 级的{master}！", "又变强了，好帅！"],
    "master_died": ["呜……{master}倒下了，我替你报仇！", "别灰心，一会儿就能回来！"],
    "master_back": ["你回来啦！想死你了～", "欢迎回来！我们继续！"],
    "buddy_low": ["我快撑不住了……先退一下！", "哎呀好疼，我躲一躲！"],
    "buddy_died": ["我先歇会儿……{respawn} 秒后回来找你！"],
    "buddy_back": ["我回来啦！", "满血复活！继续冲！"],
    "idle": ["这地方风景还不错～", "你玩得真好！", "累了就歇会儿，我在这守着。", "要不要去那边看看？", "今天也要加油哦！"],
    "item": ["捡到{item}啦！", "{item}！好东西～"],
    "cmd_follow": ["好嘞，跟上你！", "来了来了～"],
    "cmd_stay": ["收到，我在这儿守着！", "好，我原地待命～"],
    "cmd_cant": ["现在加不了血……技能还在冷却。", "这会儿帮不上，等我一下！"],
    "cmd_heal": ["给你加血！", "圣光，治愈他！"],
}
# 同一类话多久才说第二次（墙钟秒）；没写的按 20
DEFAULT_COOLDOWN = {"poke": 3, "fight": 25, "kill": 15, "healed": 12, "master_low": 15, "idle": 90, "item": 8,
                    "master_levelup": 2, "master_died": 5, "buddy_died": 5, "hello": 0,
                    "cmd_follow": 1, "cmd_stay": 1, "cmd_cant": 1, "cmd_heal": 1}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


class LocalLLM:
    """OpenAI 兼容的聊天接口（LM Studio 默认 http://127.0.0.1:1234/v1/chat/completions，Ollama 是 :11434/v1/chat/completions）。
    只在本机：请求里带的是游戏里的事情（主人叫什么、打了什么怪），不外发。"""

    def __init__(self, url: str = "http://127.0.0.1:1234/v1/chat/completions", model: str = "", timeout: float = 8.0,
                 max_chars: int = 28):
        self.url, self.model, self.timeout, self.max_chars = url, model, timeout, max_chars

    def reply(self, persona: str, event: str, ctx: dict, fallback: str) -> str | None:
        want = (f"现在发生的事：{event}（{json.dumps({k: v for k, v in ctx.items() if v}, ensure_ascii=False)}）。"
                f"参考说法：「{fallback}」。用你的口吻说一句，{self.max_chars} 个字以内，只输出这句话。")
        body = {"messages": [{"role": "system", "content": persona}, {"role": "user", "content": want}],
                "temperature": 0.9, "max_tokens": 80, "stream": False}
        if self.model:
            body["model"] = self.model
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as fh:
            data = json.loads(fh.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if "</think>" in text:                               # 推理模型把思考过程也吐出来了
            text = text.split("</think>")[-1]
        text = text.strip().strip("「」\"'").splitlines()[0] if text.strip() else ""
        return text[: self.max_chars * 2] or None


class Talk:
    def __init__(self, lines: dict | None = None, persona: str = "", llm: LocalLLM | None = None,
                 cooldown: dict | None = None, min_gap: float = 5.0):
        self.lines = {k: list(v) for k, v in DEFAULT_LINES.items()}
        self.lines.update(lines or {})
        self.persona = persona or "你是魔兽争霸里陪玩家闯关的小伙伴，活泼、贴心、爱鼓励人，说话口语化。"
        self.llm = llm
        self.cooldown = dict(DEFAULT_COOLDOWN, **(cooldown or {}))
        self.min_gap = min_gap
        self._last: dict = {}
        self._last_any = 0.0
        self._pending: list = []          # 大模型答回来的（等主循环取走）
        self._lock = threading.Lock()
        self._busy = False

    def ready(self, event: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        # 主人叫它 / 给它下命令的回话不能被「全局间隔」吞掉（09-24 实测：-stay 之后 3 秒打 -heal，回话被吞了）
        urgent = event in ("master_low", "master_died", "buddy_died", "poke", "hello") or event.startswith("cmd_")
        if not urgent and now - self._last_any < self.min_gap:
            return False
        return now - self._last.get(event, -1e9) >= self.cooldown.get(event, 20)

    def line(self, event: str, now: float | None = None, **ctx) -> str | None:
        """该说话就返回一句（固定台词），同时（有大模型的话）后台去问一句更好的，问回来从 take() 取。不该说返回 None。"""
        now = time.monotonic() if now is None else now
        if not self.ready(event, now) or not self.lines.get(event):
            return None
        self._last[event] = self._last_any = now
        text = random.choice(self.lines[event]).format_map(_SafeDict(ctx))
        if self.llm is not None and not self._busy:
            self._busy = True
            threading.Thread(target=self._ask, args=(event, ctx, text), daemon=True).start()
            return None                   # 先等大模型；它失败了 _ask 会把固定台词放进来
        return text

    def _ask(self, event: str, ctx: dict, fallback: str) -> None:
        try:
            text = self.llm.reply(self.persona, event, ctx, fallback) or fallback
        except Exception:  # noqa: BLE001 - 模型没开 / 超时：用固定台词
            text = fallback
        with self._lock:
            self._pending.append(text)
        self._busy = False

    def take(self) -> list[str]:
        """后台大模型答回来的话（主循环每拍取一次）。"""
        with self._lock:
            out, self._pending = self._pending, []
        return out
