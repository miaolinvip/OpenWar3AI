import { Fragment, useEffect, useMemo, useState } from "react";
import { Camera, Clapperboard, Eye, EyeOff, Focus, Gauge, History, Map as MapIcon, Play, RotateCcw, SlidersHorizontal, Square, Video, Zap } from "lucide-react";
import MapView, { DEFAULT_LAYERS, type CamMark } from "../components/MapView";
import { Badge, Card, Confirm, Empty, Field, Switch, useFetch } from "../components/ui";
import { api, cam, I, action } from "../lib/api";
import { toast, useApp } from "../lib/store";
import { useCamState, useInstanceMap } from "../lib/hooks";
import { unitName } from "../lib/game";
import type { DirectorShot } from "../lib/types";
import { tx } from "../lib/i18n";

// ── 手动镜头（原样保留）──
const FIELDS: { key: string; label: string; min: number; max: number; step: number }[] = [
  { key: "distance", label: tx("镜头距离"), min: 500, max: 6000, step: 50 },
  { key: "pitch", label: tx("俯角"), min: 270, max: 350, step: 1 },
  { key: "fov", label: tx("视野角"), min: 20, max: 120, step: 1 },
  { key: "yaw", label: tx("朝向"), min: 0, max: 360, step: 1 },
  { key: "zoffset", label: tx("抬高"), min: -500, max: 2000, step: 10 },
];

