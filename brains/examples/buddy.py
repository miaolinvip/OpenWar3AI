# -*- coding: utf-8 -*-
"""玩伴示例：RPG 地图里给你配一个圣骑士伙伴 —— 跟着你、帮你打怪、你残血给你加血、陪你说话。

    python tools/play.py --bot brains/examples/buddy.py --inst 20 --rpg --map "<游戏目录>\\Maps\\Scenario\\(4)WarChasers.w3m"
    或者在远见「AI 方案」页选「玩伴示例」用到实例上（单人局）。

改成你自己的玩伴，照下面改属性就行（全部选项见 sdk/python/openwar3/companion.py 开头）：
  mode      ally 另一个盟友玩家（默认） / own 算在你名下（你也能指挥它） / adopt 接管地图给的随从 / voice 只说话
  unit      造什么：任何四字码，地图自定义的也行（g.map_data.name_of 能查名字）
  heal      (施法订单名, 要学的技能, 主人血量低于多少加血)；None 不加血
  talk      台词表 / 性格 / 本地大模型（设环境变量 OPENWAR3_LLM_URL，例如 LM Studio 的 http://127.0.0.1:1234/v1/chat/completions）
"""
import os

from openwar3.companion import Companion
from openwar3.talk import LocalLLM, Talk


def make_talk() -> Talk:
    persona = ("你是魔兽争霸 RPG 地图里陪玩家闯关的小圣骑士「小圣」，活泼、有点傻气、特别爱给主人加油，"
               "说话口语化、偶尔撒娇，绝不说教。")
    url = os.environ.get("OPENWAR3_LLM_URL")
    llm = LocalLLM(url, model=os.environ.get("OPENWAR3_LLM_MODEL", "")) if url else None
    t = Talk(persona=persona, llm=llm)
    t.lines["hello"] = ["小圣来报到！{master}，这回我陪你闯{map}～", "圣光在上，我会一直跟着你的！"]
    t.lines["healed"] = ["圣光与你同在！给你补血～", "嘿咻，加满加满！"]
    return t


class Buddy(Companion):
    name = "buddy"
    mode = "ally"
    unit = "Hpal"                 # 圣骑士
    nickname = "小圣"
    heal = ("holybolt", "AHhb", 0.55)
    skills = ("AHhb", "AHds", "AHad", "AHre")   # 圣光术 -> 神圣护盾 -> 虔诚光环 -> 复活

    def on_start(self, g):
        self.talk = make_talk()
        super().on_start(g)

    def on_poke(self, g):
        # 主人右键点我：满血就说句俏皮话，主人残血就先给他加血
        m = self._master(g)
        b = self.buddy_unit(g)
        if m is not None and b is not None and m.hp < 0.9 * m.hp_max and g.cooldown(b, "AHhb") == 0:
            self.cg.cast(b, "holybolt", target=m)
            self.speak("healed")
        else:
            self.speak("poke")
