import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain import tactics as T
from brain.runtime import Brain
from test_tactics import U, world
from test_runtime_smoke import FakeCmd


class HeroHandoff(unittest.TestCase):
    def test_solo_lease_excludes_hero_after_hurt_lease_ends(self):
        import w3lease
        with tempfile.TemporaryDirectory() as d:
            brain = Brain(FakeCmd(), None, d)
            with patch.object(w3lease, "read", side_effect=lambda path, pid: {2:{}} if path.endswith('solo.json') else {}):
                self.assertEqual(brain._read_hurt(), {2})

    def test_nearby_hero_does_not_bypass_creep_strength_gate(self):
        home = U(1, 'htow')
        hero = U(2, 'Hamg', x=3000)
        creeps = [U(100+i,'nogm',12,3200,i*30) for i in range(5)]
        w=world([home,hero]+creeps,food=10)
        seq=T.AttackSequence(dict(min_attack_strength=4), state='creep', camp_ids={u.addr for u in creeps}, creep_since=w.t)
        seq.camp=dict(x=3200,y=60,strength=25,units=creeps)
        seq.allow_attack=False
        intents=seq.step(w,home)
        self.assertFalse(any(i.kind=='attack' for i in intents))
        self.assertTrue(any(i.kind=='move' and hero in i.units for i in intents))

    def _started_camp(self, hp, hp_max=500):
        home=U(1,'htow')
        hero=U(2,'Hamg',x=3000,hp=hp,hp_max=hp_max)
        creeps=[U(100+i,'nogm',12,3200,i*30) for i in range(5)]
        w=world([home,hero]+creeps,food=10)
        seq=T.AttackSequence({},state='creep',camp_ids={u.addr for u in creeps},creep_engaged=True,creep_since=w.t)
        seq.camp=dict(x=3200,y=60,strength=25,units=creeps)
        seq.allow_attack=False
        return seq, seq.step(w,home)

    def test_close_hurt_hero_does_not_keep_attacking_started_camp(self):
        """受伤英雄不继续打已经开打的营地。

        ⚠ 09-21：血线从 0.35 提到 0.50 之后这条的路径变了 —— 44% 血现在直接触发「英雄残血先回家」，
        比原来的整队 retreat 更早也更彻底。（0.50 一直写在 tactics.py 里，但 config 默认值 0.35 每次都把它覆盖掉，
        所以 #95/v10「低于 50% 退出战略进攻令」这个改动从来没上线过；见 tests/test_config_consistency.py。）
        全队只有这一个英雄，它被摘走之后 army 空，状态自然落到 home。要守的仍然是那句话：不再攻击。"""
        seq, intents = self._started_camp(hp=220)
        self.assertFalse(any(i.kind.startswith('attack') for i in intents))
        self.assertTrue(any(i.why=='英雄残血先回家' for i in intents), [i.why for i in intents])
        self.assertEqual(seq.state,'home')

    def test_healthy_hero_still_stops_when_attack_disabled(self):
        """同一个营地、英雄满血：这时不能靠血线收手，只能靠 allow_attack=False —— 否则上面那条一改血线就空转。"""
        seq, intents = self._started_camp(hp=500)
        self.assertFalse(any(i.kind.startswith('attack') for i in intents))
        self.assertFalse(any(i.why=='英雄残血先回家' for i in intents), [i.why for i in intents])
        self.assertEqual(seq.state,'retreat')