// ── 导播参数：分组，全部都能调；写进 inst<N>/director-config.json，导播几秒内热加载，不用停 ──
type P = { key: string; label: string; min: number; max: number; step: number; unit?: string; desc?: string };
type Group = { title: string; hint: string; toggles?: [string, string][]; items: P[] };
const GROUPS: Group[] = [
  {
    title: tx("节奏"), hint: tx("多久换一次镜头"),
    items: [
      { key: "minShot", label: tx("一个镜头至少停"), min: 0.5, max: 15, step: 0.5, unit: tx("秒"), desc: tx("太短画面会跳（真打起来不受它限制）") },
      { key: "staleAfter", label: tx("没动静多久就走"), min: 2, max: 30, step: 0.5, unit: tx("秒") },
      { key: "switchRatio", label: tx("新的要高出几倍才换"), min: 1, max: 3, step: 0.1, unit: tx("倍") },
      { key: "glideMax", label: tx("比这近就滑过去"), min: 500, max: 8000, step: 100, desc: tx("更远直接切") },
    ],
  },
  {
    title: tx("景别"), hint: tx("中景 / 全景两档，只在切镜那一刻换档，落定后高度不动"), toggles: [["sizesEnabled", tx("开启景别（中景 / 全景）")]],
    items: [
      { key: "distance", label: tx("中景高度"), min: 1000, max: 5000, step: 50, desc: tx("对战默认 1650") },
      { key: "distWide", label: tx("全景高度"), min: 2500, max: 7000, step: 50, desc: tx("大团战 / 预判 / 开局定场") },
      { key: "wideCrowd", label: tx("多少人算大团战"), min: 6, max: 40, step: 1, unit: tx("人"), desc: tx("双方加起来") },
    ],
  },
  {
    title: tx("切换规矩"), hint: tx("一场仗一场仗地看：没在看仗时开打立刻切；正在看的仗打完才走，别处大很多才换，不来回跳"),
    toggles: [["anticipateQuiet", tx("有仗在打时不做预判切镜")]],
    items: [
      { key: "fightSwitch", label: tx("别处的仗热到几倍才换过去"), min: 1, max: 5, step: 0.1, unit: tx("倍") },
      { key: "fightMinHold", label: tx("正在看的仗至少看"), min: 0, max: 15, step: 0.5, unit: tx("秒") },
      { key: "pingpongS", label: tx("刚离开的仗多久内不切回去"), min: 0, max: 30, step: 1, unit: tx("秒") },
      { key: "fightEndS", label: tx("多久没动静算打完"), min: 1, max: 10, step: 0.5, unit: tx("秒") },
      { key: "creepFactor", label: tx("打野的热度打几折"), min: 0, max: 1, step: 0.05, unit: tx("倍") },
      { key: "meetMinFoes", label: tx("预判来敌至少几人才切"), min: 1, max: 15, step: 1, unit: tx("人") },
    ],
  },
  {
    title: tx("构图"), hint: tx("打仗时把参战的人全放在画面可见区（顶栏和操作栏之间）正中；不做小挪动，免得两段式抖动"),
    toggles: [["smoothMove", tx("部队/英雄走起来就锁定跟着走")]],
    items: [
      { key: "reaimDist", label: tx("整场挪了多远就重新对准"), min: 200, max: 3000, step: 50, desc: tx("有人出画不受它限制") },
      { key: "reaimSmall", label: tx("挪不到这么多就不对准"), min: 0, max: 1000, step: 25, desc: tx("防抖：略偏一点比抖一下好") },
      { key: "reaimMinS", label: tx("两次对准至少隔"), min: 0.3, max: 6, step: 0.1, unit: tx("秒"), desc: tx("太短画面会晃") },
      { key: "reaimSettleS", label: tx("刚对过焦多久内不补第二刀"), min: 0, max: 10, step: 0.5, unit: tx("秒"), desc: tx("除非很多人出画") },
      { key: "cutStay", label: tx("切镜落点这么近就不动镜头"), min: 0, max: 1500, step: 50 },
      { key: "hurtMemoryS", label: tx("挨打后几秒内还算参战"), min: 1, max: 12, step: 0.5, unit: tx("秒") },
      { key: "engageRadius", label: tx("挨打的人身边多远算参战"), min: 200, max: 1500, step: 50, desc: tx("打人的那一边") },
      { key: "fightRadius", label: tx("交火点多远内算同一场"), min: 500, max: 3000, step: 50 },
    ],
  },
  {
    title: tx("预判"), hint: tx("大脑的意图只在刚冒出来那几秒有分量"), toggles: [["brainSelect", tx("让大脑的意图参与选镜")]],
    items: [
      { key: "brainMinN", label: tx("意图里至少几个人才预判"), min: 1, max: 10, step: 1, unit: tx("人") },
      { key: "brainWeight", label: tx("意图的分量"), min: 0, max: 3, step: 0.1, unit: tx("倍") },
      { key: "brainFreshS", label: tx("满分持续"), min: 0.5, max: 10, step: 0.5, unit: tx("秒") },
      { key: "brainFadeS", label: tx("淡到零"), min: 2, max: 30, step: 0.5, unit: tx("秒") },
    ],
  },
  {
    title: tx("英雄"), hint: tx("按大脑意图提前跟英雄赶路；打起来就放开、框住整场仗"),
    toggles: [["heroFollow", tx("跟拍英雄")], ["heroIntent", tx("按大脑意图提前跟")], ["heroCreepLock", tx("英雄身边打起来也锁着英雄（不推荐）")]],
    items: [
      { key: "heroMaxHold", label: tx("最长跟多久"), min: 10, max: 90, step: 5, unit: tx("秒") },
      { key: "heroCooldown", label: tx("放开后多久不再跟同一个"), min: 0, max: 120, step: 5, unit: tx("秒") },
      { key: "heroBreakW", label: tx("多大的仗就放开去看"), min: 10, max: 100, step: 1, unit: tx("分"), desc: tx("英雄身边的也算") },
    ],
  },
  {
    title: tx("画面"), hint: tx("别给没视野、没主体的镜头"),
    toggles: [["ownOnly", tx("只切有我方参与的事")], ["fogCheck", tx("避开迷雾")], ["subjectCheck", tx("画面没活单位就切走")], ["correctDrift", tx("纠正镜头漂移")]],
    items: [
      { key: "subjectGraceS", label: tx("空镜最多容忍"), min: 1, max: 10, step: 0.5, unit: tx("秒") },
      { key: "fogGrace", label: tx("迷雾最多容忍"), min: 0.5, max: 5, step: 0.1, unit: tx("秒") },
    ],
  },
];

const KIND: Record<string, string> = { combat: tx("交火"), event: tx("事件"), anticipate: tx("预判"), idle: tx("巡视"), hero: tx("英雄") };
const SIZE: Record<string, string> = { wide: tx("全景"), mid: tx("中景"), close: tx("特写") };
const HOW: Record<string, string> = { glide: tx("滑"), cut: tx("切"), follow: tx("跟"), release: tx("放") };
const KIND_TONE: Record<string, string> = { combat: "danger", event: "warning", anticipate: "accent", idle: "", hero: "success" };

