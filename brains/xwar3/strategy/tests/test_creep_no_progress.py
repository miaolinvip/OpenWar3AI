# -*- coding: utf-8 -*-
"""打野打不到就换营地（09-21 3-007 局 13 分钟左右，两个英雄卡在左下角，在移动和停止之间来回切换）。

真实失败形状（inst3 第 007 局，kernel a03c23b、2.00 倍速）：
713~914 游戏秒共 **201 秒、78 条令**，反复下同一条
`打野开打（营地强度 17，到场战力比 99.0；核心伤害 7.8/秒）：N/8 个 -> (-6667,-3705)`，
同期让路日志 `Hmkg 卡了 42 游戏秒没挪窝（离目标 658）` —— 目标够不着（地形挡着），营地血一点没掉。

**为什么原来永远不放弃**：唯一的放弃闸 `CREEP_ABORT_POWER` 按**战力比**判，
而那一刻战力比是 99.0（上限），看起来碾压。它回答的是「打不打得过」，
这里的问题是「够不够得着」—— 判据在量一个和问题无关的东西。

所以判据必须是**进展**：营地总血有没有在掉。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain import tactics as T  # noqa: E402
from brain.tables.human import PROFILE  # noqa: E402
from brain.world import build_world  # noqa: E402
from test_runtime_smoke import U  # noqa: E402

CAMP = (-6667.0, -3705.0)
HOME = (-5184.0, 2944.0)


def scene(t, creep_hp, gap=700.0):
    """全军围着一个够不着的营地：我方压倒性优势（战力比会顶到上限），但野怪血不掉。
    gap = 部队离营地多远；超过 CREEP_ENGAGE(900) 就还没开打，用来测「在路上不算没进展」。"""
    units = [U(1, "htow", *HOME), U(900, "unpl", 6000, 6000, owner=1)]
    units += [U(2, "Hamg", CAMP[0] + gap, CAMP[1]), U(3, "Hmkg", CAMP[0] + gap + 50, CAMP[1] + 60)]
    units += [U(10 + i, "hrif", CAMP[0] + gap + 100, CAMP[1] + 80 * i) for i in range(8)]
    units += [U(300 + i, "hpea", HOME[0], HOME[1]) for i in range(5)]
    for i, hp in enumerate(creep_hp):
        c = U(700 + i, "nogm", CAMP[0], CAMP[1] + 40 * i, owner=12)
        c.hp = hp
        units.append(c)
    return build_world(units, 0, float(t), 500, 300, 40, 60, levels={2: 3, 3: 3})


def engaged(w, hp):
    s = T.AttackSequence(dict(PROFILE))
    s.camp_ids = {700 + i for i in range(len(hp))}
    s.camp = dict(x=CAMP[0], y=CAMP[1], strength=17.0, units=[])
    s.state, s.creep_since, s.creep_engaged = "creep", w.t, True
    return s


def drive(seq, hps, t0=700.0, step=2.0, gap=700.0):
    """一拍一拍喂进去 —— 「没进展」只有连续几拍才看得出来，静态快照测不出来。
    返回**所有拍**的意图：放弃发生在中间某一拍，之后就进收队状态了，只看最后一拍会漏掉。"""
    out = []
    for i, hp in enumerate(hps):
        w = scene(t0 + i * step, hp, gap)
        out += seq.step(w, w.my_halls[0])
    return out


class NoProgressAbandons(unittest.TestCase):
    def test_gives_up_when_camp_hp_never_drops(self):
        """血一点不掉，撑过 CREEP_NO_PROGRESS_S 就换营地。"""
        full = [400.0] * 4
        n = int(T.CREEP_NO_PROGRESS_S / 2.0) + 3      # 期望按常量算出来，不是拍脑袋
        seq = engaged(scene(700.0, full), full)
        out = drive(seq, [full] * n)
        self.assertEqual(seq.counts.get("打野·够不着·放弃营地"), 1, seq.counts)
        self.assertTrue(any("血一直没掉" in (i.why or "") for i in out), [i.why for i in out])
        self.assertEqual(seq.state, "retreat")
        # 放弃的营地要进黑名单，否则下一拍又选回来 —— 那就成了 1-015 那种撤退循环
        self.assertTrue(any(abs(sx - CAMP[0]) < 1 and abs(sy - CAMP[1]) < 1 for sx, sy, _ in seq.skip_camps))

    def test_does_not_give_up_while_making_progress(self):
        """在掉血就不能放弃 —— 否则一场正常的打野会被这条闸打断。"""
        n = int(T.CREEP_NO_PROGRESS_S / 2.0) + 6
        hps = [[400.0 - i * 5.0] * 4 for i in range(n)]      # 每拍掉 5 血
        seq = engaged(scene(700.0, hps[0]), hps[0])
        drive(seq, hps)
        self.assertIsNone(seq.counts.get("打野·够不着·放弃营地"), seq.counts)
        self.assertEqual(seq.state, "creep")

    def test_slow_but_real_progress_resets_the_clock(self):
        """掉得慢也是进展：前面耗掉大半时间，掉一次血就该重新计时，不能累计判死。"""
        hold = int(T.CREEP_NO_PROGRESS_S / 2.0) - 1
        hps = [[400.0] * 4] * hold + [[390.0] * 4] + [[390.0] * 4] * hold
        seq = engaged(scene(700.0, hps[0]), hps[0])
        drive(seq, hps)
        self.assertIsNone(seq.counts.get("打野·够不着·放弃营地"), seq.counts)

    def test_not_engaged_yet_is_not_counted(self):
        """还在走路过去（没开打）不算没进展 —— 否则远营地永远到不了。
        部队放在 CREEP_ENGAGE(900) 之外，这一路上血当然不会掉。"""
        full = [400.0] * 4
        n = int(T.CREEP_NO_PROGRESS_S / 2.0) + 3
        far = 3000.0
        seq = engaged(scene(700.0, full, far), full)
        seq.creep_engaged = False
        drive(seq, [full] * n, gap=far)
        self.assertFalse(seq.creep_engaged, "这个场景不该开打，否则测的不是「在路上」")
        self.assertIsNone(seq.counts.get("打野·够不着·放弃营地"), seq.counts)

    def test_progress_state_is_recorded_once_engaged(self):
        full = [400.0] * 4
        seq = engaged(scene(700.0, full), full)
        drive(seq, [full] * 3)
        self.assertIsNotNone(seq.camp_hp_seen, "开打后应该记下营地血量的基准")


class ResetStaysWithTheOtherCampState(unittest.TestCase):
    """换营地时 camp_hp_seen 必须跟着清，否则新营地会继承上一个的记录、一开打就被判死。

    这是**静态**检查：打野换营地的状态重置写在三个地方（清分矿 / 选新营地 / 清营后前移），
    以后加第四处时很容易漏掉一个 —— 那正是「同一条规则写在多处，
    只改一处等于没改」。断言行为的测试抓不到「第四处」，只有对着源码数才行。"""

    def test_every_creep_state_reset_also_clears_camp_hp_seen(self):
        import re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain", "tactics.py")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        anchors = [i for i, l in enumerate(lines)
                   if re.search(r"self\.creep_since,\s*self\.creep_engaged,\s*self\.creep_focus_id\s*=", l)]
        self.assertGreaterEqual(len(anchors), 3, "找不到打野状态重置点，正则要跟着改")
        missing = [i + 1 for i in anchors
                   if not re.search(r"self\.camp_hp_seen\s*=\s*None", lines[i + 1])]
        self.assertFalse(missing, f"这些行重置了打野状态却没清 camp_hp_seen（源码行号）：{missing}")


if __name__ == "__main__":
    unittest.main()
