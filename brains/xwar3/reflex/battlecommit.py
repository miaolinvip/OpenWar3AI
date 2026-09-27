"""整队撤退须有持续劣势和真实损耗；先到的少数人不能代表全军。墙钟计时。"""
from dataclasses import dataclass, field

# ★★ 09-23 参照 AMAI 火拼时的交战 / 撤退决策重定撤退线。AMAI Jobs/RETREAT_CONTROL.eai：
#   主力 1500 内敌方强度 > 我方 × 2~3（ApplyFleeStrengthModifier，进攻性插值；线性强度）才整队回家，差一点的只放开单兵逃。
#   这里的 ratio = 打光对面要几秒 / 对面打光我们要几秒 = (对面血×输出)/(我方血×输出)，是平方口径：
#   原来 1.8 = 对面只强 1.34 倍就撤（世界模型也判输时 1.25 = 1.12 倍）。照 AMAI 的 2 倍线 -> 4.0；判输时 2.5（1.6 倍）。
#   撤退中被追着打是最亏的（09-07 账本：势均力敌时叫撤比明显要输时撤还惨一倍）；打输了离家远的整队读回城（itemuse.retreat_portal_due）。
FLEE_RATIO = 4.0            # 持续 >= 这个（且打了 2.5 秒、掉了 12% 血）才整队撤
FLEE_RATIO_MODEL = 2.5      # 世界模型也判输时
COLLAPSE_RATIO = 3.0        # 这么悬殊而且 3 秒内就会被打光：不等，立刻撤（原样）
COLLAPSE_S = 3.0


@dataclass
class BattleCommit:
    began: float = -1.0
    last: float = -1.0
    bad_since: float = -1.0
    health: dict = field(default_factory=dict)
    enemy_ids: set = field(default_factory=set)

    def observe(self, now, army, foes, kill_seconds, survival_seconds, model_bad=False):
        ids = {(f.addr, getattr(f, 'handle_lo', 0), getattr(f, 'handle_hi', 0)) for f in foes}
        if not army or not ids:
            self.began = self.bad_since = -1.0
            self.health.clear()
            self.enemy_ids.clear()
            return False
        if self.began < 0 or now-self.last > 3.0 or not (ids & self.enemy_ids):
            self.began, self.bad_since, self.health = now, -1.0, {}
        self.last, self.enemy_ids = now, ids
        current = {(u.addr, getattr(u,'handle_lo',0), getattr(u,'handle_hi',0)): u.hp for u in army}
        for k,hp in current.items():
            self.health[k] = max(hp,self.health.get(k,hp))
        # 走出局部圈的人不能被当作阵亡；仅统计仍在这片战场的同身份单位实际掉血。
        base = sum(self.health[k] for k in current)
        loss = sum(max(0,self.health[k]-hp) for k,hp in current.items()) / max(1,base)
        ratio = kill_seconds / max(.1,survival_seconds)
        if ratio >= COLLAPSE_RATIO and survival_seconds <= COLLAPSE_S:
            return True
        bad = ratio >= FLEE_RATIO or (model_bad and ratio >= FLEE_RATIO_MODEL)
        if not bad:
            self.bad_since = -1.0
            return False
        if self.bad_since < 0:
            self.bad_since = now
        return now-self.began >= 2.5 and now-self.bad_since >= 1.2 and loss >= .12
