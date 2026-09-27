import { useMemo } from "react";
import { Activity, Bot, Coins, Crown, Eye, Gauge, Hammer, Shield, Skull, Swords, Trees, Trophy, Users } from "lucide-react";
import { Badge, Bar, Card, Empty, Stat, useFetch } from "../components/ui";
import MapView, { DEFAULT_LAYERS } from "../components/MapView";
import { navigate, useApp } from "../lib/store";
import { useInstanceMap, useUnits } from "../lib/hooks";
import { ATTACK_STATE, CATEGORY_LABEL, DEFENSE_MODE, OUTCOME, RACE_NAMES, aiSentence, aiState, category, fmtTime, unitName } from "../lib/game";
import type { MatchSummary } from "../lib/types";
import { tx } from "../lib/i18n";

interface PlayerRow {
  owner: number; me: boolean; units: number; gold: number | null; lumber: number | null; foodUsed: number | null; foodCap: number | null;
  upkeep: number | null; goldGathered: number | null; lumberGathered: number | null; race?: number; difficulty?: number;
}

const DIFF_NAME: Record<number, string> = { 0: tx("简单"), 1: tx("普通"), 2: tx("疯狂") };

/** 双方经济。★ 全知信息：直接问引擎要对手的资源，人类玩家看不到（对手的电脑 AI 同样看得见我方全图）。 */
function Economy() {
  const inst = useApp((s) => s.inst);
  const { data } = useFetch<{ me: number; players: PlayerRow[] }>(`/api/i/${inst}/players`, 2500, [inst]);
  const rows = (data?.players ?? []).filter((p) => p.owner < 12);
  return (
    <Card title={tx("双方经济")} icon={<Coins size={15} />} actions={<Badge tone="warning" title={tx("这些数字是直接从游戏内存里读对手的，人类玩家看不到。对手的电脑 AI 同样看得见我方全图。")}><Eye size={12} /> {tx("全知信息")}</Badge>}
      bodyClass="card-body tight">
      {!rows.length ? <Empty title={tx("还没连上对局")} /> : (
        <table className="table">
          <thead><tr><th>{tx("玩家")}</th><th className="num">{tx("金")}</th><th className="num">{tx("木")}</th><th className="num">{tx("人口")}</th><th className="num">{tx("累计采金")}</th><th className="num">{tx("累计采木")}</th><th className="num">{tx("维护费")}</th><th className="num">{tx("单位")}</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.owner}>
                <td>{p.me ? <b className="success">{tx("我方")}</b> : <b className="danger">{tx("对手")}</b>}
                  <span className="faint mono" style={{ marginLeft: 6 }}>{p.race ? RACE_NAMES[p.race] ?? "" : ""}{!p.me && p.difficulty != null ? ` · ${DIFF_NAME[p.difficulty] ?? p.difficulty}` : ""}</span></td>
                <td className="num">{p.gold ?? "—"}</td>
                <td className="num">{p.lumber ?? "—"}</td>
                <td className="num">{p.foodUsed ?? "—"}/{p.foodCap ?? "—"}</td>
                <td className="num"><b>{p.goldGathered ?? "—"}</b></td>
                <td className="num">{p.lumberGathered ?? "—"}</td>
                <td className="num">{p.upkeep != null ? `${p.upkeep}%` : "—"}</td>
                <td className="num">{p.units}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="faint" style={{ fontSize: 12, padding: "10px 14px", lineHeight: 1.6 }}>
        {tx("累计采金是从开局到现在真正挖出来的金子 —— 比当前存量更能说明谁的经济好。维护费档 30% / 60% 会按比例吃掉收入。")}
      </div>
    </Card>
  );
}

export default function Overview() {
  const meta = useApp((s) => s.meta);
  const inst = useApp((s) => s.inst);
  const snap = useApp((s) => s.snapshot);
  const brain = useApp((s) => s.brain);
  const status = useApp((s) => s.instances.find((i) => i.inst === s.inst));
  const mapInfo = useInstanceMap();
  const { units, me } = useUnits();
  const { data: matches } = useFetch<MatchSummary[]>(`/api/i/${inst}/matches?limit=40`, 15000, [inst]);

  const counts = useMemo(() => {
    const mine = new Map<string, number>(), foe = new Map<string, number>();
    let myArmy = 0, foeArmy = 0, myWorkers = 0, foeWorkers = 0;
    for (const u of units) {
      if (u.hp <= 0 || u.owner >= 12) continue;
      const c = category(u.type, meta);
      const m = u.owner === me ? mine : foe;
      m.set(u.type, (m.get(u.type) ?? 0) + 1);
      if (c === "unit" || c === "hero") u.owner === me ? myArmy++ : foeArmy++;
      if (c === "worker") u.owner === me ? myWorkers++ : foeWorkers++;
    }
    return { mine, foe, myArmy, foeArmy, myWorkers, foeWorkers };
  }, [units, me, meta]);

  const record = useMemo(() => {
    const done = (matches ?? []).filter((m) => m.result);
    const w = done.filter((m) => m.result!.outcome === "victory").length;
    return { total: done.length, wins: w, recent: done.slice(0, 20) };
  }, [matches]);

  const b = brain;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("实例 {0} · 总览", inst)}</h1>
          <p className="page-desc">
            {tx("{0} · {1} 对 {2}{3} · {4}", mapInfo?.name ?? "—", status?.game.race ? RACE_NAMES[status.game.race] : "?", status?.game.enemyRace ? RACE_NAMES[status.game.enemyRace] : "?", status?.speed ? tx(" · {0} 倍速", status.speed / 100) : "", aiSentence(status))}
          </p>
        </div>
        <div className="right row">
          <button className="btn" onClick={() => navigate("battlefield")}><Swords size={15} />{tx("进入战场")}</button>
          <button className="btn primary" onClick={() => navigate("brain")}><Bot size={15} />{tx("看 AI 决策")}</button>
        </div>
      </div>

      <div className="grid g6" style={{ marginBottom: 14 }}>
        <Stat label={tx("游戏时间")} icon={<Activity size={14} />} value={fmtTime(snap?.t)} sub={snap ? tx("快照 {0} 个单位", snap.units.length) : tx("未连接")} />
        <Stat label={tx("金")} icon={<Coins size={14} style={{ color: "#e2b53b" }} />} value={snap?.gold ?? "—"} sub={b ? tx("采金 {0} 人", b.economy.goldCrew) : undefined} />
        <Stat label={tx("木")} icon={<Trees size={14} style={{ color: "#3fa561" }} />} value={snap?.lumber ?? "—"} sub={b ? tx("伐木 {0} 个", b.economy.lumberCrew) : undefined} />
        <Stat label={tx("人口")} icon={<Users size={14} />} value={snap && snap.foodCap != null ? `${snap.foodUsed ?? "—"}/${snap.foodCap}` : "—"} sub={snap?.foodCap ? <Bar value={snap.foodUsed ?? 0} max={snap.foodCap} kind="accent" /> : undefined} />
        <Stat label={tx("兵力 我 / 敌")} icon={<Swords size={14} />} value={<><span className="success">{counts.myArmy}</span><span className="faint"> / </span><span className="danger">{counts.foeArmy}</span></>} sub={b ? tx("强度 {0} / {1}", b.world.ownStrength, b.world.foeStrength) : undefined} />
        <Stat label={tx("战绩（近 40 局）")} icon={<Trophy size={14} />} value={record.total ? tx("{0} 胜 / {1}", record.wins, record.total) : "—"} sub={record.total ? tx("胜率 {0}%", Math.round((record.wins / record.total) * 100)) : undefined} />
      </div>

      <div className="grid g-main-side" style={{ marginBottom: 14 }}>
        <Card title={tx("全图态势")} icon={<Crown size={15} />} actions={<button className="btn sm ghost" onClick={() => navigate("battlefield")}>{tx("放大指挥")}</button>} bodyClass="card-body tight">
          <div style={{ position: "relative", height: 420, background: "var(--map-bg)", borderRadius: "0 0 var(--radius-lg) var(--radius-lg)", overflow: "hidden" }}>
            {snap ? (
              <MapView snapshot={snap} meta={meta} mapInfo={mapInfo} brain={brain} cam={null} selection={[]} layers={{ ...DEFAULT_LAYERS, hp: false, intents: false }}
                armedLabel={null} onSelect={() => {}} onPrimary={() => false} onSecondary={() => {}} onDouble={() => navigate("battlefield")} fitKey={`ov${inst}`} compact />
            ) : <Empty title={tx("没连上游戏")} />}
          </div>
        </Card>
        <Card title={tx("AI 此刻在想什么")} icon={<Bot size={15} />} actions={<button className="btn sm ghost" onClick={() => navigate("brain")}>{tx("详情")}</button>}>
          {!b ? (
            aiState(status).kind === "bot" || aiState(status).kind === "mod" ? (
              <Empty title={aiSentence(status)}>
                <div className="col gap8" style={{ alignItems: "center" }}>
                  <span>{tx("这里只显示参考大脑的实时思路；方案和模组的输出在日志页，事件在「事件与输入」页。")}</span>
                  <div className="row" style={{ gap: 6 }}>
                    <button className="btn sm" onClick={() => navigate("logs")}>{tx("看方案日志")}</button>
                    <button className="btn sm" onClick={() => navigate("events")}>{tx("看事件流")}</button>
                  </div>
                </div>
              </Empty>
            ) : <Empty title={tx("没有大脑实时数据")}>{tx("这个实例没跑新大脑，或者还没开局")}</Empty>
          ) : (
            <div className="col gap12">
              <div className="grid g2">
                <div className="card" style={{ padding: 12 }}>
                  <div className="stat-label"><Swords size={13} />{tx("出击")}</div>
                  <div style={{ fontSize: 18, fontWeight: 650, marginTop: 2 }}>{ATTACK_STATE[b.attack.state] ?? b.attack.state}</div>
                  <div className="faint" style={{ fontSize: 12 }}>{b.attack.target ? tx("目标：{0} {1}", b.attack.targetKind, unitName(b.attack.target.type, meta)) : b.attack.camp ? tx("营地强度 {0}（剩 {1}）", b.attack.camp.strength, b.attack.camp.alive) : "—"}</div>
                </div>
                <div className="card" style={{ padding: 12 }}>
                  <div className="stat-label"><Shield size={13} />{tx("守家")}</div>
                  <div style={{ fontSize: 18, fontWeight: 650, marginTop: 2 }} className={b.defense.mode === "none" ? "success" : "warning"}>{DEFENSE_MODE[b.defense.mode] ?? b.defense.mode}</div>
                  <div className="faint" style={{ fontSize: 12 }}>{tx("进攻性 {0} · 可接受威胁", b.world.aggression)} {b.world.acceptedThreat}</div>
                </div>
              </div>
              <div>
                <div className="section-label" style={{ marginTop: 0 }}><Hammer size={12} />{tx("建造队列（前 6）")}</div>
                {(b.plan.wants ?? []).slice(0, 6).map((w, i) => (
                  <div key={i} className="row" style={{ fontSize: 12.5, padding: "3px 0" }}>
                    <span className="mono faint" style={{ width: 26 }}>{w.prio}</span>
                    <span className="grow ellipsis">{unitName(w.code, meta)} <span className="faint">{w.have ?? "?"}/{w.qty}</span></span>
                    <Badge tone={w.status === "下单" ? "success" : w.status === "攒钱" ? "warning" : w.status === "已满足" ? "" : "info"}>{tx(w.status)}</Badge>
                  </div>
                ))}
              </div>
              <div>
                <div className="section-label"><Gauge size={12} />{tx("最近决定")}</div>
                <div className="col" style={{ gap: 3 }}>
                  {[...b.notes].slice(-7).reverse().map(([t, text], i) => (
                    <div key={i} className="row" style={{ fontSize: 12.5, alignItems: "flex-start" }}>
                      <span className="mono faint" style={{ width: 44, flex: "none" }}>{fmtTime(t)}</span>
                      <span className="grow">{text}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </Card>
      </div>

      <div className="grid g2" style={{ marginBottom: 14 }}>
        <Economy />
        <Card title={tx("双方兵种")} icon={<Users size={15} />}>
          <div className="grid g2">
            {[[tx("我方"), counts.mine, "success"], [tx("对手"), counts.foe, "danger"]].map(([label, m, tone]) => (
              <div key={label as string}>
                <div className="section-label" style={{ marginTop: 0 }}><span className={tone as string}>●</span>{label as string}</div>
                <div className="col" style={{ gap: 4 }}>
                  {[...(m as Map<string, number>).entries()].sort((a, c) => c[1] - a[1]).slice(0, 14).map(([code, n]) => (
                    <div key={code} className="row" style={{ fontSize: 12.5 }}>
                      <span className="grow ellipsis">{unitName(code, meta)}</span>
                      <span className="faint" style={{ fontSize: 11 }}>{CATEGORY_LABEL[category(code, meta)]}</span>
                      <b style={{ width: 28, textAlign: "right" }}>{n}</b>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid g2">
        <Card title={tx("最近对局")} icon={<Trophy size={15} />} actions={<button className="btn sm ghost" onClick={() => navigate("matches")}>{tx("全部")}</button>}>
          {!record.recent.length ? <Empty title={tx("还没有打完的对局")} /> : (
            <>
              <div className="row" style={{ gap: 3, marginBottom: 12 }}>
                {record.recent.slice().reverse().map((m) => {
                  const o = OUTCOME[m.result!.outcome];
                  return <span key={m.name} title={`${m.name} · ${o?.label ?? m.result!.outcome}`} style={{ flex: 1, height: 22, borderRadius: 3, background: o?.tone === "success" ? "var(--success)" : o?.tone === "danger" ? "var(--danger)" : o?.tone === "warning" ? "var(--warning)" : "var(--border-strong)" }} />;
                })}
              </div>
              <table className="table">
                <thead><tr><th>{tx("时间")}</th><th>{tx("结果")}</th><th className="num">{tx("时长")}</th><th className="num">{tx("峰值兵力")}</th></tr></thead>
                <tbody>
                  {record.recent.slice(0, 7).map((m) => {
                    const o = OUTCOME[m.result!.outcome];
                    return (
                      <tr key={m.name}>
                        <td className="mono">{m.date?.slice(4, 6)}-{m.date?.slice(6)} {m.time?.slice(0, 2)}:{m.time?.slice(2, 4)}</td>
                        <td><Badge tone={o?.tone}>{o?.label ?? m.result!.outcome}</Badge></td>
                        <td className="num">{tx("{0} 分钟", m.result!.minutes?.toFixed(1))}</td>
                        <td className="num">{m.result!.peakArmy}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </Card>
      </div>
      {snap && !units.some((u) => u.owner === me && category(u.type, meta) === "hall") && (
        <div className="card" style={{ marginTop: 14, padding: 12, borderColor: "var(--danger)" }}><Skull size={14} className="danger" /> {tx("我方没有大厅了")}</div>
      )}
    </div>
  );
}
