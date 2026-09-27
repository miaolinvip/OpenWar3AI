"""
W3-Simulate 的 Python 移植（war3-button-hook-lab/war3ai.ps1 `function W3-Simulate`，攻防表 `$script:W3AtkTable`、`W3-AtkMul`）。

用途：
  * M0 场景生成按推演判断分层抽样（推演说赢 / 说输 / 差不多各一份），让演练数据正好覆盖推演出错的地方；
  * M1 在同一批交战上和学出来的胜负预测器比。
**只移植、不改进**：它的毛病（不看位置、不看到场时间、不看技能、双方都当集火、按列表顺序打）正是要被比较的对象。
一致性由 tests/test_simulate.py 守着：和另一次校准（09-16，calib_sim.ps1，94 场）跑出的 win/left/aliveA/aliveB/foeLeft 逐场对上。

PowerShell 细节照搬：
  * 集火 = 按输入列表顺序打，打死溢出到下一个；双方这一步的输出在任何人掉血之前算好；
  * 回血只给活着的（hp > 0）；
  * left 用 [math]::Round(x, 3)（银行家舍入，Python round 同）；secs = [int]$t（**四舍六入五成双，不是截断**）。
"""
from __future__ import annotations

ATK_TABLE = {
    "normal": dict(small=1.00, medium=1.50, large=1.00, fort=0.70, normal=1.00, hero=1.00, divine=0.05, none=1.00),
    "pierce": dict(small=2.00, medium=0.75, large=1.00, fort=0.35, normal=1.00, hero=0.50, divine=0.05, none=1.50),
    "siege": dict(small=1.00, medium=0.50, large=1.00, fort=1.50, normal=1.00, hero=0.50, divine=0.05, none=1.50),
    "magic": dict(small=1.25, medium=0.75, large=2.00, fort=0.35, normal=1.00, hero=0.50, divine=0.05, none=1.00),
    "chaos": dict(small=1.00, medium=1.00, large=1.00, fort=1.00, normal=1.00, hero=1.00, divine=0.05, none=1.00),
    "hero": dict(small=1.00, medium=1.00, large=1.00, fort=0.50, normal=1.00, hero=1.00, divine=0.05, none=1.00),
}
STEP = 0.5
T_MAX = 120.0


def atk_mul(atk: str, def_t: str) -> float:
    row = ATK_TABLE.get(str(atk)) or ATK_TABLE["normal"]
    v = row.get(str(def_t))
    return 1.0 if v is None else float(v)


def _f(d: dict | None, key: str) -> float:
    v = (d or {}).get(key)
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def roster(us: list[tuple[str, float]], them: list[tuple[str, float]], stats: dict) -> list[dict]:
    them_defs: dict[str, int] = {}
    for typ, _ in them:
        td = stats.get(typ)
        k = str(td["defT"]) if td and td.get("defT") else "normal"
        them_defs[k] = them_defs.get(k, 0) + 1
    tot = sum(them_defs.values()) or 1
    out = []
    for typ, hp in us:
        d = stats.get(typ)
        armor = _f(d, "armor")
        ehp = float(hp) * (1.0 + 0.06 * max(0.0, armor))
        dps = 0.0
        if d and _f(d, "cool") > 0.05 and _f(d, "dmgAvg") > 0:
            raw = _f(d, "dmgAvg") / _f(d, "cool")
            mul = sum(atk_mul(d.get("atk"), k) * (n / tot) for k, n in them_defs.items())
            dps = raw * mul
        elif not d:
            dps = float(hp) / 40.0
        out.append(dict(hp=ehp, max=ehp, dps=dps, regen=_f(d, "regen")))
    return out


def simulate(mine: list[tuple[str, float]], foe: list[tuple[str, float]], stats: dict, mine_spread: bool = False) -> dict:
    """mine / foe：[(类型码, 当前血)]，顺序有意义（集火按顺序打）。返回 win / left / secs / aliveA / aliveB。"""
    A = roster(mine, foe, stats)
    B = roster(foe, mine, stats)
    if not A:
        return dict(win=False, left=0.0, secs=0, aliveA=0, aliveB=len(B))
    if not B:
        return dict(win=True, left=1.0, secs=0, aliveA=len(A), aliveB=0)
    start_hp = sum(u["max"] for u in A)
    if start_hp <= 0:
        return dict(win=False, left=0.0, secs=0, aliveA=0, aliveB=len(B))
    t = 0.0
    while t < T_MAX:
        al_a = [u for u in A if u["hp"] > 0]
        al_b = [u for u in B if u["hp"] > 0]
        if not al_a or not al_b:
            break
        d_a = sum(u["dps"] for u in al_a) * STEP
        d_b = sum(u["dps"] for u in al_b) * STEP
        if mine_spread:
            each = d_a / len(al_b)
            for u in al_b:
                u["hp"] -= each
        else:
            rem = d_a
            for u in al_b:
                if rem <= 0:
                    break
                take = min(rem, u["hp"])
                u["hp"] -= take
                rem -= take
        rem = d_b
        for u in al_a:
            if rem <= 0:
                break
            take = min(rem, u["hp"])
            u["hp"] -= take
            rem -= take
        for u in A:
            if u["hp"] > 0:
                u["hp"] = min(u["max"], u["hp"] + u["regen"] * STEP)
        for u in B:
            if u["hp"] > 0:
                u["hp"] = min(u["max"], u["hp"] + u["regen"] * STEP)
        t += STEP
    left_hp = sum(u["hp"] for u in A if u["hp"] > 0)
    foe_left = sum(1 for u in B if u["hp"] > 0)
    return dict(win=foe_left == 0, left=round(left_hp / start_hp, 3), secs=int(round(t)),
                aliveA=sum(1 for u in A if u["hp"] > 0), aliveB=foe_left)


def roster_from_counts(comp: dict, stats: dict, default_hp: float = 500.0) -> list[tuple[str, float]]:
    """calib_sim.ps1 的 Roster：{类型: 个数} 按字典顺序展开、满血（表里没有血量的按 500）。"""
    out = []
    for typ, n in comp.items():
        d = stats.get(typ)
        hp = int(_f(d, "hp")) if d and _f(d, "hp") > 0 else default_hp
        out += [(typ, float(hp))] * int(n)
    return out
