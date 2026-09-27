import { useMemo, useState } from "react";
import { Cpu, Gauge, Hand, Info, MousePointerClick, Timer, Users, Zap } from "lucide-react";
import { Badge, Card, Empty, Stat, useFetch } from "../components/ui";
import { LineChart } from "../components/Chart";
import { useApp } from "../lib/store";
import { OUTCOME } from "../lib/game";
import { tx } from "../lib/i18n";

interface Tl { minute: number; raw: number; eq: number; src: Record<string, number> }
interface Src { key: string; label: string; raw: number; eq: number; rawApm: number; eqApm: number }
interface ApmMatch {
  no: number; name: string; start: string; live: boolean; outcome: string | null; speed: number; gameMinutes: number;
  raw: { total: number; apm: number; peak10s: number }; eq: { total: number; apm: number; peak10s: number };
  sources: Src[]; timeline: Tl[]; reaction: { n: number; median: number; p90: number; min: number } | null; complete: boolean;
  lastMinute?: { raw: number; eq: number }; restraint?: { sameOrderSkipped: number; yielded: number };
}
interface ApmResp { inst: number; matches: ApmMatch[] }

// 人类数字是社区里常见的粗略范围，不是统计出来的
const HUMAN = [
  { label: tx("新手 / 休闲玩家"), lo: 20, hi: 60 },
  { label: tx("普通天梯玩家"), lo: 60, hi: 150 },
  { label: tx("职业选手"), lo: 200, hi: 350 },
];