function clock(gt: number | null | undefined): string {
  if (gt == null) return "—";
  const s = Math.max(0, Math.round(gt));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function Director() {
  const inst = useApp((s) => s.inst);
  const selection = useApp((s) => s.selection);
  const snap = useApp((s) => s.snapshot);
  const meta = useApp((s) => s.meta);
  const brain = useApp((s) => s.brain);
  const mapInfo = useInstanceMap();
  const { cam: s, error: camError } = useCamState(1000);
  // 回看：'' = 当前这一局（最近 20 笔，2 秒一刷）；选了某一局就把那一局整本账拿来
  const [viewStamp, setViewStamp] = useState("");
  const { data: stampsData } = useFetch<{ stamps: { stamp: string; mtime: number; shots: number }[] }>(I("/director/stamps"), 10000, [inst]);
  const shotsUrl = viewStamp ? I(`/director/shots?stamp=${encodeURIComponent(viewStamp)}&limit=300`) : I("/director/shots?limit=20");
  const { data: shotsData } = useFetch<{ stamp: string | null; shots: DirectorShot[]; total: number }>(shotsUrl, viewStamp ? 0 : 2000, [inst, viewStamp]);
  const [takeover, setTakeover] = useState(false);
  const [fields, setFields] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [openRow, setOpenRow] = useState<number | null>(null);
  const d = s?.director;
  const cfg = (d?.config ?? {}) as Record<string, number | boolean>;
  const pinned = new Set(d?.pinned ?? []);
  const focus = selection[0] ? snap?.units.find((u) => u[0] === selection[0]) : undefined;
  const target = focus ? { addr: focus[0], x: focus[3], y: focus[4] } : undefined;
  const shots = shotsData?.shots ?? [];

  useEffect(() => {
    if (!s?.cam) return;
    setFields((f) => (Object.keys(f).length ? f : { distance: s.cam!.distance, pitch: s.cam!.pitch, fov: s.cam!.fov, yaw: s.cam!.yaw, zoffset: s.cam!.zOffset }));
  }, [s?.cam]);

  // 草稿：拖滑块时先显示草稿值；导播吃下新值（下一次轮询读回来一致）后草稿自然让位
  useEffect(() => {
    setDraft((dr) => {
      const next = { ...dr };
      for (const k of Object.keys(dr)) if (Number(cfg[k]) === dr[k]) delete next[k];
      return Object.keys(next).length === Object.keys(dr).length ? dr : next;
    });
  }, [d?.config]); // eslint-disable-line react-hooks/exhaustive-deps

  // 编号 = 表格里的行号（账本里的序号）。"放开跟随"那几行没有落点，跳过但不重新编号 ——
  // 否则地图上的 7 和表格里的 7 说的不是同一次
  const marks: CamMark[] = useMemo(() => shots
    .map((x, i) => ({ x, n: i + 1 }))
    .filter(({ x }) => x.ev !== "release" && x.x != null && x.y != null)
    .map(({ x, n }) => ({ x: x.x!, y: x.y!, n, kind: x.kind ?? "idle" })), [shots]);

  const directorCall = async (actionName: string, body: Record<string, unknown> = {}) => {
    try {
      const r = await api.post<{ ok: boolean; error?: string; holder?: number; note?: string }>(I(`/director/${actionName}`), body);
      if (!r.ok && r.holder) { setTakeover(true); return; }
      if (!r.ok) toast(r.error ?? tx("导播没成功"), "error");
      else toast(actionName === "start" ? tx("导播已启动") : actionName === "stop" ? tx("导播已停止") : (r.note ?? tx("参数已保存")), "success");
    } catch (e) { toast((e as Error).message, "error"); }
  };
  const setParam = (key: string, value: number | boolean) => directorCall("configure", { config: { [key]: value } });

  if (!s) return (
    <div className="page">
      <Empty icon={<Clapperboard size={36} />} title={tx("读不到镜头")}>
        <div className="col gap8" style={{ alignItems: "center" }}>
          <div>{tx("实例 {0} 的游戏没连上", inst)}{camError ? `：${camError}` : tx("，或者还在开局")}</div>
          <button className="btn sm" onClick={() => location.reload()}>{tx("重新加载页面")}</button>
        </div>
      </Empty>
    </div>
  );

  const counts = Object.entries(d?.counts ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("导播台")}</h1>
          <p className="page-desc">{tx("自动导播：找交火、按大脑的意图提前到位、跟英雄、中景 / 全景两档景别。每次切镜都记账，下面能看到为什么切到那里。镜头是纯本地表现，不进指令流、不影响对局。")}</p>
        </div>
      </div>

      {/* 第一排：镜头在哪 + 现在在干什么 */}
      <div className="grid g3" style={{ marginBottom: 14 }}>
        <Card title={tx("镜头在哪")} icon={<MapIcon size={15} />} style={{ gridColumn: "span 2" }}
          actions={<span className="faint" style={{ fontSize: 12 }}>{viewStamp ? tx("回看 {0}：编号 = 那一局的切镜顺序", viewStamp) : tx("白框 = 当前视野；编号 = 最近的切镜（越大越新）；双击小地图 = 看那儿")}</span>}>
          {/* 复用战场页的 .battle-map：画布的 100% 尺寸靠这条样式撑，不套它画布会按物理像素铺开、溢出卡片 */}
          <div className="battle-map" style={{ height: 380, borderRadius: "var(--radius)" }}>
            <MapView snapshot={snap} meta={meta} mapInfo={mapInfo} brain={brain}
              cam={!viewStamp && s.cam ? { x: s.cam.x, y: s.cam.y, distance: s.cam.distance } : null}
              selection={[]} layers={{ ...DEFAULT_LAYERS, threat: false, target: false, grid: false }}
              armedLabel={null} compact marks={marks} fitKey={`${inst}|${mapInfo?.path ?? ""}`}
              onSelect={() => {}} onPrimary={() => false} onSecondary={() => {}}
              onDouble={(x, y) => cam("look", { x, y, seconds: 0.4 })} />
          </div>
          <div className="row wrap" style={{ gap: 10, marginTop: 8, fontSize: 12 }}>
            {Object.entries(KIND).map(([k, v]) => <span key={k} className="row" style={{ gap: 4 }}><span style={{ width: 9, height: 9, borderRadius: 9, background: ({ combat: "#ef4444", event: "#f59e0b", anticipate: "#a855f7", idle: "#94a3b8", hero: "#facc15" } as Record<string, string>)[k] }} />{v}</span>)}
          </div>
        </Card>
        <Card title={tx("自动导播")} icon={<Clapperboard size={15} />} actions={d?.running ? <Badge tone="success">{tx("运行中")}</Badge> : <Badge>{tx("已停止")}</Badge>}>
          <div className="kv" style={{ marginBottom: 12 }}>
            <span>{tx("托管在")}</span><span>{s.directorHost === "external" ? tx("独立进程 pid {0}", s.directorHolderPid) : s.directorHost === "console" ? tx("控制台") : tx("无")}</span>
            <span>{tx("在看")}</span><span>{d?.shot ? <><Badge tone={KIND_TONE[String(d.shot.kind)] ?? ""}>{KIND[String(d.shot.kind)] ?? String(d.shot.kind)}</Badge> {tx("停了 {0} 秒", d.heldFor)}</> : "—"}</span>
            <span>{tx("为什么")}</span><span>{d?.why || "—"}</span>
            <span>{tx("景别")}</span><span>{d?.size ? <>{SIZE[d.size]}{d.curDistance ? <span className="faint mono"> {Math.round(d.curDistance)}</span> : null}{d.sizeWhy ? <span className="faint">（{d.sizeWhy}）</span> : null}</> : "—"}</span>
            <span>{tx("模式")}</span><span>{d?.mode === "hero" ? tx("跟英雄") : d?.mode === "auto" ? tx("按事件切") : d?.mode ?? "—"}</span>
            <span>{tx("状态")}</span><span>{d?.note || "—"}</span>
            <span>{tx("这一局")}</span><span>{d ? tx("切 {0} · 滑 {1} · 跟 {2}", d.stats.cuts ?? 0, d.stats.glides ?? 0, d.stats.creeps ?? 0) : "—"}</span>
            <span>{tx("账本")}</span><span className="mono">{d?.ledger?.stamp ? tx("{0} · {1} 拍 · {2} 笔", d.ledger.stamp, d.ledger.ticks, d.ledger.shots) : "—"}{d?.ledger?.error ? <span className="danger"> {d.ledger.error}</span> : null}</span>
            <span>{tx("一拍用时")}</span><span className="mono">{d?.lastTickS != null ? tx("{0} 秒", d.lastTickS) : "—"}</span>
          </div>
          <div className="row">
            <button className="btn primary" onClick={() => directorCall("start")}><Play size={14} />{tx("启动")}</button>
            <button className="btn" onClick={() => directorCall("stop")}><Square size={13} />{tx("停止")}</button>
          </div>
          {s.directorHost === "external" && <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>{tx("导播在 director_inst.py 里跑。调参数不用接管，直接生效；只有在这里「启动」才会接管。")}</div>}
        </Card>
      </div>

      {/* 第二排：最近 20 次切镜 */}
      <Card title={viewStamp ? tx("切镜记录（回看）") : tx("最近的切镜")} icon={<History size={15} />} style={{ marginBottom: 14 }}
        actions={<div className="row" style={{ gap: 10 }}>
          <span className="faint mono" style={{ fontSize: 12 }}>{shotsData?.stamp ? tx("账本 {0} · 共 {1} 笔{2}", shotsData.stamp, shotsData.total, viewStamp ? "" : tx("，显示最近 20")) : tx("还没有账本（导播跑过一拍才有）")}</span>
          <select className="input sm" value={viewStamp} onChange={(e) => { setViewStamp(e.target.value); setOpenRow(null); }} title={tx("回看以前的某一局")}>
            <option value="">{tx("当前这一局")}</option>
            {(stampsData?.stamps ?? []).map((x) => <option key={x.stamp} value={x.stamp}>{x.stamp}{tx("（{0} 笔）", x.shots)}</option>)}
          </select>
        </div>}>
        {shots.length === 0 ? <div className="faint">{tx("这一局还没切过镜头。")}</div> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>#</th><th>{tx("游戏时间")}</th><th>{tx("怎么动")}</th><th>{tx("看什么")}</th><th>{tx("景别")}</th><th>{tx("为什么切")}</th><th>{tx("那里是什么")}</th><th>{tx("分数")}</th><th>{tx("上一个停了")}</th><th>{tx("落选")}</th></tr></thead>
              <tbody>
                {[...shots].reverse().map((x, i) => {
                  const n = shots.length - i;
                  const open = openRow === n;
                  return (
                    <Fragment key={n}>
                      <tr onClick={() => setOpenRow(open ? null : n)} style={{ cursor: x.rivals?.length ? "pointer" : undefined }}>
                        <td className="mono">{n}</td>
                        <td className="mono">{clock(x.gt)}</td>
                        <td>{HOW[x.ev === "cut" ? (x.how ?? "cut") : x.ev] ?? x.ev}</td>
                        <td>{x.kind ? <Badge tone={KIND_TONE[x.kind] ?? ""}>{KIND[x.kind] ?? x.kind}{x.src === "intent" ? tx("·意图") : ""}</Badge> : "—"}</td>
                        <td>{x.size ? SIZE[x.size] ?? x.size : "—"}</td>
                        <td>{x.reason || "—"}</td>
                        <td style={{ maxWidth: 360 }}>{x.why || "—"}</td>
                        <td className="mono">{x.score != null && x.score < 900 ? x.score.toFixed(1) : "—"}</td>
                        <td className="mono">{x.heldPrev != null ? `${x.heldPrev}s` : "—"}</td>
                        <td className="mono">{x.rivals?.length ? `${x.rivals.length} ▾` : "—"}</td>
                      </tr>
                      {open && x.rivals?.length ? (
                        <tr><td /><td colSpan={9}>
                          <div className="col" style={{ gap: 4, fontSize: 12 }}>
                            {x.rivals.map((r, k) => <div key={k} className="row" style={{ gap: 8 }}><Badge tone={KIND_TONE[r.kind] ?? ""}>{KIND[r.kind] ?? r.kind}</Badge><span className="mono">{r.score}</span><span>{r.why}</span><span className="faint mono">({r.x}, {r.y})</span></div>)}
                          </div>
                        </td></tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* 第三排：参数（全部）+ 计数器 */}
      <div className="grid g3" style={{ marginBottom: 14 }}>
        <Card title={tx("导播参数")} icon={<SlidersHorizontal size={15} />} style={{ gridColumn: "span 2" }}
          actions={<span className="faint" style={{ fontSize: 12 }}>{tx("改完几秒内生效，不用停导播；")}<Badge tone="accent">{tx("人调过")}</Badge> {tx("的项自动调参不会再动")}</span>}>
          <div className="col" style={{ gap: 16 }}>
            {GROUPS.map((g) => (
              <div key={g.title}>
                <div className="section-label">{g.title} <span className="faint" style={{ textTransform: "none" }}>· {g.hint}</span></div>
                {g.toggles && (
                  <div className="row wrap" style={{ gap: 18, marginBottom: 8 }}>
                    {g.toggles.map(([k, label]) => (
                      <label key={k} className="row" style={{ gap: 8 }}>
                        <Switch on={Boolean(cfg[k])} onChange={(v) => setParam(k, v)} />{label}{pinned.has(k) && <Badge tone="accent">{tx("人调过")}</Badge>}
                      </label>
                    ))}
                  </div>
                )}
                <div className="grid g3" style={{ gap: "10px 18px" }}>
                  {g.items.map((c) => {
                    const cur = draft[c.key] ?? Number(cfg[c.key] ?? c.min);
                    const pending = draft[c.key] != null;
                    return (
                      <Field key={c.key} desc={c.desc}
                        label={<span className="row">{c.label}{pinned.has(c.key) && <Badge tone="accent">{tx("人调过")}</Badge>}<span className="right mono accent">{Number.isFinite(cur) ? +cur.toFixed(2) : "—"}{c.unit ? ` ${c.unit}` : ""}{pending ? <span className="faint"> {tx("待生效")}</span> : null}</span></span>}>
                        <input type="range" min={c.min} max={c.max} step={c.step} value={cur}
                          onChange={(e) => setDraft({ ...draft, [c.key]: Number(e.target.value) })}
                          onPointerUp={(e) => setParam(c.key, Number((e.target as HTMLInputElement).value))}
                          onKeyUp={(e) => setParam(c.key, Number((e.target as HTMLInputElement).value))} />
                      </Field>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card title={tx("规则走了几次")} icon={<Gauge size={15} />}
          actions={<span className="faint" style={{ fontSize: 12 }}>{tx("这一局 · 没出现 = 那条路没走过")}</span>}>
          {counts.length === 0 ? <div className="faint">{tx("还没有计数（新的一局刚开始，或者导播是旧版本）。")}</div> : (
            <div className="col" style={{ gap: 3, fontSize: 12, maxHeight: 620, overflowY: "auto" }}>
              {counts.map(([k, v]) => (
                <div key={k} className="row" style={{ gap: 8 }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={k}>{k}</span>
                  <span className="mono">{v}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* 第四排：手动镜头 + 画面（原样保留） */}
      <div className="grid g3">
        <Card title={tx("手动镜头")} icon={<Camera size={15} />} style={{ gridColumn: "span 2" }}
          actions={<span className="faint mono" style={{ fontSize: 12 }}>{s.cam ? `(${Math.round(s.cam.x)}, ${Math.round(s.cam.y)})` : ""}</span>}>
          <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>{tx("导播开着的时候，手动移开的镜头会被纠偏拉回来；要长时间手动操作，先停导播。")}</div>
          <div className="grid g2" style={{ gap: "10px 22px" }}>
            {FIELDS.map((f) => (
              <Field key={f.key} label={<span className="row">{f.label}<span className="right mono accent">{Math.round(fields[f.key] ?? 0)}</span></span>}>
                <input type="range" min={f.min} max={f.max} step={f.step} value={fields[f.key] ?? f.min}
                  onChange={(e) => setFields({ ...fields, [f.key]: Number(e.target.value) })}
                  onMouseUp={(e) => cam("field", { field: f.key, value: Number((e.target as HTMLInputElement).value), seconds: 0.3 })} />
              </Field>
            ))}
          </div>
          <div className="section-label">{tx("预设机位")} {target ? <Badge tone="accent">{tx("主角：")}{unitName(String(focus?.[1]), meta)}</Badge> : <span className="faint" style={{ textTransform: "none" }}>{tx("（在战场选中一个单位当主角）")}</span>}</div>
          <div className="row wrap" style={{ gap: 6 }}>
            {s.presets.map((p) => <button key={p.key} className="btn sm" onClick={() => cam("preset", { name: p.key, target, seconds: 1.5 })}>{tx(p.label)}</button>)}
          </div>
          <div className="section-label">{tx("运镜")}</div>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn sm" onClick={() => cam("orbit", { target, degrees: 360, seconds: 8 })}><RotateCcw size={13} />{tx("环绕")}</button>
            <button className="btn sm" onClick={() => cam("vertigo", { target, seconds: 3 })}><Zap size={13} />{tx("希区柯克变焦")}</button>
            <button className="btn sm" onClick={() => cam("dolly", { target, to: 900, seconds: 3 })}>{tx("推近")}</button>
            <button className="btn sm" onClick={() => cam("crane", { to: 2400, seconds: 4 })}>{tx("升降")}</button>
            <button className="btn sm" onClick={() => cam("whip", { degrees: 120, seconds: 0.3 })}>{tx("甩镜")}</button>
            <button className="btn sm" onClick={() => cam("sweep", { target, degrees: 60, seconds: 4 })}>{tx("横摇")}</button>
            {s.sequences.map((q) => <button key={q.key} className="btn sm" onClick={() => cam("sequence", { name: q.key, target })}><Video size={13} />{tx(q.label)}</button>)}
            <button className="btn sm danger" onClick={() => cam("stop")}><Square size={12} />{tx("停止运镜")}</button>
          </div>
          <div className="row wrap" style={{ gap: 6, marginTop: 12 }}>
            <button className="btn sm" disabled={!target} onClick={() => target && cam("follow", { target })}><Focus size={13} />{tx("跟随主角")}</button>
            <button className="btn sm" onClick={() => cam("release")}>{tx("取消跟随")}</button>
            <button className="btn sm" onClick={() => cam("reset", { seconds: 0.5 })}><RotateCcw size={13} />{tx("恢复游戏镜头")}</button>
            <button className="btn sm" onClick={() => cam("bounds", { margin: 3000 })}>{tx("放开镜头边界")}</button>
          </div>
        </Card>
        <Card title={tx("画面")} icon={<Eye size={15} />}>
          <Field inline label={tx("隐藏游戏界面")} desc={tx("拉远拍全景时收起 HUD")}><Switch on={s.ui.hidden} onChange={(v) => cam("ui", { show: !v, seconds: 0.3 })} /></Field>
          <Field inline label={tx("干净画面")} desc={tx("只留小地图")}><Switch on={s.ui.clean} onChange={(v) => cam("clean", { on: v })} /></Field>
          <Field inline label={tx("拉远自动收界面")} desc={tx("超过 {0} 自动隐藏", Math.round(s.ui.threshold))}><Switch on={s.ui.auto} onChange={(v) => cam("autohide", { enabled: v })} /></Field>
          <Field inline label={tx("血条随镜头缩放")} desc={tx("镜头越远血条越短")}><Switch on={s.bars.width} onChange={(v) => cam("bars", { width: v })} /></Field>
          <Field inline label={tx("血条敌我配色")} desc={tx("我方绿、敌方红")}><Switch on={s.bars.colour} onChange={(v) => cam("bars", { colour: v })} /></Field>
          {s.error && <div className="danger" style={{ fontSize: 12, marginTop: 10 }}><EyeOff size={12} /> {s.error}</div>}
        </Card>
      </div>
      <Confirm open={takeover} title={tx("接管导播")} danger confirm={tx("接管并启动")}
        body={tx("实例 {0} 的导播正在独立进程（director_inst.py，pid {1}）里运行。接管会停掉那个进程，改由控制台托管。（只调参数不需要接管。）", inst, s.directorHolderPid)}
        onClose={() => setTakeover(false)} onConfirm={() => action(() => api.post(I("/director/start"), { takeover: true }), tx("已接管导播"))} />
    </div>
  );
}
