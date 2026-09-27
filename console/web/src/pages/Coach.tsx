import { useState } from "react";
import { Bot, CircleAlert, Cpu, Eye, History, Play, RefreshCw, Sparkles } from "lucide-react";
import { Badge, Card, Empty, FetchState, Switch, useFetch } from "../components/ui";
import { api, action } from "../lib/api";
import { unitName } from "../lib/game";
import { navigate, useApp } from "../lib/store";
import { tx } from "../lib/i18n";

// 运营顾问（本地大模型，war3-brain/brain/coach.py）。
// 09-21 无从确认大模型运营层到底起没起来，需要在远见后台按进程看到这一层的状态。
// 当时 LM Studio 根本没起，而 6 个大脑的行为和「没有这一层」一模一样（设计如此：失败就退回纯规则）——
// 能安全降级的东西最容易悄悄降级一整天，所以这一页同时摆两个视角：
//   模型服务在不在（服务端）   每个实例连上没有、最近一条建议是什么（大脑端）
// 两边对不上（服务在、实例却一直连不上）本身就是一条线索。

type Plan = { diagnosis: string; gold: number | null; lumber: number | null; priority: string[]; posture: string; avoid: string[]; latency: number; at: number };
type CoachLive = {
  state: "off" | "waiting" | "down" | "ok"; enabled: boolean; model: string; busy: boolean; every: number;
  lastTry: number | null; lastOk: number | null; lastError: string;
  latencyMedian: number | null; latencyMax: number | null;
  plan: Plan | null; planAge: number | null; planFresh: boolean;
  history: (Plan & { t: number; wall: number })[]; counts: Record<string, number>;
  input?: Snap;       // 最后一次发给模型的局面（09-21 起的大脑才有）
};
type Snap = {
  gameMinute?: number; gold?: number; lumber?: number; food?: string; halls?: number; hurtUnits?: number;
  workers?: { total?: number; onGold?: number; onLumber?: number; idle?: number };
  mines?: { workers: number; cap: number }[]; army?: Record<string, number>;
  heroes?: { type: string; hpPct: number }[]; enemyArmy?: number; enemyHalls?: number;
  ownStrength?: number; enemyStrength?: number; enemyRace?: string;
};
type Row = { inst: number; liveAge: number; fresh: boolean; gameT: number | null; coach: CoachLive | null; hasCoach: boolean;
  world: { gold?: number; lumber?: number; foodUsed?: number; foodCap?: number; workers?: number }; economy: { goldCrew?: number; lumberCrew?: number } | null };
type Server = { baseUrl: string; up: boolean; want: string; wantLoaded: boolean; loaded: { id: string; ctx: number | null; quant: string | null }[]; error: string };
type Overview = { server: Server; instances: Row[]; start: { running: boolean; log: string[]; ok: boolean | null } };

const POSTURE: Record<string, string> = { attack: tx("出击"), defend: tx("守家"), creep: tx("打野"), expand: tx("开矿"), recover: tx("回血整队"), hold: tx("攒兵") };

function ago(wall: number | null | undefined) {
  if (!wall) return "—";
  const s = Math.max(0, Date.now() / 1000 - wall);
  return s < 60 ? tx("{0} 秒前", s.toFixed(0)) : s < 3600 ? tx("{0} 分钟前", (s / 60).toFixed(0)) : tx("{0} 小时前", (s / 3600).toFixed(1));
}

// 每种情况一个说法 —— 「没动静」同时可能是四件事，页面上必须分得开
function stateOf(r: Row): { tone: string; label: string; desc: string } {
  if (!r.fresh) return { tone: "", label: tx("大脑没在跑"), desc: tx("brain_live.json {0} 秒没更新", r.liveAge.toFixed(0)) };
  if (!r.hasCoach) return { tone: "warning", label: tx("旧版大脑"), desc: tx("这一局开局时的大脑代码还没有顾问状态发布，下一局自动换上") };
  const c = r.coach!;
  switch (c.state) {
    case "off": return { tone: "", label: tx("已关闭"), desc: tx("enable_coach 关着：运营完全按规则表") };
    case "waiting": return { tone: "info", label: tx("等第一次"), desc: tx("开局 25 游戏秒后才开始问") };
    case "down": return { tone: "danger", label: tx("连不上"), desc: c.lastError || tx("最近一次请求失败") };
    default: return { tone: "success", label: tx("正常"), desc: c.planFresh ? tx("建议在有效期内，正在影响工人配比和出兵优先级") : tx("最近一次成功，但建议已过期（局面变了不再用）") };
  }
}

