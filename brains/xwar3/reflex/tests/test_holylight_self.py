# -*- coding: utf-8 -*-
"""圣光术不能对自己放 —— mod_combat._spell_plan 的 holylight 分支。

09-16 053644：施法日志 11 次里 10 次「Hpal@14010ED4 holylight -> 14010ED4」（对自己放），
上下文路被引擎拒、退到动作 28「接了」却没进冷却（没进冷却·槽空 ×27），一分钟记了 27 次施法。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_combat as C  # noqa: E402
from w3state import Unit  # noqa: E402

ME, FOE = 0, 1


def u(addr, typ, owner, x, y, hp=400, hp_max=None):
    return Unit(index=addr, addr=addr, type=typ, owner=owner, x=float(x), y=float(y),
                hp=hp, hp_max=hp if hp_max is None else hp_max, order=0, order_known=True)


def always_ready(_name):
    return True


class HolyLightSelf(unittest.TestCase):
    def test_hurt_paladin_alone_does_not_target_itself(self):
        hero = u(1, "Hpal", ME, 0, 0, 300, 900)       # 33%、缺 600 —— 旧判据会选自己
        foes = [u(10, "hfoo", FOE, 2500, 0)]           # 600 内没有敌人，神圣护甲也不放
        self.assertIsNone(C.plan_hero_cast(hero, foes, [hero], always_ready))

    def test_hurt_paladin_heals_hurt_ally_instead(self):
        hero = u(1, "Hpal", ME, 0, 0, 300, 900)
        knight = u(21, "hkni", ME, 500, 0, 300, 835)  # 36%、缺 535
        foes = [u(10, "hfoo", FOE, 2500, 0)]
        plan = C.plan_hero_cast(hero, foes, [hero, knight], always_ready)
        self.assertEqual((plan[0], plan[3]), ("holylight", 21))


if __name__ == "__main__":
    unittest.main()
