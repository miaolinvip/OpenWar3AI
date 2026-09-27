import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Search, Trophy } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Badge, Card, Empty, FetchState, Pager, Seg, Stat, useFetch } from "../components/ui";
import { LineChart } from "../components/Chart";
import { navigate, useApp } from "../lib/store";
import { DIFFICULTY, OUTCOME, RACE_COLORS, RACE_KEY, RACE_NAMES, STUCK_SUSPECT_DESC, TIER_LABEL, fmtTime, mapLabel } from "../lib/game";
import type { GroupStats, Kernel, MatchDetail, MatchOverview, MatchSummary } from "../lib/types";
import { locale, tx } from "../lib/i18n";

// 结局不可信的不进胜率：后端 matches.py 的 UNRELIABLE 按这个口径算（和 tuner.py 一致）

function kernelText(m: MatchSummary) {
  if (!m.brain) return m.planVer != null ? `v${m.planVer}` : "—";
  const k = m.kernel;
  if (!k) return tx("新大脑");
  return `${k.approx ? "≈" : ""}#${k.kernel}${k.dirty ? "+" : ""}`;
}

function kernelTitle(m: MatchSummary, kernels: Map<number, Kernel>) {
  if (!m.brain) return m.planVer != null ? tx("老 AI 策略层 v{0}（war3ai.ps1 的 $W3PlanVer）", m.planVer) : "";
  const k = m.kernel;
  if (!k) return tx("没有版本信息");
  const info = kernels.get(k.kernel);
  const when = info ? new Date(info.at * 1000).toLocaleString(locale, { hour12: false }) : "";
  return [
    tx("新大脑内核 #{0} · {1}{2}", k.kernel, k.kernelRev ?? info?.rev ?? "", when ? tx(" · {0} 提交", when) : ""),
    k.approx ? tx("≈：这局开局时还没记版本，按开局时间对齐到开局前最近一次大脑代码提交") : tx("开局时监督器记下的"),
    k.dirty ? tx("+：开局时工作区里还有没提交的大脑代码改动") : "",
    info ? tx("\n这一版改了：{0}", info.subject) : "",
  ].filter(Boolean).join("\n");
}

function Race({ race }: { race: string | null }) {
  const k = race ? RACE_KEY[race] : undefined;
  return k ? <b style={{ color: RACE_COLORS[k], fontWeight: 600 }}>{RACE_NAMES[k]}</b> : <span className="faint">?</span>;
}

function setupText(m: MatchSummary) {
  return [mapLabel(m.map), m.difficulty != null ? tx("{0}电脑", DIFFICULTY[m.difficulty] ?? tx("难度 {0}", m.difficulty)) : "", m.speed ? tx("{0} 倍速", m.speed / 100) : ""].filter(Boolean).join(" · ");
}

function ResultCell({ m }: { m: MatchSummary }) {
  if (!m.result) return m.live ? <Badge tone="info">{tx("进行中")}</Badge> : <Badge title={tx("对局文件没写结局就停了（监督器被杀或重启）")}>{tx("没收尾")}</Badge>;
  const o = OUTCOME[m.result.outcome];
  return (
    <span className="row gap4">
      <Badge tone={o?.tone} title={o?.desc}>{o?.label ?? m.result.outcome}</Badge>
      {m.endnote?.suspect && <Badge tone="danger" title={tx("{0}\n（最后一份快照后 {1} 秒写结局，那一拍游戏时钟走了 {2} 秒）", STUCK_SUSPECT_DESC, m.endnote.gap, m.endnote.advanced)}>{tx("误判")}</Badge>}
      {m.opening && m.opening.id !== "none" && <Badge tone="info" title={tx("开局谱 {0}（war3-brain/brain/openings）", m.opening.id)}>{m.opening.name}</Badge>}
      {m.tuned && (
        <Badge tone="accent" title={tx("{0}自动调参试的参数：{1}", m.tuning?.study ? tx("调参实验 {0} 第 {1} 组（{2}）\n", m.tuning.study, m.tuning.trial, m.tuning.tag ?? "") : "", JSON.stringify(m.config))}>
          {m.tuning?.study ? tx("调参 {0}·{1}", m.tuning.study, m.tuning.trial) : tx("调参")}
        </Badge>
      )}
    </span>
  );
}