export default function Coach() {
  const { data, reload, error } = useFetch<Overview>("/api/coach", 3000);
  const meta = useApp((s) => s.meta);
  if (!data) return <div className="page"><FetchState error={error} onRetry={reload} /></div>;
  const rows = data.instances;
  const running = rows.filter((r) => r.fresh);
  const ok = running.filter((r) => r.coach?.state === "ok").length;
  const down = running.filter((r) => r.coach?.state === "down").length;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("运营顾问")}</h1>
          <p className="page-desc">{tx("本地大模型每 20 游戏秒看一次局面，给出工人配比、优先出什么、这一分钟的姿态。它只改目标和优先级，具体下令仍由规则层和毫秒层负责； 模型没起来 / 超时 / 答非所问时，这一层自动当作不存在。")}</p>
        </div>
        <div className="right row" style={{ gap: 8 }}>
          <Badge tone={ok ? "success" : ""}>{ok} {tx("个正常")}</Badge>
          {down > 0 && <Badge tone="danger">{down} {tx("个连不上")}</Badge>}
          <button className="btn sm" onClick={reload}><RefreshCw size={13} />{tx("刷新")}</button>
        </div>
      </div>
      <ServerCard s={data.server} start={data.start} onStarted={reload} />
      {!rows.length ? (
        <Empty icon={<Bot size={34} />} title={tx("没有在跑的新大脑")}>
          <div className="col gap8" style={{ alignItems: "center" }}>
            <span>{tx("运营顾问只接参考大脑（新大脑）。跑玩家方案或玩法模组的实例不用它。")}</span>
            <button className="btn sm" onClick={() => navigate("instances")}>{tx("去「实例与开局」")}</button>
          </div>
        </Empty>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(420px, 100%), 1fr))", marginTop: 16 }}>
          {rows.map((r) => <InstCard key={r.inst} r={r} meta={meta} />)}
        </div>
      )}
    </div>
  );
}

function ServerCard({ s, start, onStarted }: { s: Server; start: Overview["start"]; onStarted: () => void }) {
  return (
    <Card title={tx("本地模型服务")} icon={<Cpu size={15} />}
      actions={<span className="faint mono" style={{ fontSize: 12 }}>{s.baseUrl}</span>}>
      <div className="row wrap" style={{ gap: 14, alignItems: "center" }}>
        <span className={`dot ${s.up ? (s.wantLoaded ? "success" : "warning") : "danger"}`} />
        <b style={{ fontSize: 14 }}>{!s.up ? tx("LM Studio 没在跑") : s.wantLoaded ? tx("在跑，顾问要的模型已载入") : tx("在跑，但顾问要的模型没载入")}</b>
        <span className="faint" style={{ fontSize: 12.5 }}>{tx("顾问要的模型：")}<span className="mono">{s.want || "—"}</span></span>
        {!s.wantLoaded && (
          <button className="btn primary sm" disabled={start.running}
            onClick={() => action(() => api.post("/api/coach/llm-start"), tx("开始拉起本地模型（约 20 秒，载入约 21 GB 显存）")).then(onStarted)}>
            <Play size={13} />{start.running ? tx("拉起中…") : tx("拉起模型")}
          </button>
        )}
      </div>
      {s.error && <div className="faint" style={{ fontSize: 12, marginTop: 8 }}><CircleAlert size={12} /> {s.error}</div>}
      {s.loaded.length > 0 && (
        <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
          {s.loaded.map((m) => <Badge key={m.id} tone="info">{m.id}{m.quant ? ` · ${m.quant}` : ""}{m.ctx ? tx(" · 上下文 {0}", m.ctx) : ""}</Badge>)}
        </div>
      )}
      {(start.running || start.log.length > 0) && (
        <pre className="mono faint" style={{ fontSize: 11.5, marginTop: 10, whiteSpace: "pre-wrap" }}>
          {start.log.join("\n")}{start.ok === true ? tx("\n✔ 模型就绪") : start.ok === false ? tx("\n✘ 没起来（看上面的输出）") : ""}
        </pre>
      )}
    </Card>
  );
}

