# -*- coding: utf-8 -*-
"""这一仗打不打：回防、支援交战、分矿守不守 —— 所有「打不打得过」只从这里问。

## 为什么有这一层（09-22）

现象：打不赢的团战，剩下的单位仍被反复投入，直到全部阵亡；看起来已经撤退，又被安排回头继续打。
查下来同一个问题有三套判据、互相不认：
  * 回防 —— AMAI 的城镇威胁（按距离加权）对比 player_threat(我方强度 × 进攻性倍数)，没校准过；
  * 支援交战 —— amai 兵力比对 SUPPORT_MIN_RATIO；
  * 分矿守不守 —— 当天手调的「打光时间 × 1.5」（第三套口径）。
毫秒层的叫撤信号（fight_retreat.json）只有出击层在「在场人数 >= 全军一半」时才认，回防根本不看 ——
3 局 196 次「毫秒层刚把人拉出来，4 秒内大脑又派回去打」。

参考了 TypeSafe Jev / laya（「System One」类型化决策）的接口思路：业务代码不自己写阈值，
只问「这一仗有几成」，拿**校准过的**概率分档行动。概率本身用项目里已有的那条校准曲线
（winprob：964 个战斗帧文件、5981 场仗拟合的 amai 兵力比 -> 胜率，检验集 AUC 0.71），不另起一个模型。
以后换成世界模型的胜负预测（worldmodel/winpred_infer），只改这里。

## 三档

    放弃 giveup   胜率 < P_GIVEUP（11%，兵力比 0.63），或毫秒层 LOST_HOLD_S 内在这片战场叫过撤
    压上 commit   胜率 >= 这个场景的线（LINES）
    观望 hold     介于两者之间：不硬打、不零散往里填人，先集结 / 等援兵

三个场景的线都是 P_EVEN（兵力比 1.0 那一点的胜率，24%），区别在「到不了线」时调用方怎么做：
    main       主基地   家不能丢：先退到大厅背后集结，到齐 / 敌人开始拆建筑才反打（tactics.Defense 原有那条路）
    expansion  分矿     不送：保兵回主基地，等兵力够了再来
    field      野外交战 不压上去：接触着的人先退回主力那边（tactics.AttackSequence._support）
「放弃」档（含毫秒层叫过撤）在野外 = 整队撤回家。

「赢」是 winprob 的口径：这一仗我方阵亡的人头份额低于对面。打玩家的仗胜率封顶约 44%（兵力比再大也没用），
所以线只能定在 11%~44% 之间，P_EVEN 取兵力比 1.0 那一点，是从曲线上读出来的，不是手调的倍数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import amai
from . import winprob as W

P_GIVEUP = W.P_GIVEUP
R_EVEN = 1.0                # 兵力相当
P_EVEN = W.win_prob(R_EVEN)
LINES = {"main": P_EVEN, "expansion": P_EVEN, "field": P_EVEN}
LOST_HOLD_S = 30.0          # 毫秒层叫撤之后这么多游戏秒内，这片战场判「放弃」
LOST_R = 2000.0             #   「这片战场」= 叫撤的位置这么近
BAND_CN = {"commit": "压上", "hold": "观望", "giveup": "放弃"}
WHERE_CN = {"main": "主基地", "expansion": "分矿", "field": "野外"}


@dataclass
class Call:
    band: str               # commit / hold / giveup
    p: float                # 校准过的胜率
    ratio: float            # amai 兵力比（我方 / 敌方）
    ours: float
    theirs: float
    where: str
    lost: bool = False      # 毫秒层叫过撤

    support: float = 0.0    # 我方参战的塔（含在 ours 里）

    @property
    def why(self) -> str:
        s = f"胜率 {self.p:.0%}（兵力比 {self.ratio:.2f}：我方 {self.ours:.0f} / 敌方 {self.theirs:.0f}）"
        if self.support > 0:
            s += f"（含我方塔 {self.support:.0f}）"
        return s + ("，毫秒层叫过撤" if self.lost else "")

    @property
    def key(self) -> str:
        """计数用：判仗·分矿·放弃 这样。"""
        return f"判仗·{WHERE_CN.get(self.where, self.where)}·{BAND_CN[self.band]}" + ("·毫秒层叫过撤" if self.lost else "")


def recently_lost(lost, t: float, x: float, y: float) -> bool:
    """lost = 毫秒层最近一次叫撤 (游戏秒, x, y) 或 None；这片战场（LOST_R 内）LOST_HOLD_S 内叫过就算。"""
    return lost is not None and t - lost[0] <= LOST_HOLD_S and math.hypot(lost[1] - x, lost[2] - y) <= LOST_R


def judge(ours, theirs, levels=None, where: str = "field", lost: bool = False, support=()) -> Call:
    """ours / theirs：这一仗双方参战的单位（调用方决定谁算参战）。
    support = 我方在这片战场上**能开火的建筑**（守家的塔）—— 它们真的在打，原来一点不算。

    ★★ 09-23 （1-439）敌人拆分矿时我方部队不敢上前防守 ——
      实测那一刻：我方 12 步兵 + 6 火枪 对 18 食尸鬼 + 3 蜘蛛，胜率 21% < 压上线 24%，判「分矿守不住，保兵回主基地」，
      而分矿旁边**两座箭塔正在开火**（第 128/129 拍才被拆掉）。把塔按本身强度算进来：21% -> 27%，直接过线。
      这是漏项，不是把阈值调松：塔本来就在这场仗里。"""
    sup = amai.strength(list(support), levels)
    o = amai.strength(ours, levels) + sup
    t = amai.strength(theirs, levels)
    ratio = o / t if t > 0 else (99.0 if o > 0 else 1.0)
    p = W.win_prob(ratio)
    if lost or p < P_GIVEUP:
        band = "giveup"
    elif p >= LINES[where]:
        band = "commit"
    else:
        band = "hold"
    return Call(band, p, ratio, o, t, where, lost, sup)