function two(a: ReactNode, b: ReactNode) {
  return <div className="col" style={{ gap: 1 }}><div className="nowrap">{a}</div><div className="faint nowrap" style={{ fontSize: 11.5 }}>{b}</div></div>;
}

function devText(m: MatchSummary) {
  return [m.maxHalls ? tx("{0} 矿", m.maxHalls) : "", m.tier ? TIER_LABEL[m.tier] : "", m.result?.peakHeroes != null ? tx("英雄 {0}", m.result.peakHeroes) : "",
    m.result?.peakWorkers != null ? tx("工人 {0}", m.result.peakWorkers) : ""].filter(Boolean).join(" · ") || "—";
}

const SCOPE_LIMITS = [200, 600, 2000, 0];   // 最近多少局（0 = 全部）；统计和分组在后端算，全部也不卡
const PAGE = 50;                          // 表格一页画多少局（以前 600 局一次画出来，2.4 万个节点）

function loadPref<T extends string | number>(key: string, ok: (v: string) => boolean, parse: (v: string) => T, dflt: T): T {
  try { const v = localStorage.getItem(key); return v != null && ok(v) ? parse(v) : dflt; } catch { return dflt; }
}
function savePref(key: string, v: string | number) {
  try { localStorage.setItem(key, String(v)); } catch { /* 存不了就只在这次有效 */ }
}

function fmtStartOf(date: string | null, time: string | null) {
  return `${date?.slice(4, 6)}-${date?.slice(6)} ${time?.slice(0, 2)}:${time?.slice(2, 4)}`;
}

const pct = (s: GroupStats) => (s.reliable ? `${Math.round((s.wins / s.reliable) * 100)}%` : "—");
const mins = (s: GroupStats) => (s.done && s.avgMinutes != null ? tx("{0} 分", s.avgMinutes.toFixed(1)) : "—");
const peaks = (s: GroupStats) => (s.done ? `${Math.round(s.avgPeakArmy ?? 0)} / ${Math.round(s.avgPeakFoe ?? 0)}` : "—");