function InstCard({ r, meta }: { r: Row; meta: any }) {
  const [hist, setHist] = useState(false);
  const [seen, setSeen] = useState(false);
  const st = stateOf(r);
  const c = r.coach;
  const p = c?.plan;
  const n = c?.counts ?? {};
  const asked = n["顾问·问"] ?? 0, adopted = n["顾问·采纳"] ?? 0;
  const failed = Object.entries(n).filter(([k]) => k.startsWith("顾问·出错") || k === "顾问·超时" || k === "顾问·答复不是 JSON")
    .reduce((a, [, v]) => a + v, 0);
  const clamped = Object.entries(n).filter(([k]) => k.startsWith("顾问·钳位")).reduce((a, [, v]) => a + v, 0);
  const toggle = (on: boolean) => action(() => api.put(`/api/i/${r.inst}/brain/config`, { enable_coach: on }),
    tx("实例 {0} 运营顾问已{1}（2 秒内生效）", r.inst, on ? tx("打开") : tx("关闭")));
  return (
    <Card title={
      <span className="row" style={{ gap: 10 }}>
        <span className={`dot ${st.tone === "success" ? "success" : st.tone === "danger" ? "danger" : st.tone === "warning" ? "warning" : ""}`} />
        <span style={{ fontSize: 15 }}>{tx("实例")} {r.inst}</span>
        <Badge tone={st.tone} title={st.desc}>{st.label}</Badge>
        {c?.busy && <Badge tone="info">{tx("请求中")}</Badge>}
      </span>
    } actions={c && <label className="row" style={{ gap: 6, fontSize: 12 }} title={tx("brain_config.json 的 enable_coach，大脑 2 秒内读到")}>
      <Switch on={c.enabled} onChange={toggle} />{tx("顾问")}</label>}>
      <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>{st.desc}</div>
      {c && (
        <div className="kv" style={{ marginBottom: 10 }}>
          <span>{tx("最近成功")}</span><span>{ago(c.lastOk)}{c.planAge != null ? tx("（游戏内 {0} 秒前的建议）", c.planAge.toFixed(0)) : ""}</span>
          <span>{tx("延迟")}</span><span>{c.latencyMedian != null ? tx("中位 {0} 秒 · 最慢 {1} 秒", c.latencyMedian, c.latencyMax) : "—"}</span>
          <span>{tx("次数")}</span><span>{tx("问 {0} · 采纳 {1} · 失败", asked, adopted)} {failed}{clamped ? tx(" · 被钳位 {0}", clamped) : ""}</span>
          {c.lastError && <><span>{tx("最近错误")}</span><span className="mono" style={{ fontSize: 11.5, color: "var(--danger)" }}>{c.lastError}</span></>}
          <span>{tx("模型看到的")}</span><span>{c.input?.workers ? (
            <>{tx("农民")} {c.input.workers.total} {tx("· 采金")} {c.input.workers.onGold} {tx("· 伐木")} {c.input.workers.onLumber}
              {c.input.workers.idle != null ? tx(" · 没订单 {0}", c.input.workers.idle) : ""}
              <span className="faint">{tx("（金")} {c.input.gold} {tx("木")} {c.input.lumber} {tx("人口")} {c.input.food}）</span></>
          ) : "—"}</span>
        </div>
      )}
      {p ? <PlanView p={p} meta={meta} fresh={!!c?.planFresh} /> : c && <div className="faint" style={{ fontSize: 12.5 }}>{tx("还没有建议")}</div>}
      <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
        {c?.input && Object.keys(c.input).length > 0 && (
          <button className="btn ghost sm" onClick={() => setSeen(!seen)} title={tx("建议是基于这份局面给的 —— 建议看着不对时，先看是不是输入就错了")}>
            <Eye size={13} />{seen ? tx("收起局面") : tx("模型看到的局面")}</button>
        )}
        {c && c.history.length > 1 && (
          <button className="btn ghost sm" onClick={() => setHist(!hist)}><History size={13} />{hist ? tx("收起") : tx("最近 {0} 条建议", c.history.length)}</button>
        )}
      </div>
      {seen && c?.input && <SnapView s={c.input} meta={meta} />}
      {c && c.history.length > 1 && (
        <>
          {hist && (
            <table className="table" style={{ marginTop: 6, fontSize: 12 }}>
              <tbody>
                {[...c.history].reverse().map((h, i) => (
                  <tr key={i}>
                    <td className="mono faint nowrap">{Math.floor(h.t / 60)}:{String(Math.floor(h.t % 60)).padStart(2, "0")}</td>
                    <td className="nowrap">{POSTURE[h.posture] ?? (h.posture || "—")}</td>
                    <td className="nowrap faint">{tx("金{0} 木", h.gold ?? "—")}{h.lumber ?? "—"}</td>
                    <td>{h.diagnosis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Card>
  );
}

// 模型看到的局面（brain/coach.snapshot 发出去的那一份，原样摆出来）
function SnapView({ s, meta }: { s: Snap; meta: any }) {
  const army = Object.entries(s.army ?? {});
  return (
    <div className="kv" style={{ marginTop: 8, padding: "8px 10px", background: "var(--bg-sunken)", borderRadius: 8 }}>
      <span>{tx("时间")}</span><span>{tx("游戏 {0} 分 · 对手", s.gameMinute ?? "—")} {s.enemyRace ?? "?"}</span>
      <span>{tx("资源")}</span><span>{tx("金")} {s.gold} {tx("· 木")} {s.lumber} {tx("· 人口")} {s.food} {tx("· 大厅")} {s.halls}</span>
      <span>{tx("农民")}</span><span>{tx("共")} {s.workers?.total} {tx("· 采金")} {s.workers?.onGold} {tx("· 伐木")} {s.workers?.onLumber} {tx("· 没订单")} {s.workers?.idle ?? "—"}</span>
      <span>{tx("金矿")}</span><span>{(s.mines ?? []).map((m, i) => `${m.workers}/${m.cap}`).join("　") || "—"}</span>
      <span>{tx("部队")}</span><span>{army.length ? army.map(([k, v]) => `${unitName(k, meta)}×${v}`).join("　") : "—"}{s.hurtUnits ? tx("（半血以下 {0}）", s.hurtUnits) : ""}</span>
      <span>{tx("英雄")}</span><span>{(s.heroes ?? []).map((h) => `${unitName(h.type, meta)} ${h.hpPct}%`).join("　") || "—"}</span>
      <span>{tx("敌情")}</span><span>{tx("作战单位")} {s.enemyArmy} {tx("· 大厅")} {s.enemyHalls} {tx("· 强度 我")} {s.ownStrength} {tx("/ 敌")} {s.enemyStrength}</span>
    </div>
  );
}

function PlanView({ p, meta, fresh }: { p: Plan; meta: any; fresh: boolean }) {
  return (
    <div style={{ opacity: fresh ? 1 : 0.55 }}>
      <div className="section-label" style={{ marginTop: 0 }}><Sparkles size={12} />{tx("最近一条建议{0}", fresh ? "" : tx("（已过期）"))}
        <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>{tx("· {0} 秒出结果", p.latency)}</span></div>
      <div style={{ fontSize: 13.5, marginBottom: 8 }}>{p.diagnosis || "—"}</div>
      <div className="row wrap" style={{ gap: 6 }}>
        {p.posture && <Badge tone="accent">{tx("姿态：")}{POSTURE[p.posture] ?? p.posture}</Badge>}
        {(p.gold != null || p.lumber != null) && <Badge>{tx("采金 {0} · 伐木", p.gold ?? "—")} {p.lumber ?? "—"}</Badge>}
        {p.priority.map((code, i) => <Badge key={code} tone="info" title={code}>{i + 1}. {unitName(code, meta)}</Badge>)}
      </div>
      {p.avoid.length > 0 && <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>{tx("不要做：")}{p.avoid.join("；")}</div>}
    </div>
  );
}