function CompareRow({ label, value, lo, hi, max, color, sub }: { label: string; value?: number; lo?: number; hi?: number; max: number; color: string; sub?: string }) {
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  return (
    <div className="row" style={{ gap: 12, padding: "7px 0", alignItems: "center" }}>
      <div style={{ width: 150, flex: "none", fontSize: 13 }}>
        <div>{label}</div>
        {sub && <div className="faint" style={{ fontSize: 11.5 }}>{sub}</div>}
      </div>
      <div className="grow" style={{ position: "relative", height: 18, background: "var(--bg-sunken)", borderRadius: 4, overflow: "hidden" }}>
        {value != null && <i style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: pct(value), background: color, borderRadius: 4 }} />}
        {lo != null && hi != null && (
          <i style={{ position: "absolute", left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})`, top: 0, bottom: 0, background: color, opacity: 0.55, borderRadius: 4 }} />
        )}
      </div>
      <b className="mono" style={{ width: 84, textAlign: "right", fontSize: 13 }}>{value != null ? Math.round(value) : `${lo}–${hi}`}</b>
    </div>
  );
}

export default function Apm() {
  const inst = useApp((s) => s.inst);
  const { data, error } = useFetch<ApmResp>(`/api/i/${inst}/apm?matches=10`, 3000, [inst]);
  const [pick, setPick] = useState<string | null>(null);
  const matches = data?.matches ?? [];
  const m = matches.find((x) => x.name === pick) ?? matches[0];

  const chart = useMemo(() => {
    if (!m) return null;
    const tl = m.timeline;
    return {
      labels: tl.map((x) => `${x.minute}:00`),
      series: [
        { name: tx("折算人类操作"), color: "var(--accent)", values: tl.map((x) => x.eq) },
        { name: tx("原始（每单位一条）"), color: "var(--text-3)", values: tl.map((x) => x.raw), dashed: true },
        { name: tx("大脑"), color: "#3fa561", values: tl.map((x) => x.src.brain ?? 0) },
        { name: tx("毫秒层"), color: "#e2703b", values: tl.map((x) => x.src.mod_combat ?? 0) },
      ],
    };
  }, [m]);

  if (error) return <div className="page"><Empty title={tx("读不到指令数据")}>{error}</Empty></div>;
  if (!data) return <div className="page"><Empty title={tx("加载中…")} /></div>;
  if (!m) return <div className="page"><Empty title={tx("这个实例还没有新大脑的对局记录")} /></div>;

  const max = Math.max(360, m.raw.peak10s * 1.05);
  const o = m.outcome ? OUTCOME[m.outcome] : null;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("指令速度 · 和人类 APM 比一比")}</h1>
          <p className="page-desc">{tx("AI 每一条下给游戏的命令都有记录。按游戏时间折算成每分钟，再换算成人类要点几下鼠标键盘，和人类玩家的 APM 放在一起看。")}</p>
        </div>
        <div className="right row">
          <select className="input" value={m.name} onChange={(e) => setPick(e.target.value)} style={{ minWidth: 250 }}>
            {matches.map((x) => (
              <option key={x.name} value={x.name}>{tx("第 {0} 局 · {1} ·", x.no, x.start.slice(11, 16))} {x.live ? tx("进行中") : OUTCOME[x.outcome ?? ""]?.label ?? x.outcome}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label={tx("折算人类 APM")} icon={<MousePointerClick size={14} />} tone="accent" value={m.eq.apm.toFixed(0)}
          sub={m.lastMinute ? tx("最近 1 游戏分钟 {0}", m.lastMinute.eq.toFixed(0)) : tx("整局 {0} 次操作", m.eq.total)} />
        <Stat label={tx("原始 APM（每单位一条令）")} icon={<Cpu size={14} />} value={m.raw.apm.toFixed(0)}
          sub={m.lastMinute ? tx("最近 1 游戏分钟 {0}", m.lastMinute.raw.toFixed(0)) : tx("整局 {0} 条令", m.raw.total)} />
        <Stat label={tx("最忙的 10 游戏秒（折算到每分钟）")} icon={<Zap size={14} />} value={m.eq.peak10s}
          sub={tx("原始 {0}", m.raw.peak10s)} />
        <Stat label={tx("保命反应（中位）")} icon={<Timer size={14} />} value={m.reaction ? `${m.reaction.median} ms` : "—"}
          sub={m.reaction ? tx("90% 在 {0} ms 内 · {1} 次 · 人类约 200–250 ms", m.reaction.p90, m.reaction.n) : tx("这局毫秒层没拉过人")} />
      </div>

      <div className="grid g-main-side" style={{ marginBottom: 14 }}>
        <Card title={tx("和人类比")} icon={<Hand size={15} />}>
          <CompareRow label={tx("AI 折算（整局平均）")} value={m.eq.apm} max={max} color="var(--accent)" />
          <CompareRow label={tx("AI 折算（最忙 10 秒）")} value={m.eq.peak10s} max={max} color="var(--accent)" />
          <CompareRow label={tx("AI 原始（整局平均）")} value={m.raw.apm} max={max} color="var(--text-3)" sub={tx("每个单位单独算")} />
          <div style={{ height: 1, background: "var(--border)", margin: "8px 0" }} />
          {HUMAN.map((h) => <CompareRow key={h.label} label={h.label} lo={h.lo} hi={h.hi} max={max} color="#e2b53b" />)}
          <p className="faint" style={{ fontSize: 12, marginTop: 10, lineHeight: 1.6 }}>
            {tx("人类的数字是社区里常见的粗略范围，不是统计值。人类 APM 里有不少是重复点击、切屏、编队、选中，这些 AI 都不需要： 它不看屏幕，直接读全部单位的数据，也不会连点同一条命令。")}
            {m.restraint && <> {tx("这局 AI 已经")} <b>{tx("忍住 {0} 次", m.restraint.sameOrderSkipped)}</b>{tx("没有重复下同一条令，另有 {0} 次因为别的模块（保命、施法）正握着指挥权而让开。", m.restraint.yielded)}</>}
          </p>
        </Card>
        <Card title={tx("谁在下令")} icon={<Users size={15} />} bodyClass="card-body tight">
          <table className="table">
            <thead><tr><th>{tx("来源")}</th><th className="num">{tx("条数")}</th><th className="num">{tx("原始 APM")}</th><th className="num">{tx("折算 APM")}</th><th className="num">{tx("占比")}</th></tr></thead>
            <tbody>
              {m.sources.map((s) => (
                <tr key={s.key}>
                  <td>{tx(s.label)}</td>
                  <td className="num">{s.raw}</td>
                  <td className="num">{s.rawApm.toFixed(1)}</td>
                  <td className="num">{s.eqApm.toFixed(1)}</td>
                  <td className="num">{m.raw.total ? Math.round((s.raw / m.raw.total) * 100) : 0}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="faint" style={{ fontSize: 12, padding: "10px 14px", lineHeight: 1.6 }}>
            {tx("游戏 {0} 倍速在跑，这局已打 {1} 游戏分钟。", m.speed, m.gameMinutes.toFixed(1))}
            {!m.complete && <><br /><span className="warning">{tx("这局的采金、伐木、造房、集结点、学技能还没进命令日志（09-18 01:36 才补上，下一次大脑换代码后生效），大脑的数偏低。")}</span></>}
          </div>
        </Card>
      </div>

      <Card title={tx("每游戏分钟的指令数")} icon={<Gauge size={15} />} style={{ marginBottom: 14 }}>
        {chart && chart.labels.length > 1 ? <LineChart labels={chart.labels} series={chart.series} height={220} /> : <Empty title={tx("还不到两分钟")} />}
      </Card>

      <div className="grid g2">
        <Card title={tx("最近几局")} icon={<Timer size={15} />} bodyClass="card-body tight">
          <table className="table">
            <thead><tr><th>{tx("局")}</th><th>{tx("结果")}</th><th className="num">{tx("游戏分钟")}</th><th className="num">{tx("折算 APM")}</th><th className="num">{tx("原始 APM")}</th><th className="num">{tx("峰值")}</th><th className="num">{tx("反应")}</th></tr></thead>
            <tbody>
              {matches.map((x) => {
                const oo = x.outcome ? OUTCOME[x.outcome] : null;
                return (
                  <tr key={x.name} onClick={() => setPick(x.name)} style={{ cursor: "pointer" }} className={x.name === m.name ? "on" : ""}>
                    <td className="mono">#{x.no} {x.start.slice(11, 16)}</td>
                    <td>{x.live ? <Badge tone="info">{tx("进行中")}</Badge> : <Badge tone={oo?.tone}>{oo?.label ?? x.outcome}</Badge>}</td>
                    <td className="num">{x.gameMinutes.toFixed(1)}</td>
                    <td className="num"><b>{x.eq.apm.toFixed(0)}</b></td>
                    <td className="num">{x.raw.apm.toFixed(0)}</td>
                    <td className="num">{x.eq.peak10s}</td>
                    <td className="num">{x.reaction ? `${x.reaction.median}ms` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        <Card title={tx("怎么换算的")} icon={<Info size={15} />}>
          <div className="col" style={{ gap: 8, fontSize: 13, lineHeight: 1.65 }}>
            <div><b>{tx("原始")}</b>{tx("：AI 给每个单位单独下令，12 个兵一起攻击就是 12 条。")}</div>
            <div><b>{tx("折算人类操作")}</b>{tx("：同一秒、同一个模块、同一种命令、同一个目标的令合并成")} <b>{tx("1 次点击")}</b>{tx("；这批单位和它上一批不一样，再加")} <b>{tx("1 次选中")}</b>{tx("。人类框选一队兵右键一下，就是这 2 个操作。")}</div>
            <div><b>{tx("按游戏时间算")}</b>{tx("：录像里的人类 APM 按游戏内时间算，这里也一样。AI 在 {0} 倍速下跑，墙钟一分钟约是 {1} 游戏分钟。", m.speed, m.speed.toFixed(0))}</div>
            <div><b>{tx("最忙 10 秒")}</b>{tx("：把游戏时间切成 10 秒一段，取操作最多的那一段乘 6。")}</div>
            <div><b>{tx("保命反应")}</b>{tx("：毫秒层从读到\"这个单位快死了 / 被集火了\"到撤退令发出去的时间，不含游戏自己执行命令的时间。人类从看到画面到手上动，简单反应约 200–250 ms。")}</div>
          </div>
        </Card>
      </div>
      {o && null}
    </div>
  );
}