export default function Matches() {
  const inst = useApp((s) => s.inst);
  const [scope, setScopeState] = useState<"inst" | "all">(() => loadPref("farsight.matches.scope", () => true, (v) => (v === "all" ? "all" : "inst"), "inst" as "inst" | "all"));
  const [limit, setLimitState] = useState<number>(() => loadPref("farsight.matches.limit", (v) => SCOPE_LIMITS.includes(Number(v)), Number, 600));
  const { data: kernelList } = useFetch<Kernel[]>(`/api/kernels`, 60000, []);
  const kernels = useMemo(() => new Map((kernelList ?? []).map((k) => [k.n, k])), [kernelList]);
  const [open, setOpen] = useState<{ inst: number; name: string } | null>(null);
  const [filter, setFilter] = useState<"all" | "brain" | "old">("brain");
  const [kernelPick, setKernelPick] = useState<number | null>(null);
  const [openingPick, setOpeningPick] = useState<string | null>(null);
  const [instPick, setInstPick] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [qd, setQd] = useState("");                          // 停止输入 0.3 秒后才去查
  useEffect(() => { const h = setTimeout(() => setQd(q.trim()), 300); return () => clearTimeout(h); }, [q]);
  const [offset, setOffset] = useState(0);
  const filterKey = `${scope}|${inst}|${limit}|${filter}|${instPick}|${kernelPick}|${openingPick}|${qd}`;
  useEffect(() => { setOffset(0); }, [filterKey]);
  // 结局名是按当前语言翻译的：搜「胜利」时把对得上的结局码一起发过去
  const oc = qd ? Object.entries(OUTCOME).filter(([, o]) => o.label.toLowerCase().includes(qd.toLowerCase())).map(([k]) => k).join(",") : "";
  const params = new URLSearchParams({ scope, limit: String(limit), kind: filter, offset: String(offset), size: String(PAGE) });
  if (scope === "inst") params.set("inst", String(inst));
  if (instPick != null) params.set("pi", String(instPick));
  if (kernelPick != null) params.set("pk", String(kernelPick));
  if (openingPick != null) params.set("po", openingPick);
  if (qd) params.set("q", qd);
  if (oc) params.set("oc", oc);
  const { data: got, error: dataError, reload: reloadData } = useFetch<MatchOverview>(`/api/matches/overview?${params}`, 20000, []);
  // 翻页 / 改筛选时先留着上一份（同一范围），免得整页闪成「读取中」。范围认返回里带的 scope / inst ——
  // 切换那一下 useFetch 手里还是旧范围的数据，不能拿它冒充新范围（会闪出「0 局 / 没有对局」）。
  const sameView = (d: MatchOverview | null) => !!d && d.scope === scope && (scope === "all" || d.inst === inst);
  const prev = useRef<MatchOverview | null>(null);
  const fresh = sameView(got) ? got : null;
  if (fresh) prev.current = fresh;
  const data = fresh ?? (sameView(prev.current) ? prev.current : null);
  const stale = !!data && (data !== got);
  const setScope = (v: "inst" | "all") => {
    setScopeState(v);
    setInstPick(null); setKernelPick(null); setOpeningPick(null);
    savePref("farsight.matches.scope", v);
  };
  const setLimit = (v: number) => { setLimitState(v); savePref("farsight.matches.limit", v); };
  const st = data?.stats;
  const outcomes = data?.outcomes ?? [];
  const suspects = st?.suspects ?? 0;
  const rows = data?.rows ?? [];
  if (open) return <MatchView inst={open.inst} name={open.name} kernels={kernels} onBack={() => setOpen(null)} />;
  const limitLabel = (n: number) => (n ? tx("最近 {0} 局", n) : tx("全部"));
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("对局记录")}</h1>
          <p className="page-desc">
            {tx("{0}（matches/*.jsonl），点一行看资源/兵力曲线和 AI 动作时间线。鼠标停在版本、结局上看说明。", scope === "all"
              ? (limit ? tx("所有实例最近 {0} 局（按开局时间）", limit) : tx("所有实例的全部 {0} 局", data?.scanned ?? "…"))
              : (limit ? tx("实例 {0} 最近 {1} 局", inst, limit) : tx("实例 {0} 的全部 {1} 局", inst, data?.scanned ?? "…")))}
          </p>
        </div>
        <div className="right row" style={{ gap: 8, flexWrap: "wrap" }}>
          <Seg value={scope} onChange={setScope} options={[{ value: "inst", label: tx("实例 {0}", inst) }, { value: "all", label: tx("所有实例") }]} />
          <Seg value={limit} onChange={setLimit} options={SCOPE_LIMITS.map((n) => ({ value: n, label: limitLabel(n) }))} />
          <Seg value={filter} onChange={(v) => { setFilter(v); setKernelPick(null); }} options={[{ value: "brain", label: tx("新大脑") }, { value: "old", label: tx("老 AI") }, { value: "all", label: tx("全部") }]} />
        </div>
      </div>
      <div className="grid g4" style={{ marginBottom: 14, opacity: stale ? 0.6 : 1 }}>
        <Stat label={tx("对局数")} value={data?.total ?? "—"} sub={st ? tx("打完 {0}{1}", st.done, suspects ? tx(" · 误判结束 {0}", suspects) : "") : ""} />
        <Stat label={tx("胜")} value={<span className="success">{st?.wins ?? "—"}</span>} sub={st?.reliable ? tx("胜率 {0}%（{1} 局结局可信）", Math.round((st.wins / st.reliable) * 100), st.reliable) : "—"} />
        <Stat label={tx("平均时长")} value={st ? mins(st) : "—"} sub={tx("游戏时间")} />
        <Stat label={tx("平均峰值兵力 我 / 敌")} value={st?.done ? <><span className="success">{Math.round(st.avgPeakArmy ?? 0)}</span> / <span className="danger">{Math.round(st.avgPeakFoe ?? 0)}</span></> : "—"} />
      </div>
      {scope === "all" && !!data?.byInst.length && (
        <Card title={tx("按实例")} style={{ marginBottom: 14 }} bodyClass="card-body tight"
          actions={instPick != null ? <button className="btn ghost sm" onClick={() => setInstPick(null)}>{tx("看全部实例")}</button> : <span className="faint" style={{ fontSize: 12 }}>{tx("点一行只看这个实例")}</span>}>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>{tx("实例")}</th><th className="num">{tx("局数")}</th><th className="num">{tx("胜 / 可信")}</th><th className="num">{tx("胜率")}</th><th className="num">{tx("平均时长")}</th><th className="num">{tx("峰兵 我/敌")}</th><th>{tx("版本")}</th><th>{tx("最近一局")}</th></tr></thead>
              <tbody>
                {data.byInst.map((g) => {
                  const lo = g.last.outcome ? OUTCOME[g.last.outcome] : null;
                  return (
                    <tr key={g.inst} className={instPick === g.inst ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => setInstPick(instPick === g.inst ? null : g.inst)}>
                      <td className="nowrap"><b>{tx("实例")} {g.inst}</b>{g.last.live && <> <Badge tone="info">{tx("在打")}</Badge></>}</td>
                      <td className="num">{g.n}</td>
                      <td className="num"><span className="success">{g.wins}</span> / {g.reliable}</td>
                      <td className="num">{pct(g)}</td>
                      <td className="num">{mins(g)}</td>
                      <td className="num">{peaks(g)}</td>
                      <td className="mono nowrap faint">{g.kernels.length ? g.kernels.slice(0, 3).map((k) => `#${k}`).join(" ") + (g.moreKernels ? " …" : "") : "—"}</td>
                      <td className="nowrap"><span className="mono faint">{fmtStartOf(g.last.date, g.last.time)}</span> {g.last.outcome ? <Badge tone={lo?.tone}>{lo?.label ?? g.last.outcome}</Badge> : g.last.live ? <Badge tone="info">{tx("进行中")}</Badge> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {(data?.byOpening.length ?? 0) > 1 && (
        <Card title={tx("按开局谱")} style={{ marginBottom: 14 }} bodyClass="card-body tight"
          actions={openingPick != null ? <button className="btn ghost sm" onClick={() => setOpeningPick(null)}>{tx("看全部开局")}</button> : <span className="faint" style={{ fontSize: 12 }}>{tx("点一行只看这种开局（AI 决策页 / 设置页切换）")}</span>}>
          <table className="table">
            <thead><tr><th>{tx("开局谱")}</th><th className="num">{tx("局数")}</th><th className="num">{tx("胜 / 可信")}</th><th className="num">{tx("胜率")}</th><th className="num">{tx("平均时长")}</th><th className="num">{tx("峰兵 我/敌")}</th></tr></thead>
            <tbody>
              {data!.byOpening.map((g) => (
                <tr key={g.id} className={openingPick === g.id ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => setOpeningPick(openingPick === g.id ? null : g.id)}>
                  <td className="nowrap">{g.name} <span className="faint mono">{g.id}</span></td>
                  <td className="num">{g.n}</td>
                  <td className="num"><span className="success">{g.wins}</span> / {g.reliable}</td>
                  <td className="num">{pct(g)}</td>
                  <td className="num">{mins(g)}</td>
                  <td className="num">{peaks(g)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <div className="grid g2" style={{ marginBottom: 14, alignItems: "start" }}>
        <Card title={tx("按内核版本")} actions={kernelPick != null ? <button className="btn ghost sm" onClick={() => setKernelPick(null)}>{tx("看全部版本")}</button> : <span className="faint" style={{ fontSize: 12 }}>{tx("点一行只看这一版")}</span>} bodyClass="card-body tight">
          {!data?.byKernel.length ? <Empty title={tx("没有新大脑对局")} /> : (
            <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>{tx("版本")}</th><th className="num">{tx("局数")}</th><th className="num">{tx("胜 / 可信")}</th><th className="num">{tx("平均时长")}</th><th className="num">{tx("峰兵 我/敌")}</th><th>{tx("这一版改了什么")}</th></tr></thead>
              <tbody>
                {data.byKernel.map((g) => {
                  const info = kernels.get(g.kernel);
                  return (
                    <tr key={g.kernel} className={kernelPick === g.kernel ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => setKernelPick(kernelPick === g.kernel ? null : g.kernel)}>
                      <td className="mono nowrap" title={info ? `${info.rev} · ${new Date(info.at * 1000).toLocaleString(locale, { hour12: false })}` : ""}>#{g.kernel} <span className="faint">{info?.rev}</span></td>
                      <td className="num">{g.n}</td>
                      <td className="num"><span className="success">{g.wins}</span> / {g.reliable}</td>
                      <td className="num">{mins(g)}</td>
                      <td className="num">{peaks(g)}</td>
                      <td className="faint" title={info?.subject} style={{ maxWidth: 0, width: "100%" }}><div className="ellipsis">{info?.subject ?? ""}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          )}
        </Card>
        <Card title={tx("结局怎么判的")}>
          {!outcomes.length ? <Empty title={tx("还没有打完的局")} /> : (
            <div className="col gap12" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              {outcomes.map(([k, c]) => {
                const o = OUTCOME[k];
                return (
                  <div key={k} className="row" style={{ alignItems: "flex-start", gap: 10 }}>
                    <span style={{ minWidth: 92 }}><Badge tone={o?.tone}>{o?.label ?? k}</Badge> <b className="mono">{c}</b></span>
                    <span className="muted">
                      {o?.desc ?? k}
                      {k === "ended-stuck" && suspects > 0 && <><br /><span className="danger">{tx("其中 {0} 局标了「误判」：", suspects)}</span>{STUCK_SUSPECT_DESC}</>}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>
      <Card title={[instPick != null ? tx("实例 {0}", instPick) : "", kernelPick != null ? tx("内核 #{0}", kernelPick) : ""].filter(Boolean).join(" · ") + tx("{0}对局（{1}）", instPick != null || kernelPick != null ? tx(" 的") : "", data?.total ?? 0)}
        actions={<div className="search"><Search size={14} /><input className="input sm" placeholder={tx("局号、pid、版本、结局、种族…")} value={q} onChange={(e) => setQ(e.target.value)} /></div>}
        bodyClass="card-body tight">
        {!data ? <FetchState error={dataError} onRetry={reloadData} /> : !rows.length ? (
          <Empty icon={<Trophy size={30} />} title={tx("没有对局")}>
            <div className="col gap8" style={{ alignItems: "center" }}>
              <span>{tx("这里是参考大脑（新大脑）和老 AI 写的对局文件。玩家方案和玩法模组的战绩在「AI 方案」页。")}</span>
              <div className="row" style={{ gap: 6 }}>
                {filter !== "all" && <button className="btn sm" onClick={() => setFilter("all")}>{tx("看全部对局")}</button>}
                <button className="btn sm" onClick={() => navigate("schemes")}>{tx("去「AI 方案」看战绩")}</button>
              </div>
            </div>
          </Empty>
        ) : (
          <div style={{ overflowX: "auto", opacity: stale ? 0.6 : 1 }}>
            <table className="table">
              <thead><tr>
                <th>{tx("局号")}</th><th>{tx("开局")}</th><th>{tx("进程")}</th><th>{tx("版本")}</th><th>{tx("对阵 · 设置")}</th><th>{tx("结局")}</th>
                <th className="num">{tx("时长")}</th><th className="num">{tx("峰值兵力 我/敌")}</th><th>{tx("发展")}</th><th className="num">{tx("局末强度 我/敌")}</th>
              </tr></thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={`${m.inst}/${m.name}`} style={{ cursor: "pointer" }} onClick={() => setOpen({ inst: m.inst, name: m.name })} title={`inst${m.inst}/matches/${m.name}`}>
                    <td>{two(<b className="mono">#{m.game != null ? String(m.game).padStart(3, "0") : "—"}</b>, <span className="mono">{m.episode ?? ""}</span>)}</td>
                    <td>{two(<span className="mono">{m.date?.slice(4, 6)}-{m.date?.slice(6)} {m.time?.slice(0, 2)}:{m.time?.slice(2, 4)}:{m.time?.slice(4)}</span>, m.wallMinutes != null ? tx("墙钟 {0} 分", m.wallMinutes) : "")}</td>
                    <td title={[m.supervisorPid ? tx("监督器 pid {0}", m.supervisorPid) : "", m.runId ? `runId ${m.runId}` : ""].filter(Boolean).join(" · ")}>
                      {two(tx("实例 {0}", m.inst), m.pid ? `${m.exe ?? tx("游戏")} · pid ${m.pid}` : tx("没记进程"))}
                    </td>
                    <td title={kernelTitle(m, kernels)}>
                      {two(<Badge tone={m.brain ? "accent" : ""}>{kernelText(m)}</Badge>, m.brain ? (m.kernel?.kernelRev ?? "") : tx("老 AI"))}
                    </td>
                    <td>{two(<span className="row gap4"><Race race={m.race} /><span className="faint">vs</span><Race race={m.foeRace} /></span>, setupText(m) || tx("没记开局设置"))}</td>
                    <td><ResultCell m={m} /></td>
                    <td className="num">{m.result ? tx("{0} 分", m.result.minutes?.toFixed(1)) : "—"}</td>
                    <td className="num"><b className="success">{m.result?.peakArmy ?? "—"}</b> / <b className="danger">{m.peakFoeArmy ?? "—"}</b></td>
                    <td className="nowrap">{devText(m)}</td>
                    <td className="num">{m.endOwnStr ?? "—"} / {m.endFoeStr ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager offset={data.offset} size={PAGE} total={data.total} onChange={setOffset} unit={tx(" 局")} />
          </div>
        )}
      </Card>
    </div>
  );
}

function MatchView({ inst, name, kernels, onBack }: { inst: number; name: string; kernels: Map<number, Kernel>; onBack: () => void }) {
  const { data, loading, error, reload } = useFetch<MatchDetail>(`/api/i/${inst}/matches/${name}`, 0, [inst, name]);
  const [q, setQ] = useState("");
  const acts = useMemo(() => (data?.actions ?? []).filter((a) => !q || a.text.includes(q)), [data, q]);
  const st = data?.states ?? [];
  const labels = st.map((s) => fmtTime(s.t));
  const m = data?.summary;
  const res = m?.result;
  const o = res ? OUTCOME[res.outcome] : null;
  const info = m?.kernel ? kernels.get(m.kernel.kernel) : undefined;
  return (
    <div className="page">
      <div className="page-head">
        <button className="btn ghost icon" onClick={onBack}><ArrowLeft size={18} /></button>
        <div>
          <h1 className="page-title">{tx("第 {0} 局", m?.game != null ? String(m.game).padStart(3, "0") : "—")} <span className="faint mono" style={{ fontSize: 13, fontWeight: 400 }}>{name}</span></h1>
          <p className="page-desc">{m ? <>{res ? <Badge tone={o?.tone}>{o?.label ?? res.outcome}</Badge> : null} {m.brain ? tx("新大脑内核 {0}", kernelText(m)) : tx("老 AI v{0}", m.planVer)} · {res ? tx("{0} 分钟", res.minutes?.toFixed(1)) : m.live ? tx("进行中") : tx("没收尾")}</> : loading ? tx("读取中…") : ""}</p>
        </div>
      </div>
      {!data || !m ? <FetchState error={error} onRetry={reload} /> : (
        <>
          <div className="grid g3" style={{ marginBottom: 14, alignItems: "start" }}>
            <Card title={tx("这一局")}>
              <div className="kv">
                <span>{tx("局号")}</span><span className="mono">#{m.game != null ? String(m.game).padStart(3, "0") : "—"} · episode {m.episode ?? "—"}</span>
                <span>{tx("开局")}</span><span className="mono">{m.date?.slice(0, 4)}-{m.date?.slice(4, 6)}-{m.date?.slice(6)} {m.time?.slice(0, 2)}:{m.time?.slice(2, 4)}:{m.time?.slice(4)}</span>
                <span>{tx("对阵")}</span><span className="row gap4"><Race race={m.race} /><span className="faint">vs</span><Race race={m.foeRace} /></span>
                <span>{tx("地图")}</span><span>{mapLabel(m.map) || <span className="faint">{tx("没记")}</span>}</span>
                <span>{tx("电脑难度")}</span><span>{m.difficulty != null ? DIFFICULTY[m.difficulty] ?? m.difficulty : <span className="faint">{tx("没记")}</span>}</span>
                <span>{tx("倍速")}</span><span>{m.speed ? tx("{0} 倍", m.speed / 100) : <span className="faint">{tx("没记")}</span>}</span>
                <span>{tx("时长")}</span><span>{tx("游戏 {0} · 墙钟 {1} 分", res ? tx("{0} 分", res.minutes?.toFixed(1)) : "—", m.wallMinutes ?? "—")}</span>
              </div>
            </Card>
            <Card title={tx("谁跑的")}>
              <div className="kv">
                <span>{tx("实例")}</span><span>{inst}</span>
                <span>{tx("游戏进程")}</span><span className="mono">{m.pid ? `${m.exe ? `${m.exe} · ` : ""}pid ${m.pid}` : <span className="faint">{tx("没记")}</span>}</span>
                <span>{tx("监督器")}</span><span className="mono">{m.supervisorPid ? `pid ${m.supervisorPid}` : <span className="faint">{tx("没记")}</span>}{m.runId ? ` · runId ${m.runId}` : ""}</span>
                <span>{tx("版本")}</span><span>{m.brain ? <><Badge tone="accent">{kernelText(m)}</Badge> <span className="mono faint">{m.kernel?.kernelRev}</span></> : tx("老 AI 策略层 v{0}", m.planVer)}</span>
                {m.kernel?.approx && <><span /><span className="faint">{tx("≈ 开局时没记版本，按开局时间对齐")}</span></>}
                {m.kernel?.dirty && <><span /><span className="faint">{tx("+ 开局时还有没提交的大脑代码改动")}</span></>}
                {info && <><span>{tx("这一版")}</span><span className="muted" style={{ lineHeight: 1.6 }}>{info.subject}</span></>}
                {m.tuning?.study && <><span>{tx("调参实验")}</span><span>{m.tuning.study} {tx("· 第")} {m.tuning.trial} {tx("组")}{m.tuning.tag ? `（${m.tuning.tag}）` : ""}</span></>}
                {m.tuned && <><span>{tx("调参")}</span><span className="mono">{JSON.stringify(m.config)}</span></>}
              </div>
            </Card>
            <Card title={tx("结局")}>
              <div className="kv">
                <span>{tx("结局")}</span><span>{res ? <Badge tone={o?.tone}>{o?.label ?? res.outcome}</Badge> : m.live ? tx("进行中") : tx("没收尾")} {m.endnote?.suspect && <Badge tone="danger">{tx("误判")}</Badge>}</span>
                {o && <><span>{tx("怎么判的")}</span><span className="muted" style={{ lineHeight: 1.6 }}>{o.desc}</span></>}
                {m.endnote?.suspect && <><span /><span className="danger" style={{ lineHeight: 1.6 }}>{STUCK_SUSPECT_DESC}{tx("（最后一份快照后 {0} 秒写结局，那一拍游戏时钟走了 {1} 秒）", m.endnote.gap, m.endnote.advanced)}</span></>}
                <span>{tx("峰值兵力")}</span><span>{tx("我")} <b className="success">{res?.peakArmy ?? "—"}</b> {tx("/ 敌")} <b className="danger">{m.peakFoeArmy ?? "—"}</b></span>
                <span>{tx("发展")}</span><span>{devText(m)}</span>
                <span>{tx("强度")}</span><span>{tx("我方峰值 {0} · 局末 我 {1} / 敌", m.maxOwnStr ?? "—", m.endOwnStr ?? "—")} {m.endFoeStr ?? "—"}</span>
                <span>{tx("记录")}</span><span className="faint">{m.states} {tx("拍 · {0} 条动作 · {1} KB", m.actions, (m.size / 1024).toFixed(0))}</span>
              </div>
            </Card>
          </div>
          <div className="grid g2" style={{ marginBottom: 14 }}>
            <Card title={tx("资源")}>
              <LineChart labels={labels} series={[{ name: tx("金"), color: "#e2b53b", values: st.map((s) => s.gold) }, { name: tx("木"), color: "#3fa561", values: st.map((s) => s.lumber) }]} />
            </Card>
            <Card title={tx("兵力强度 · 人口")}>
              <LineChart labels={labels} series={[
                { name: tx("我方强度"), color: "#3b82f6", values: st.map((s) => s.ownStr) },
                { name: tx("敌方强度"), color: "#ef4444", values: st.map((s) => s.foeStr) },
                { name: tx("人口"), color: "#a78bfa", values: st.map((s) => s.foodUsed), dashed: true },
              ]} />
            </Card>
          </div>
          {!!data.statesTotal && data.statesTotal > st.length && (
            <p className="faint" style={{ fontSize: 12, margin: "-6px 0 14px" }}>{tx("曲线从 {0} 拍里均匀取了 {1} 个点。", data.statesTotal, st.length)}</p>
          )}
          <Card title={tx("AI 动作时间线（{0}）", acts.length) + (data.actionsCapped ? tx(" · 只读了前 {0} 条", data.actions.length) : "")}
            actions={<div className="search"><Search size={14} /><input className="input sm" placeholder={tx("筛选")} value={q} onChange={(e) => setQ(e.target.value)} /></div>} bodyClass="">
            <ActionTimeline acts={acts} />
          </Card>
        </>
      )}
    </div>
  );
}

/** 动作时间线：一局最多 4000 条，只画看得见的那几十条（以前全画出来，打开一局要等、滚动发卡）。 */
function ActionTimeline({ acts }: { acts: { t: number; text: string }[] }) {
  const parent = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: acts.length, getScrollElement: () => parent.current, estimateSize: () => 30, overscan: 20 });
  if (!acts.length) return <Empty title={tx("没有动作")} />;
  return (
    <div ref={parent} className="timeline" style={{ height: Math.min(560, acts.length * 30 + 2), overflow: "auto", display: "block" }}>
      <div style={{ height: v.getTotalSize(), position: "relative" }}>
        {v.getVirtualItems().map((it) => {
          const a = acts[it.index];
          return (
            <div key={it.key} data-index={it.index} ref={v.measureElement} className="tl-item"
              style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${it.start}px)` }}>
              <span className="tl-t">{fmtTime(a.t)}</span><span>{a.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
