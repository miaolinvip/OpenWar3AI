import { useMemo, useState } from "react";
import { Activity, Bot, Cpu, Gauge, Hammer, ListTree, Radio, Search, Shield, Swords, Timer } from "lucide-react";
import { Badge, Card, Empty, Seg, Stat, Switch, useFetch } from "../components/ui";
import { api, action, I } from "../lib/api";
import { navigate, useApp } from "../lib/store";
import { ATTACK_STATE, DEFENSE_MODE, aiSentence, aiState, fmtTime, unitName } from "../lib/game";
import type { ConfigItem } from "../lib/types";
import { tx } from "../lib/i18n";

const STATES = ["home", "creep", "gather", "attack", "retreat"];

export default function BrainPage() {
  const brain = useApp((s) => s.brain);
  const meta = useApp((s) => s.meta);
  const inst = useApp((s) => s.inst);
  const status = useApp((s) => s.instances.find((i) => i.inst === s.inst));
  const [tab, setTab] = useState<"plan" | "notes" | "counts" | "jobs">("plan");
  const [q, setQ] = useState("");
  const { data: schema } = useFetch<ConfigItem[]>("/api/brain/schema");
  const { data: cfg, reload } = useFetch<{ overrides: Record<string, unknown>; values: Record<string, any> }>(`/api/i/${inst}/brain/config`, 0, [inst]);
  const toggles = (schema ?? []).filter((x) => x.type === "bool" || x.type === "choice");

  const notes = useMemo(() => {
    const k = q.trim();
    const list = [...(brain?.notes ?? [])].reverse();
    return k ? list.filter(([, t]) => t.includes(k)) : list;
  }, [brain?.notes, q]);

  if (!brain) {
    const ai = aiState(status);
    return (
      <div className="page">
        <Empty icon={<Bot size={36} />} title={ai.kind === "bot" || ai.kind === "mod" ? aiSentence(status) : tx("没有 AI 实时数据")}>
          <div className="col gap8" style={{ alignItems: "center" }}>
            <span>{ai.kind === "bot" || ai.kind === "mod"
              ? tx("这一页只显示参考大脑（新大脑）的思路。方案和模组的输出在日志页，这一局发生的事在「事件与输入」页。")
              : tx("实例 {0} 没有在跑新大脑（war3-brain），或者这一局还没开始。可以去「实例与开局」启动。", inst)}</span>
            <div className="row" style={{ gap: 6 }}>
              {ai.kind === "bot" || ai.kind === "mod" ? (
                <>
                  <button className="btn sm" onClick={() => navigate("logs")}>{tx("看方案日志")}</button>
                  <button className="btn sm" onClick={() => navigate("events")}>{tx("看事件流")}</button>
                </>
              ) : <button className="btn sm" onClick={() => navigate("instances")}>{tx("去「实例与开局」")}</button>}
            </div>
          </div>
        </Empty>
      </div>
    );
  }
  const b = brain;
  const age = Date.now() / 1000 - b.wall;
  const set = async (key: string, v: unknown) => { await action(() => api.put(I("/brain/config"), { [key]: v }), tx("已下发，大脑 2 秒内生效")); reload(); };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("AI 决策")}</h1>
          <p className="page-desc">{tx("新大脑（AMAI 式）每 1.5 游戏秒发布一次：出击状态机、守家、建造规划、战术意图、计数。数据")} {age < 3 ? <span className="success">{tx("实时")}</span> : <span className="warning">{Math.round(age)} {tx("秒前")}</span>} {tx("· 游戏 {0} · 配置第 {1} 版", fmtTime(b.t), b.configVersion)}</p>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label={tx("我方强度")} icon={<Swords size={14} />} value={<span className="success">{b.world.ownStrength}</span>} sub={tx("{0} 兵 · {1} 英雄 · {2} 工人", b.world.army, b.world.heroes, b.world.workers)} />
        <Stat label={tx("敌方强度")} icon={<Swords size={14} />} value={<span className="danger">{b.world.foeStrength}</span>} sub={tx("{0} 个作战单位 · {1} 座建筑", b.world.enemyFighters, b.world.enemyBuildings)} />
        <Stat label={tx("进攻性（AMAI）")} icon={<Gauge size={14} />} value={b.world.aggression} sub={tx("本 {0} 对 {1} · 对手 {2}", b.world.tier, b.world.foeTier, b.world.foeRace ?? "?")} />
        <Stat label={tx("经济")} icon={<Activity size={14} />} value={`${b.economy.goldCrew} / ${b.economy.lumberCrew}`} sub={tx("采金 / 伐木 · 树 {0} · 峰值兵力 {1}", b.economy.trees, b.peak.army)} />
      </div>

      <div className="grid g3" style={{ marginBottom: 14 }}>
        <Card title={tx("出击状态机")} icon={<Swords size={15} />}>
          <div className="row" style={{ gap: 4, marginBottom: 12 }}>
            {STATES.map((s, i) => (
              <div key={s} className="row" style={{ gap: 4, flex: 1 }}>
                <div style={{ flex: 1, textAlign: "center", padding: "7px 0", borderRadius: "var(--radius)", fontWeight: 600, fontSize: 12.5,
                  background: b.attack.state === s ? "var(--accent)" : "var(--bg-sunken)", color: b.attack.state === s ? "var(--accent-fg)" : "var(--text-3)", border: "1px solid var(--border)" }}>
                  {ATTACK_STATE[s]}
                </div>
                {i < STATES.length - 1 && <span className="faint">›</span>}
              </div>
            ))}
          </div>
          <div className="kv">
            <span>{tx("目标")}</span><span>{b.attack.target ? `${b.attack.targetKind} · ${unitName(b.attack.target.type, meta)} (${b.attack.target.x}, ${b.attack.target.y})` : "—"}</span>
            <span>{tx("出发兵力")}</span><span>{b.attack.startStrength || "—"}</span>
            <span>{tx("营地")}</span><span>{b.attack.camp ? tx("强度 {0} · 剩 {1} 个{2}", b.attack.camp.strength, b.attack.camp.alive, b.attack.camp.committed ? tx(" · 认准") : "") : "—"}</span>
            <span>{tx("集结开始")}</span><span>{b.attack.gatherAt != null ? fmtTime(b.attack.gatherAt) : "—"}</span>
            <span>{tx("上次撤退")}</span><span>{b.attack.retreatAt != null ? fmtTime(b.attack.retreatAt) : "—"}</span>
            <span>{tx("对面兵力打没了")}</span><span>{b.attack.safe ? <Badge tone="success">{tx("是")}</Badge> : tx("否")}</span>
          </div>
          <div className="row wrap" style={{ gap: 4, marginTop: 10 }}>
            {Object.entries(b.attack.counts).sort((a, c) => c[1] - a[1]).slice(0, 8).map(([k, v]) => <Badge key={k}>{k} {v}</Badge>)}
          </div>
        </Card>
        <Card title={tx("守家 · 城镇威胁")} icon={<Shield size={15} />}>
          <div style={{ fontSize: 20, fontWeight: 650, marginBottom: 8 }} className={b.defense.mode === "none" ? "success" : "warning"}>{DEFENSE_MODE[b.defense.mode] ?? b.defense.mode}</div>
          <table className="table">
            <thead><tr><th>{tx("城镇")}</th><th className="num">{tx("威胁")}</th><th className="num">{tx("可接受")}</th></tr></thead>
            <tbody>
              {b.world.towns.map((t, i) => (
                <tr key={i}>
                  <td><span style={{ color: t.owner === 0 ? "var(--success)" : "var(--danger)" }}>●</span> {unitName(t.hall ?? "", meta)} <span className="faint mono">({t.x},{t.y})</span></td>
                  <td className="num">{t.threat}</td>
                  <td className="num faint">{b.world.acceptedThreat}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="section-label">{tx("敌方军团")}</div>
          {b.world.enemyArmies.length === 0 ? <div className="faint">—</div> : b.world.enemyArmies.map((a, i) => (
            <div key={i} className="row" style={{ fontSize: 12.5 }}><span className="danger">●</span> {tx("{0} 个 · 强度", a.n)} {a.strength}<span className="faint mono right">({a.x},{a.y}) {Math.hypot(a.dx, a.dy) > 30 ? tx("移动中") : tx("静止")}</span></div>
          ))}
        </Card>
        <Card title={tx("当前意图 · 开关")} icon={<Radio size={15} />}>
          {b.intents.length === 0 ? <div className="faint" style={{ marginBottom: 8 }}>{tx("这一轮没有下战术令")}</div> : b.intents.map((it, i) => (
            <div key={i} className="row" style={{ fontSize: 12.5, padding: "3px 0" }}>
              <Badge tone={it.kind === "move" ? "info" : "warning"}>{it.kind === "move" ? tx("移动") : tx("攻击移动")}</Badge>
              <span className="grow ellipsis">{it.why}</span><span className="faint">{it.n} {tx("个")}</span>
            </div>
          ))}
          <div className="section-label">{tx("AI 开关（实时生效）")}</div>
          <div className="grid g2" style={{ gap: "6px 12px" }}>
            {toggles.map((t) => (
              <label key={t.key} className="row" style={{ gap: 8, fontSize: 12.5 }} title={t.desc ? tx(t.desc) : undefined}>
                {t.type === "choice" ? (
                  <Seg value={String(cfg?.values?.[t.key] ?? t.default)} options={(t.options ?? []).map((o) => ({ value: o, label: tx(t.labels?.[o] ?? o) }))} onChange={(v) => set(t.key, v)} />
                ) : (
                  <Switch on={Boolean(cfg?.values?.[t.key] ?? t.default)} onChange={(v) => set(t.key, v)} />
                )}{tx(t.label)}
              </label>
            ))}
          </div>
        </Card>
      </div>

      <Card bodyClass="" title={
        <div className="tabs" style={{ border: 0, padding: 0 }}>
          {([["plan", tx("建造规划"), <Hammer size={14} key="i" />], ["notes", tx("决定时间线"), <ListTree size={14} key="i" />], ["counts", tx("计数器"), <Cpu size={14} key="i" />], ["jobs", tx("任务调度"), <Timer size={14} key="i" />]] as const).map(([k, l, ic]) => (
            <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{ic}{l}</button>
          ))}
        </div>
      } actions={tab === "notes" ? <div className="search"><Search size={14} /><input className="input sm" placeholder={tx("筛选")} value={q} onChange={(e) => setQ(e.target.value)} /></div> : tab === "plan" ? <span className="faint" style={{ fontSize: 12 }}>{tx("金")} {b.plan.gold} {tx("· 木")} {b.plan.lumber} {tx("· 已占用")} {b.plan.committed?.join(" / ")} {tx("· 攒钱停在")} <b>{b.plan.stoppedAt ? unitName(b.plan.stoppedAt, meta) : "—"}</b></span> : null}>
        <div style={{ maxHeight: 520, overflow: "auto" }}>
          {tab === "plan" && (
            <table className="table">
              <thead><tr><th className="num">{tx("优先级")}</th><th>{tx("类型")}</th><th>{tx("项目")}</th><th className="num">{tx("有 / 要")}</th><th>{tx("状态")}</th><th>{tx("备注")}</th></tr></thead>
              <tbody>
                {(b.plan.wants ?? []).map((w, i) => (
                  <tr key={i}>
                    <td className="num mono">{w.prio}</td>
                    <td className="muted">{({ unit: tx("单位"), building: tx("建筑"), hero: tx("英雄"), upgrade: tx("研究"), upgrade_hall: tx("升本"), expand: tx("开矿") } as Record<string, string>)[w.kind] ?? w.kind}</td>
                    <td><b style={{ fontWeight: 550 }}>{unitName(w.code, meta)}</b> <span className="mono faint">{w.code}</span></td>
                    <td className="num">{w.have ?? "?"} / {w.qty}</td>
                    <td><Badge tone={w.status === "下单" ? "success" : w.status === "攒钱" ? "warning" : w.status === "已满足" ? "" : w.status === "没轮到" ? "" : "info"}>{tx(w.status)}</Badge></td>
                    <td className="faint">{w.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "notes" && (
            <div className="timeline">
              {notes.map(([t, text], i) => (
                <div key={i} className="tl-item"><span className="tl-t">{fmtTime(t)}</span><span>{text}</span></div>
              ))}
            </div>
          )}
          {tab === "counts" && (
            <div className="grid g2" style={{ padding: 14 }}>
              {[[tx("决策计数"), b.counts], [tx("下令回执"), b.act]].map(([title, obj]) => {
                const entries = Object.entries(obj as Record<string, number>).sort((a, c) => c[1] - a[1]);
                const max = entries[0]?.[1] ?? 1;
                return (
                  <div key={title as string}>
                    <div className="section-label" style={{ marginTop: 0 }}>{title as string}</div>
                    {entries.map(([k, v]) => (
                      <div key={k} className="row" style={{ fontSize: 12.5, padding: "2px 0" }}>
                        <span className="grow ellipsis" title={k}>{k}</span>
                        <div style={{ width: 90 }} className="bar accent"><i style={{ width: `${(v / max) * 100}%` }} /></div>
                        <span className="mono" style={{ width: 46, textAlign: "right" }}>{v}</span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
          {tab === "jobs" && (
            <table className="table">
              <thead><tr><th>{tx("任务")}</th><th className="num">{tx("运行次数")}</th><th className="num">{tx("出错")}</th><th className="num">{tx("耗时 ms")}</th><th>{tx("最后错误")}</th></tr></thead>
              <tbody>
                {Object.entries(b.jobs).map(([k, j]) => (
                  <tr key={k}><td className="mono">{k}</td><td className="num">{j.runs}</td><td className={`num ${j.errors ? "danger" : ""}`}>{j.errors}</td><td className="num">{j.ms}</td><td className="danger mono" style={{ fontSize: 11 }}>{j.last_error ?? ""}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}
