import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Crosshair, Pause, Play, Search, Swords, Timer } from "lucide-react";
import { Badge, Card, Empty, Seg, pageHidden, useShowMore } from "../components/ui";
import { api } from "../lib/api";
import { fmtTime } from "../lib/game";
import { useApp } from "../lib/store";
import { tx } from "../lib/i18n";

// 战斗决策明细（war3-modules/declog.py 写的 decisions.jsonl：毫秒层 + 大脑）。
// 09-21 新增：在后台逐条显示决策内容 —— 哪个单位、什么兵种（骑士还是大法师），带 # 后面的 ID；
//   这个单位在这个时间点怎么判定、执行了什么；决策依据的输入信息默认折叠，点开看详情。
//   重点是战斗相关的决策，每条都要显示从信息获取到执行的各段耗时。

type U = { id: number | null; name: string; type: string; addr: string; hp?: number; hpMax?: number };
type Rec = {
  wall: number; g?: number; src: string; act: string; units: U[]; target?: U | { x: number; y: number };
  why: string; inputs?: Record<string, unknown>; timing: { snapAge?: number; decide?: number; cmd?: number; total?: number; gap?: number };
};
type Resp = { rows: Rec[]; exists: boolean; now: number; offset?: number };

const MAX_ROWS = 1500;

function who(u: U) {
  const pct = u.hpMax ? Math.round((100 * (u.hp ?? 0)) / u.hpMax) : null;
  return (
    <span className="nowrap">
      <b>{tx(u.name)}</b>{u.id != null ? <span className="muted"> #{u.id}</span> : null}
      {pct != null ? <span className={pct < 35 ? "danger" : pct < 60 ? "warning" : "muted"}> {pct}%</span> : null}
    </span>
  );
}

function target(t: Rec["target"]) {
  if (!t) return <span className="muted">—</span>;
  if ("name" in t) return who(t as U);
  return <span className="muted">{tx("坐标 ({0}, {1})", t.x, t.y)}</span>;
}

function ms(v: number | undefined) {
  return v == null ? "—" : `${v.toFixed(v < 10 ? 1 : 0)} ms`;
}

function clock(wall: number) {
  const d = new Date(wall * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function val(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? tx("是") : tx("否");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function Decisions() {
  const inst = useApp((s) => s.inst);
  const [rows, setRows] = useState<Rec[]>([]);
  const [exists, setExists] = useState(true);
  const [src, setSrc] = useState<"" | "毫秒层" | "大脑">("");
  const [unit, setUnit] = useState("");
  const [q, setQ] = useState("");
  const [live, setLive] = useState(true);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const last = useRef<number | null>(null);
  const offset = useRef<number | null>(null);

  // 条件变了：清空重拉；之后每 1.5 秒只拉新写的。★ 09-26：带上次的字节偏移（offset），后端只读这之后的
  // （以前带 since，后端每次还是读 8 MB、解析 6000 条再筛）；上一次没回来不发下一次；页面藏起来就停。
  const key = `${inst}|${src}|${unit}|${q}`;
  useEffect(() => { setRows([]); last.current = null; offset.current = null; setOpen({}); }, [key]);
  useEffect(() => {
    let stop = false;
    let busy = false;
    const tick = async () => {
      if (!live || busy || pageHidden()) return;
      busy = true;
      const p = new URLSearchParams({ limit: "600" });
      if (src) p.set("src", src);
      if (unit.trim()) p.set("unit", unit.trim().replace(/^#/, ""));
      if (q.trim()) p.set("q", q.trim());
      if (last.current != null) p.set("since", String(last.current));
      if (offset.current != null) p.set("offset", String(offset.current));
      try {
        const r = await api.get<Resp>(`/api/i/${inst}/decisions?${p.toString()}`);
        if (stop) return;
        setExists(r.exists);
        if (r.offset != null) offset.current = r.offset;
        if (r.rows.length) {
          last.current = r.rows[r.rows.length - 1].wall;
          setRows((old) => [...old, ...r.rows].slice(-MAX_ROWS));
        }
      } catch { /* 下一轮再试 */ } finally { busy = false; }
    };
    tick();
    const h = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(h); };
  }, [key, live, inst, src, unit, q]);

  const all = useMemo(() => [...rows].reverse(), [rows]);
  const { shown, more } = useShowMore(all, 200, key);
  const lat = useMemo(() => {
    const byOne = (s: string) => rows.filter((r) => r.src === s && r.timing?.total != null).map((r) => r.timing.total as number).sort((a, b) => a - b);
    const med = (a: number[]) => (a.length ? a[Math.floor(a.length / 2)] : null);
    const p90 = (a: number[]) => (a.length ? a[Math.floor(a.length * 0.9)] : null);
    const m = byOne(tx("毫秒层")), b = byOne(tx("大脑"));
    return { mMed: med(m), mP90: p90(m), bMed: med(b), bP90: p90(b), mN: m.length, bN: b.length };
  }, [rows]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("战斗决策")}</h1>
          <p className="page-desc">
            {tx("每一条 = 哪个单位（中文名 #血条编号 血量）在什么时候、被哪一层、决定做什么、为什么。点一行看依据的数据和耗时明细。")}
            <br />
            <b>{tx("毫秒层")}</b>{tx("（拉残血、孤兵回撤、英雄自保、补攻击令、放技能、牧师、用物品）每 100 毫秒一轮；")}
            <b> {tx("大脑")}</b>{tx("（打野、出击、守家、英雄去留、支援交战）每 2 游戏秒一拍。 「耗时」列 = 快照读到时已停更多久 + 从拿到快照到做出决定 + 下命令本身；点开还能看到两轮之间的等待和最坏情况。")}
          </p>
        </div>
      </div>

      <div className="grid g2" style={{ gap: 12, marginBottom: 12 }}>
        <Card title={tx("毫秒层 耗时（从拿到数据到下令）")} icon={<Timer size={16} />}>
          {tx("中位")} <b>{ms(lat.mMed ?? undefined)}</b> {tx("· 90% 在")} <b>{ms(lat.mP90 ?? undefined)}</b> {tx("以内")} <span className="muted">{tx("（{0} 条）", lat.mN)}</span>
        </Card>
        <Card title={tx("大脑 耗时（从拿到数据到下令）")} icon={<Timer size={16} />}>
          {tx("中位")} <b>{ms(lat.bMed ?? undefined)}</b> {tx("· 90% 在")} <b>{ms(lat.bP90 ?? undefined)}</b> {tx("以内")} <span className="muted">{tx("（{0} 条）", lat.bN)}</span>
        </Card>
      </div>

      <Card title={tx("实例 {0} · 最近 {1} 条", inst, rows.length)} icon={<Swords size={16} />}
            actions={
              <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <Seg value={src} onChange={setSrc} options={[{ value: "", label: tx("全部") }, { value: "毫秒层", label: tx("毫秒层") }, { value: "大脑", label: tx("大脑") }]} />
                <div className="search"><Crosshair size={14} /><input className="input sm" style={{ width: 96 }} placeholder={tx("编号 #")} value={unit} onChange={(e) => setUnit(e.target.value)} /></div>
                <div className="search"><Search size={14} /><input className="input sm" style={{ width: 150 }} placeholder={tx("找决策/理由")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
                <button className="btn sm" onClick={() => setLive(!live)}>{live ? <><Pause size={13} />{tx("暂停")}</> : <><Play size={13} />{tx("继续")}</>}</button>
              </div>
            }>
        {!exists ? (
          <Empty icon={<Swords size={34} />} title={tx("这个实例还没有决策记录")}>
            {tx("记录从 09-21 23:25 之后开局的对局开始写（毫秒层和大脑每局开局时重新加载）。")}
          </Empty>
        ) : !shown.length ? (
          <Empty icon={<Swords size={34} />} title={tx("还没有符合条件的决策")} />
        ) : (
          <table className="table" style={{ fontSize: 13 }}>
            <thead>
              <tr><th style={{ width: 18 }} /><th>{tx("游戏时间")}</th><th>{tx("层")}</th><th>{tx("单位")}</th><th>{tx("决策")}</th><th>{tx("对谁 / 去哪")}</th><th>{tx("为什么")}</th><th>{tx("耗时")}</th></tr>
            </thead>
            <tbody>
              {shown.map((r, i) => {
                const k = `${r.wall}|${r.src}|${r.act}|${r.units[0]?.addr ?? i}`;   // 别用位置序号：新记录插在最上面，序号一挪展开的就换了一行
                const on = !!open[k];
                const t = r.timing?.total;
                return (
                  <Fragment key={k}>
                    <tr onClick={() => setOpen({ ...open, [k]: !on })} style={{ cursor: "pointer" }}>
                      <td>{on ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                      <td className="nowrap">{r.g != null ? fmtTime(r.g) : "—"}<div className="muted" style={{ fontSize: 11 }}>{clock(r.wall)}</div></td>
                      <td><Badge tone={r.src === "大脑" ? "info" : "success"}>{tx(r.src)}</Badge></td>
                      <td>{who(r.units[0])}{r.units.length > 1 ? <span className="muted"> {tx("等 {0} 个", r.units.length)}</span> : null}</td>
                      <td className="nowrap"><b>{r.act}</b></td>
                      <td>{target(r.target)}</td>
                      <td>{r.why}</td>
                      <td className={`nowrap ${t != null && t > 500 ? "danger" : t != null && t > 150 ? "warning" : ""}`}>{ms(t)}</td>
                    </tr>
                    {on && (
                      <tr>
                        <td />
                        <td colSpan={7}>
                          <div className="grid g-main-side" style={{ gap: 12 }}>
                            <div>
                              <div className="muted" style={{ marginBottom: 4 }}>{tx("依据的数据")}</div>
                              <table className="table" style={{ fontSize: 12 }}>
                                <tbody>
                                  {Object.entries(r.inputs ?? {}).map(([kk, v]) => (
                                    <tr key={kk}><td className="muted nowrap">{kk}</td><td>{val(v)}</td></tr>
                                  ))}
                                  {!r.inputs && <tr><td className="muted">{tx("（这条没有附带数据）")}</td></tr>}
                                </tbody>
                              </table>
                              {r.units.length > 1 && (
                                <div style={{ marginTop: 6 }}>
                                  <span className="muted">{tx("全部单位：")}</span>
                                  {r.units.map((u, j) => <span key={j} style={{ marginRight: 10 }}>{who(u)}</span>)}
                                </div>
                              )}
                            </div>
                            <div>
                              <div className="muted" style={{ marginBottom: 4 }}>{tx("耗时明细")}</div>
                              <table className="table" style={{ fontSize: 12 }}>
                                <tbody>
                                  <tr><td className="muted">{tx("① 游戏里发生 → 进快照")}</td><td>≤ 50 ms <span className="faint">{tx("（DLL 发布周期，没逐条量）")}</span></td></tr>
                                  <tr><td className="muted">{tx("② 上一轮 → 这一轮读快照")}</td><td>{ms(r.timing?.gap)} <span className="faint">{tx("（事件落在两轮之间最多再等这么久）")}</span></td></tr>
                                  <tr><td className="muted">{tx("③ 快照读到时已停更")}</td><td>{ms(r.timing?.snapAge)}</td></tr>
                                  <tr><td className="muted">{tx("④ 拿到快照 → 做出决定")}</td><td>{ms(r.timing?.decide)}</td></tr>
                                  <tr><td className="muted">{tx("⑤ 下命令本身")}</td><td>{ms(r.timing?.cmd)}</td></tr>
                                  <tr><td><b>{tx("③+④+⑤ 合计")}</b></td><td><b>{ms(r.timing?.total)}</b></td></tr>
                                  <tr><td className="muted">{tx("最坏情况（①~⑤ 全加）")}</td><td>{r.timing?.gap != null && r.timing?.total != null ? ms(50 + r.timing.gap + r.timing.total) : "—"}</td></tr>
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        {more}
      </Card>
    </div>
  );
}
