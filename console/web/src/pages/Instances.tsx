import { useEffect, useMemo, useState } from "react";
import { Bot, Check, CircleStop, Clapperboard, Cpu, Gamepad2, Map as MapIcon, Package, Play, Power, Puzzle, Search, Server, Skull, TriangleAlert } from "lucide-react";
import { Badge, BusyButton, Card, Confirm, Dialog, Empty, Field, Seg, Switch, useFetch } from "../components/ui";
import { api, action } from "../lib/api";
import { setInst, toast, useApp } from "../lib/store";
import { DIFFICULTY, RACE_COLORS, RACE_GLYPH, RACE_NAMES, aiSentence, fmtTime } from "../lib/game";
import type { InstanceStatus, MapEntry, SchemesOverview } from "../lib/types";
import { DEFAULT_MAP } from "../lib/hooks";
import { locale, localizeSchemes, tx } from "../lib/i18n";

const SPEEDS = [100, 150, 200, 300, 400, 500];
const HOURS = [6, 12, 24, 72];
const HUMAN_MODE = "human_vs_openwar3ai";
const TAKEOVER_PHASE: Record<string, string> = {
  waiting: tx("等待对局就绪"), pausing: tx("正在暂停原生 AI"), paused: tx("原生 AI 已暂停，等待 xwar3"),
  running: tx("xwar3 已接管 P1"), failed: tx("接管失败"), ended: tx("本局已结束"),
};

async function checkedStart(n: number, op: string) {
  const r = await api.post<any>(`/api/instances/${n}/${op}`, { hours: 24 });
  if (!r.ok) throw new Error(r.error ?? r.farm?.error ?? r.brain?.error ?? tx("启动失败"));
  return r;
}

// 这个实例有没有东西在跑（游戏 / 启动器 / farm / AI）—— 全部停止、开始测试面板的圆点都按它
function isRunning(i: InstanceStatus) {
  return i.game.alive || !!i.launcher || i.farm.length > 0 || i.brain.length > 0 || i.oldAi.length > 0 || (i.bot?.length ?? 0) > 0;
}
const hasAi = (i: InstanceStatus) => i.brain.length > 0 || i.oldAi.length > 0 || (i.bot?.length ?? 0) > 0;

export default function Instances() {
  const instances = useApp((s) => s.instances);
  const cur = useApp((s) => s.inst);
  const [showDead, setShowDead] = useState(false);
  const [stopAll, setStopAll] = useState(false);
  const running = instances.filter(isRunning);
  const list = instances.filter((i) => showDead || isRunning(i) || i.inst === cur);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1 className="page-title">{tx("实例与开局")}</h1><p className="page-desc">{tx("每个实例 = 一个 War3 游戏 + farm 守护（打完按下一局设置自动重开）+ 新大脑 AI。停掉的实例不会自己再开，要跑就再点「开始测试」。")}</p></div>
        <div className="right row" style={{ gap: 12 }}>
          <label className="row" style={{ gap: 8 }}><Switch on={showDead} onChange={setShowDead} />{tx("显示已停止的实例")}</label>
          <button className="btn danger" disabled={!running.length} onClick={() => setStopAll(true)}><Power size={14} />{tx("全部停止")}{running.length ? tx("（{0} 个在跑）", running.length) : ""}</button>
        </div>
      </div>
      <TestLauncher instances={instances} />
      {!list.length ? <Empty icon={<Server size={34} />} title={tx("没有在跑的实例")} /> : (
        <div className="col gap16">{list.map((i) => <InstanceCard key={i.inst} s={i} current={i.inst === cur} />)}</div>
      )}
      <Confirm open={stopAll} title={tx("全部停止")} danger confirm={tx("全部停止")} onClose={() => setStopAll(false)}
        body={tx("停掉 {0} 号的 farm 守护、AI 和游戏，并挂上停止牌 —— 之后这些号不会自己再开，要跑再点「开始测试」。镜头导播进程留着（没有游戏时它什么也不做）。", running.map((i) => i.inst).join("、"))}
        onConfirm={() => action(() => api.post("/api/instances/stop-all"), tx("已全部停止"))} />
    </div>
  );
}

function TestLauncher({ instances }: { instances: InstanceStatus[] }) {
  const byInst = useMemo(() => new Map(instances.map((i) => [i.inst, i])), [instances]);
  const [sel, setSel] = useState<number[]>([]);
  const [extra, setExtra] = useState("");
  const [hours, setHours] = useState(24);
  const [director, setDirector] = useState<"keep" | "on" | "off">("keep");   // 这次顺手把选中实例的导演台开关设成…
  const [busy, setBusy] = useState(false);
  const nums = useMemo(() => [...new Set([...Array.from({ length: 15 }, (_, k) => k + 1), ...instances.map((i) => i.inst), ...sel])].sort((a, b) => a - b), [instances, sel]);
  const toggle = (n: number) => setSel((s) => (s.includes(n) ? s.filter((x) => x !== n) : [...s, n].sort((a, b) => a - b)));
  const addExtra = () => {
    if (!extra) return;
    const n = parseInt(extra, 10);
    if (n >= 1 && n <= 30) { setSel((s) => (s.includes(n) ? s : [...s, n].sort((a, b) => a - b))); setExtra(""); }
    else toast(tx("实例号要在 1~30 之间"), "error");
  };
  const start = async () => {
    setBusy(true);
    toast(tx("依次启动 {0} 号（每个间隔 4 秒）…", sel.join("、")), "info");
    const r = await action(() => api.post<any>("/api/tests/start", { insts: sel, hours, ...(director === "keep" ? {} : { director: director === "on" }) }));
    setBusy(false);
    if (!r) return;
    for (const x of r.results ?? []) {
      const why = [x.error, x.farm?.error, x.brain?.error].filter(Boolean).join("；");
      const dir = x.director ? (x.directorStart?.ok === false ? tx("，导演台没起来：{0}", x.directorStart.error) : tx("，带导演台")) : "";
      const human = byInst.get(x.inst)?.nextGame.matchMode === HUMAN_MODE;
      toast(x.ok ? human ? tx("实例 {0} 正在准备陪练，请查看接管状态", x.inst) : tx("实例 {0} 已开始测试（约 15 秒进局{1}）", x.inst, dir) : tx("实例 {0} 没起全：{1}", x.inst, why), x.ok ? "success" : "error", 6000);
    }
    setSel([]);
  };
  return (
    <Card title={tx("开始测试")} icon={<Play size={15} />} style={{ marginBottom: 16 }}
      actions={<span className="faint" style={{ fontSize: 12 }}>{tx("每个编号 = 一个游戏（打完按下一局设置自动重开）+ 新大脑 AI；绿点 = 已经在跑")}</span>}>
      <div className="row wrap" style={{ gap: 6 }}>
        {nums.map((n) => {
          const s = byInst.get(n);
          const run = !!s && isRunning(s);
          return (
            <button key={n} className={`btn sm ${sel.includes(n) ? "primary" : ""}`} style={{ minWidth: 44 }} onClick={() => toggle(n)}
              title={(run ? tx("实例 {0} 在跑：{1}", n, aiSentence(s)) : s?.held ? tx("实例 {0} 已停止", n) : tx("实例 {0}", n)) + (s?.nextGame.director ? tx("；带导演台") : "")}>
              {n}{s?.nextGame.director && <Clapperboard size={11} style={{ marginLeft: 3 }} />}{run && <span className="dot success" style={{ marginLeft: 4 }} />}
            </button>
          );
        })}
        <input className="input sm" style={{ width: 76 }} placeholder={tx("其它号")} value={extra} onChange={(e) => setExtra(e.target.value.replace(/\D/g, ""))}
          onKeyDown={(e) => e.key === "Enter" && addExtra()} onBlur={addExtra} />
      </div>
      <div className="row wrap" style={{ gap: 14, marginTop: 14 }}>
        <span className="faint" style={{ fontSize: 12.5 }}>{tx("最多跑")}</span>
        <Seg value={hours} onChange={setHours} options={HOURS.map((h) => ({ value: h, label: tx("{0} 小时", h) }))} />
        <span className="faint" style={{ fontSize: 12.5 }} title={tx("导演台 = 自动运镜 + 头顶血条（敌军红色、长度随镜头缩放）。选「带 / 不带」会记到这几个实例的开关上，以后开始测试照这个")}>{tx("导演台")}</span>
        <Seg value={director} onChange={setDirector} options={[{ value: "keep", label: tx("按各自开关") }, { value: "on", label: tx("带") }, { value: "off", label: tx("不带") }]} />
        <button className="btn primary" disabled={!sel.length || busy} onClick={start}>
          <Play size={14} />{busy ? tx("启动中…") : sel.length ? tx("开始测试（{0} 号）", sel.join("、")) : tx("先选实例号")}
        </button>
        <span className="faint" style={{ fontSize: 12 }}>{tx("已经在跑的那一半不会重复起；停掉的号会摘掉停止牌。地图/种族/难度按各实例的「下一局设置」。")}</span>
      </div>
    </Card>
  );
}

type ProcRow = { kind: string; label: string; name: string; pid: number; started: number | null; note: string };

// 这个实例名下的进程，每个都能单独停（后端 kill-proc 只认 status 里列出来的 pid）
function procRows(s: InstanceStatus): ProcRow[] {
  const rows: ProcRow[] = [];
  const farmOn = s.farm.length > 0;
  if (s.game.alive) rows.push({ kind: "game", label: tx("游戏"), name: s.game.exe ?? "War3", pid: s.game.pid, started: s.game.started,
    note: farmOn ? tx("关掉这一局。farm 守护还在，会按下一局设置马上重开 —— 不想再开就用「停止实例」。") : tx("关掉这一局（没有 farm 守护，不会重开）。") });
  if (s.launcher) rows.push({ kind: "launcher", label: tx("启动器守护"), name: "war3_launch.exe", pid: s.launcher.pid, started: s.launcher.started,
    note: tx("游戏挂在它的 Job 里，停它游戏跟着退（同「关游戏」）。") });
  for (const f of s.farm) {
    const multi = f.range[0] !== f.range[1];
    rows.push({ kind: "farm", label: tx("farm 守护"), name: multi ? tx("管 {0}~{1} 号", f.range[0], f.range[1]) : tx("管 {0} 号", f.range[0]), pid: f.pid, started: f.started,
      note: tx("只停 farm：当前这局照打，打完不再自动重开{0}。", multi ? tx("（它管的 {0}~{1} 号都不再重开）", f.range[0], f.range[1]) : "") });
  }
  for (const b of s.brain) rows.push({ kind: "brain", label: tx("新大脑监督器"), name: "run_brain.py", pid: b.pid, started: b.started,
    note: tx("停掉新大脑：这局没人指挥、对局文件不写结局，之后的局也不接管；它拉起的毫秒层模块一起停。") });
  for (const b of s.bot ?? []) rows.push({ kind: "bot", label: tx("方案运行器"), name: b.scheme ?? "run_scheme.py", pid: b.pid, started: b.started,
    note: tx("停掉这个方案：这局没人指挥，之后的局也不接管。") });
  for (const m of s.modules) rows.push({ kind: "module", label: tx("毫秒层模块"), name: m.name, pid: m.pid, started: null,
    note: tx("只停 {0}：这局剩下的时间没有它；下一局监督器会重新拉起。", m.name) });
  for (const o of s.oldAi) rows.push({ kind: "oldAi", label: tx("老 AI"), name: "run-ai.ps1", pid: o.pid, started: o.started, note: tx("停掉老 AI 监督器和它的模块。") });
  for (const d of s.director) rows.push({ kind: "director", label: tx("镜头导播"), name: "director_inst.py", pid: d.pid, started: d.started, note: tx("停掉这个实例的自动运镜（导播台页可以再开）。") });
  return rows;
}

function InstanceCard({ s, current }: { s: InstanceStatus; current: boolean }) {
  const [confirm, setConfirm] = useState<null | "stop" | "end" | "brainStop">(null);
  const [kill, setKill] = useState<ProcRow | null>(null);
  const [pick, setPick] = useState(false);
  const procs = procRows(s);
  const needTest = !s.farm.length || !hasAi(s);
  const ng = s.nextGame;
  const human = ng.matchMode === HUMAN_MODE;
  const modeLocked = s.game.alive || !!s.launcher || hasAi(s) || (s.farm.length > 0 && !s.held);
  const map = ng.map ?? (s as any).map ?? DEFAULT_MAP;
  const race = ng.race ?? 1, enemy = ng.enemyRace ?? 0, diff = ng.difficulty ?? 2, speed = ng.speed ?? s.speed ?? 300;
  const setNext = (patch: Record<string, unknown>) => action(() => api.put(`/api/instances/${s.inst}/next-game`, patch), tx("下一局设置已保存"));
  const { data: info } = useFetch<any>(`/api/maps/info?path=${encodeURIComponent(map)}`, 0, [map]);
  return (
    <Card title={
      <span className="row" style={{ gap: 10 }}>
        <span className={`dot ${s.game.alive ? "success" : ""}`} />
        <span style={{ fontSize: 15 }}>{tx("实例 {0}", s.inst)}</span>
        {current && <Badge tone="accent">{tx("当前查看")}</Badge>}
        {s.game.alive ? <Badge tone="success">{tx("游戏在跑 · pid {0}", s.game.pid)}</Badge> : <Badge>{tx("游戏未运行")}</Badge>}
        {s.farm.length > 0 && <Badge tone="info">{tx("farm 守护")}{s.farm[0].keep ? tx("（自动重开）") : ""}</Badge>}
        {s.brain.length > 0 && <Badge tone="accent"><Bot size={11} />{tx("新大脑")}</Badge>}
        {(s.bot?.length ?? 0) > 0 && (s.schemeKind === "mod"
          ? <Badge tone="info" title={tx("玩法模组（run_scheme.py）")}><Puzzle size={11} />{tx("模组 {0}", s.bot![0].scheme ?? "?")}</Badge>
          : <Badge tone="accent" title={tx("玩家方案（run_scheme.py）")}><Bot size={11} />{tx("方案 {0}", s.bot![0].scheme ?? "?")}</Badge>)}
        {s.oldAi.length > 0 && <Badge>{tx("老 AI")}</Badge>}
        {s.director.length > 0 && <Badge tone="accent" title={tx("独立导播进程 {0}：自动运镜 + 头顶血条", s.director[0].pid)}><Clapperboard size={11} />{tx("导演台")}</Badge>}
        {s.held && <Badge tone="warning" title={tx("停止牌 inst{0}/hold.json{1}：farm 不重开、新大脑不接管，点「开始测试」摘掉", s.inst, s.held.at ? tx("（{0} 挂的）", new Date(s.held.at * 1000).toLocaleString(locale, { hour12: false })) : "")}>{tx("已停止 · 不会自己再开")}</Badge>}
      </span>
    } actions={!current && <button className="btn sm" onClick={() => setInst(s.inst)}>{tx("切到这个实例")}</button>}>
      <div className="grid g-side-main" style={{ gap: 22 }}>
        <div>
          <div className="section-label" style={{ marginTop: 0 }}><Gamepad2 size={12} />{tx("当前这一局")}</div>
          <div className="kv">
            <span>{tx("种族")}</span><span>{tx("{0} 对 {1}", s.game.race ? RACE_NAMES[s.game.race] : "—", s.game.enemyRace ? RACE_NAMES[s.game.enemyRace] : "—")}</span>
            <span>{tx("地图")}</span><span className="ellipsis">{((s as any).map ?? "—").split("\\").pop()}</span>
            <span>{tx("倍速")}</span><span>{s.speed ? `${s.speed / 100}×` : "—"}</span>
            <span>{tx("运行了")}</span><span>{s.game.started ? fmtTime(Date.now() / 1000 - s.game.started) : "—"}{tx("（墙钟）")}</span>
            <span>{tx("对局文件")}</span>{(s.bot?.length ?? 0) > 0
              ? <span className="faint" style={{ fontSize: 12 }}>{tx("玩家方案不写对局文件，战绩在「AI 方案」页")}</span>
              : <span className="mono ellipsis" style={{ fontSize: 11.5 }}>{s.match?.name ?? "—"}</span>}
            <span>{tx("毫秒层模块")}</span><span>{s.modules.length ? s.modules.map((m) => m.name.replace("mod_", "")).join(" · ") : "—"}</span>
            <span>{tx("导播")}</span><span>{s.director.length ? tx("独立进程 {0}", s.director[0].pid) : "—"}</span>
          </div>
          <div className="section-label">{tx("操作")}</div>
          {human && <div className="faint" style={{ marginBottom: 10 }}>
            <Badge tone={s.takeover?.phase === "failed" ? "danger" : s.takeover?.phase === "running" ? "success" : "info"}>
              {s.takeover ? TAKEOVER_PHASE[s.takeover.phase] ?? s.takeover.phase : tx("真人 P0 · xwar3 P1 · 全图感知")}
            </Badge>
            {s.takeover?.error && <div role="alert" style={{ marginTop: 6 }}>{s.takeover.error}</div>}
          </div>}
          <div className="row wrap" style={{ gap: 6 }}>
            {needTest && <BusyButton className="btn primary" title={tx("游戏 farm 守护 + 选中的 AI 方案，缺哪个起哪个")} onClick={() => action(() => checkedStart(s.inst, "test-start"), human ? tx("正在准备陪练，请查看接管状态") : tx("开始测试：约 15 秒进局"))}><Play size={14} />{human ? tx("开始陪练") : tx("开始测试（游戏 + AI）")}</BusyButton>}
            {!s.farm.length && <BusyButton className="btn" title={tx("只起游戏和 farm 守护，不起 AI")} onClick={() => action(() => checkedStart(s.inst, "farm-start"), tx("farm 启动中，约 15 秒进局"))}><Gamepad2 size={14} />{tx("只起游戏")}</BusyButton>}
            {!hasAi(s) && <BusyButton className="btn" title={tx("按下面选的 AI 方案起 AI")} onClick={() => action(() => checkedStart(s.inst, "brain-start"), tx("AI 启动中"))}><Bot size={14} />{tx("只起 AI")}</BusyButton>}
            {(s.brain.length > 0 || (s.bot?.length ?? 0) > 0) && <button className="btn" onClick={() => setConfirm("brainStop")}><CircleStop size={14} />{tx("停止 AI")}</button>}
            {s.game.alive && <button className="btn" onClick={() => setConfirm("end")}><Skull size={14} />{tx("结束这一局")}</button>}
            {isRunning(s) && <button className="btn danger" onClick={() => setConfirm("stop")}><Power size={14} />{tx("停止实例")}</button>}
          </div>
          <SchemePick key={`${s.inst}:${s.scheme ?? ""}`} s={s} />
          {!human && <label className="row" style={{ gap: 10, marginTop: 12, alignItems: "flex-start", cursor: "pointer" }}>
            <Switch on={!!s.nextGame.director} onChange={(v) => action(() => api.post(`/api/instances/${s.inst}/director`, { on: v }),
              v ? tx("实例 {0} 带导演台{1}", s.inst, isRunning(s) ? tx("，导播几秒内就位") : tx("（开始测试时带上）")) : tx("实例 {0} 不带导演台，导播已停", s.inst))} />
            <span>
              <b><Clapperboard size={13} /> {tx("导演台")}</b>{tx("：自动运镜 + 头顶血条（敌军红色、长度随镜头缩放）")}
              <div className="faint" style={{ fontSize: 12 }}>
                {tx("开关会记住：开着的实例每次开始测试都带上，每局换游戏自动跟上，导播意外退出 10 秒内补起。一般只给一个实例开。{0}", s.director.length ? tx(" 现在：导播 pid {0}", s.director[0].pid) : s.nextGame.director && isRunning(s) ? tx(" 现在：正在拉起…") : "")}
              </div>
            </span>
          </label>}
          {procs.length > 0 && (
            <>
              <div className="section-label"><Cpu size={12} />{tx("进程（可以单独停）")}</div>
              <table className="table">
                <tbody>
                  {procs.map((p) => (
                    <tr key={`${p.kind}-${p.pid}`}>
                      <td className="nowrap">{p.label}</td>
                      <td className="mono faint nowrap" style={{ fontSize: 11.5 }}>{p.name}</td>
                      <td className="mono nowrap">{p.pid}</td>
                      <td className="faint nowrap">{p.started ? fmtTime(Date.now() / 1000 - p.started) : ""}</td>
                      <td className="num"><button className="btn ghost sm" title={p.note} onClick={() => setKill(p)}><CircleStop size={13} />{tx("停止")}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        <div>
          <Field label={tx("对局模式")}>
            <fieldset disabled={!!modeLocked} style={{ border: 0, padding: 0, margin: "0 0 8px" }}>
              <Seg value={ng.matchMode ?? "ai_vs_builtin"} onChange={(v) => setNext({ matchMode: v })}
                options={[{ value: "ai_vs_builtin", label: tx("AI 测试") }, { value: HUMAN_MODE, label: tx("玩家 vs xwar3") }]} />
            </fieldset>
          </Field>
          <div className="faint" style={{ fontSize: 12, marginBottom: 14 }}>{human ? tx("真人控制 P0，完整 xwar3 控制 P1；全图感知，正常资源消耗。切换模式前请停止实例。") : tx("切换对局模式前请停止实例。")}</div>
          <div className="section-label" style={{ marginTop: 0 }}><MapIcon size={12} />{tx("下一局设置")} <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>{tx("（结束当前局后生效）")}</span></div>
          <div className="row" style={{ gap: 14, alignItems: "stretch" }}>
            <button className="map-card" style={{ width: 150, flex: "none", padding: 0, textAlign: "left" }} onClick={() => setPick(true)}>
              {info?.preview ? <img src={`/api/maps/preview/${encodeURIComponent(info.preview)}`} alt="" /> : <div style={{ aspectRatio: "1", background: "#000" }} />}
              <div className="meta">
                <div className="name ellipsis">{info?.name ?? map.split("\\").pop()}</div>
                {/* 同名的图有好几份（原版 / AMAI / HvH……）：文件名才分得清选的是哪一份 */}
                <div className="map-file mono ellipsis" title={map}>{map.replace(/^.*?\\Maps\\/i, "")}</div>
                <div className="faint" style={{ fontSize: 11 }}>{info?.suggested ?? ""} {tx("· 点击换图")}</div>
              </div>
            </button>
            <div className="grow col gap12">
              <Field label={human ? tx("真人种族（P0）") : tx("我方种族")}>
                <RacePick value={race} onChange={(v) => setNext({ race: v })} />
              </Field>
              <div className="faint" style={{ fontSize: 12 }}><TriangleAlert size={12} /> {tx("四个种族都有建造表（照 AMAI Normal 战略）；多战略切换和反制兵种还在移植中")}</div>
              <Field label={human ? tx("xwar3 种族（P1）") : tx("对手种族")}>
                <RacePick value={enemy} onChange={(v) => setNext({ enemyRace: v })} allowMirror />
              </Field>
              <div className="row" style={{ gap: 16 }}>
                {!human && <Field label={tx("电脑难度")}><Seg value={diff} onChange={(v) => setNext({ difficulty: v })} options={[2, 3, 4].map((d) => ({ value: d, label: DIFFICULTY[d] }))} /></Field>}
                <Field label={tx("倍速")}><Seg value={speed} onChange={(v) => setNext({ speed: v })} options={SPEEDS.map((x) => ({ value: x, label: `${x / 100}×` }))} /></Field>
              </div>
              <div><button className="btn primary" disabled={!s.game.alive} onClick={() => setConfirm("end")}><Check size={14} />{tx("立即按新设置重开")}</button></div>
            </div>
          </div>
        </div>
      </div>
      <MapPicker open={pick} current={map} onClose={() => setPick(false)} onPick={(p) => { setNext({ map: p }); setPick(false); }} />
      <Confirm open={confirm === "stop"} title={tx("停止实例 {0}", s.inst)} danger confirm={tx("停止")} onClose={() => setConfirm(null)}
        body={tx("停掉 farm 守护、新大脑和模块，关闭游戏，并挂上停止牌 —— 之后这个号不会自己再开，要跑再点「开始测试」。镜头导播留着。")}
        onConfirm={() => action(() => api.post(`/api/instances/${s.inst}/stop`), tx("实例 {0} 已停止，不会自己再开", s.inst))} />
      <Confirm open={!!kill} title={kill ? tx("停止 {0}（pid {1}）", kill.label, kill.pid) : ""} danger confirm={tx("停止")} onClose={() => setKill(null)}
        body={kill?.note ?? ""}
        onConfirm={() => kill && action(() => api.post(`/api/instances/${s.inst}/kill-proc`, { pid: kill.pid }), tx("已停止 {0} {1}", kill.label, kill.pid))} />
      <Confirm open={confirm === "end"} title={tx("结束这一局")} confirm={tx("结束")} body={tx("动作 22 结束当前这局；有 farm 守护的话会按「下一局设置」自动开新的一局，AI 监督器接着接管。")} onClose={() => setConfirm(null)}
        onConfirm={() => action(() => api.post(`/api/instances/${s.inst}/end-game`), tx("已结束这一局"))} />
      <Confirm open={confirm === "brainStop"} title={tx("停止 AI")} danger confirm={tx("停止")} body={tx("当前这局会没有 AI 指挥（参考大脑的对局文件不写结局），之后的局也不接管。")} onClose={() => setConfirm(null)}
        onConfirm={() => action(() => api.post(`/api/instances/${s.inst}/brain-stop`), tx("AI 已停止"))} />
    </Card>
  );
}

// 这个实例用哪个 AI 方案：下拉选 + 「切换」（在跑就立刻接管这一局）。完整的方案管理（导入 / 复制 / 战绩）在「AI 方案」页
function SchemePick({ s }: { s: InstanceStatus }) {
  const { data: raw } = useFetch<SchemesOverview>("/api/schemes", 15000);
  const data = useMemo(() => localizeSchemes(raw), [raw]);
  const selected = s.scheme ?? "builtin/xwar3";
  const [pick, setPick] = useState<string>(selected);
  const [busy, setBusy] = useState(false);
  const running = s.game.alive || s.farm.length > 0;
  const list = (data?.schemes ?? []).filter((x) => !x.error);
  const cur = list.find((x) => x.ref === pick);
  const go = async () => {
    setBusy(true);
    const r = await action(() => api.post<any>(`/api/instances/${s.inst}/scheme`, { ref: pick, now: true }));
    setBusy(false);
    if (r && !r.ok) toast(r.error ?? r.start?.error ?? tx("没切成"), "error", 8000);
    else if (r) toast(r.applied === "now" ? tx("实例 {0} 换成「{1}」，接管当前这一局", s.inst, cur?.name ?? pick) : tx("实例 {0} 下次开始测试用「{1}」", s.inst, cur?.name ?? pick), "success", 5000);
  };
  if (s.nextGame.matchMode === HUMAN_MODE) return <div className="faint" style={{ marginTop: 12 }}>
    <b>{tx("AI 方案")}: builtin/xwar3</b><div>{tx("完整参考大脑：策略层 + 战斗微操 + 捡装备 + 侦察与战斗记录")}</div>
  </div>;
  return (
    <div style={{ marginTop: 12 }}>
      <div className="section-label"><Package size={12} />{tx("AI 方案")}</div>
      <div className="row wrap" style={{ gap: 8 }}>
        <select className="input" style={{ minWidth: 240 }} value={pick} onChange={(e) => setPick(e.target.value)}>
          {(["mine", "installed", "builtin"] as const).map((src) => {
            const items = list.filter((x) => x.source === src);
            return items.length ? (
              <optgroup key={src} label={{ mine: tx("我的方案"), installed: tx("已安装（别人的）"), builtin: tx("内置") }[src]}>
                {items.map((x) => <option key={x.ref} value={x.ref}>{x.name}{x.version ? ` v${x.version}` : ""}{!x.trusted ? tx("（未信任）") : ""}</option>)}
              </optgroup>
            ) : null;
          })}
        </select>
        <button className="btn sm primary" disabled={busy || (pick === selected && (s.schemeRunning ?? null) === pick) || (cur && !cur.trusted)}
          title={cur && !cur.trusted ? tx("别人的方案要先在「AI 方案」页确认信任") : running ? tx("停掉当前 AI，新方案马上接管这一局") : tx("记下来，开始测试时用")} onClick={go}>
          <Play size={13} />{busy ? tx("切换中…") : running ? tx("切换（立刻生效）") : tx("选定")}
        </button>
      </div>
      <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
        {tx("选的：")}<span className="mono">{selected}</span>{s.schemeRunning ? <> {tx("· 在跑：")}<span className="mono">{s.schemeRunning}</span></> : tx(" · 现在没有 AI 在跑")}
        {cur?.description ? ` · ${cur.description}` : ""}
      </div>
    </div>
  );
}

function RacePick({ value, onChange, allowMirror }: { value: number; onChange: (v: number) => void; allowMirror?: boolean }) {
  const opts = allowMirror ? [0, 1, 2, 3, 4] : [1, 2, 3, 4];
  return (
    <div className="race-pick" style={{ gridTemplateColumns: `repeat(${opts.length}, 1fr)` }}>
      {opts.map((r) => (
        <div key={r} className={`race ${value === r ? "on" : ""}`} onClick={() => onChange(r)}>
          <div className="glyph" style={{ background: r ? RACE_COLORS[r] : "var(--border-strong)" }}>{r ? RACE_GLYPH[r] : "="}</div>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>{r ? RACE_NAMES[r] : tx("镜像")}</div>
        </div>
      ))}
    </div>
  );
}

// ── 选地图 ────────────────────────────────────────────────────────────
// 09-26 选地图列表里有几项一模一样、无从区分 —— 本机有 4 份 Echo Isles（原版、注了 AMAI 的、固定成人族对电脑人族的 HvH、
// 别的图包里改过的），地图里写的名字、缩略图都一样。现在卡片上写文件名（和不在 FrozenThrone 下的文件夹），
// 同名的几份互相比（后端 console/server/mapinfo.py 的 fingerprint：各部分哈希、自带 AI 脚本、固定玩家设置），把哪里不一样标出来。
const PART_LABEL: Record<string, string> = {
  "war3map.j": tx("触发脚本改过"), "war3map.w3e": tx("地形不同"), "war3map.doo": tx("装饰物不同"),
  "war3mapUnits.doo": tx("预放单位不同"), "war3map.w3i": tx("地图设置不同"),
};
const SEAT_TYPE: Record<number, string> = { 1: tx("玩家"), 2: tx("电脑"), 3: tx("中立"), 4: tx("可营救") };

interface MapTag { text: string; tone?: string; title?: string }

const mapRel = (m: MapEntry) => m.rel ?? (!m.folder || m.folder === "." ? m.file : `${m.folder}\\${m.file}`);
const kb = (n: number) => `${Math.round(n / 1024)} KB`;
const seatsText = (m: MapEntry) => (m.seats ?? []).map((s) => `${SEAT_TYPE[s.type] ?? s.type} ${s.race ? RACE_NAMES[s.race] ?? s.race : tx("可选")}`).join(" · ");

/** a 和 b 是不是同一张图：一个可能是绝对路径、一个是相对地图目录的（下一局设置里存的两种都有） */
function samePath(a: string, b: string) {
  const x = a.toLowerCase(), y = b.toLowerCase();
  return x === y || x.endsWith("\\" + y) || y.endsWith("\\" + x);
}

/** 每张地图：同名（同人数）的有几份，和同名的其它几份哪里不一样 */
function mapVariants(maps: MapEntry[]): Map<string, { dupes: number; tags: MapTag[] }> {
  const groups = new Map<string, MapEntry[]>();
  for (const m of maps) {
    const k = `${(m.name ?? m.file).toLowerCase()}|${m.players ?? 0}`;
    groups.set(k, [...(groups.get(k) ?? []), m]);
  }
  const out = new Map<string, { dupes: number; tags: MapTag[] }>();
  for (const g of groups.values()) {
    // 每个部分在这一组里最常见的指纹 =「大多数版本是这样」；没有多数（两份各不一样）就不说谁改过
    const common: Record<string, string | undefined> = {};
    for (const part of Object.keys(PART_LABEL)) {
      const n = new Map<string, number>();
      for (const m of g) { const h = m.parts?.[part]; if (h) n.set(h, (n.get(h) ?? 0) + 1); }
      const best = [...n.entries()].sort((a, b) => b[1] - a[1])[0];
      common[part] = best && best[1] >= 2 ? best[0] : undefined;
    }
    for (const m of g) {
      const tags: MapTag[] = [];
      if (g.length > 1) {
        const twin = g.find((o) => o !== m && o.sha1 && o.sha1 === m.sha1);
        if (twin) tags.push({ text: tx("和 {0} 完全相同", mapRel(twin)), title: twin.path });
        if (m.aiKind) tags.push({ text: m.aiKind === "AMAI" ? tx("自带 AMAI 电脑 AI") : tx("自带 AI 脚本"), tone: "accent", title: (m.aiScripts ?? []).join(", ") });
        if (m.fixedSlots) tags.push({ text: tx("固定：{0}", seatsText(m)), tone: "warning", title: tx("玩家 / 电脑的位置和种族写死在地图里") });
        for (const [part, label] of Object.entries(PART_LABEL)) {
          if (part === "war3map.w3i" && m.fixedSlots) continue;       // 上面已经说了是哪种设置
          if (common[part] && m.parts?.[part] && m.parts[part] !== common[part]) tags.push({ text: label, tone: "info" });
        }
        if (!tags.length) tags.push({ text: /暴雪|blizzard/i.test(m.author ?? "") ? tx("原版") : tx("标准版"), tone: "success" });
      }
      out.set(m.path, { dupes: g.length, tags });
    }
  }
  return out;
}

function MapTags({ tags }: { tags: MapTag[] }) {
  return <>{tags.map((t, i) => <Badge key={i} tone={t.tone} title={t.title}>{t.text}</Badge>)}</>;
}

function MapPicker({ open, current, onClose, onPick }: { open: boolean; current: string; onClose: () => void; onPick: (p: string) => void }) {
  // 预览 / 指纹还在后台生成时每 3 秒再拉一次，生成完就不拉了
  const [poll, setPoll] = useState(0);
  const { data } = useFetch<{ maps: MapEntry[]; warm: { done: number; total: number }; root?: string }>(open ? "/api/maps" : null, poll, [open, poll]);
  useEffect(() => setPoll(data && data.warm.done < data.warm.total ? 3000 : 0), [data]);
  const [q, setQ] = useState("");
  const [players, setPlayers] = useState<number | 0>(2);
  const [folder, setFolder] = useState("");
  const [sel, setSel] = useState<string>(current);
  const all = data?.maps ?? [];
  const variants = useMemo(() => mapVariants(all), [all]);
  // 地图目录下的每个子文件夹（FrozenThrone、Download、别的图包……）和各有几张
  const folders = useMemo(() => {
    const n = new Map<string, number>();
    for (const m of all) n.set(m.folder, (n.get(m.folder) ?? 0) + 1);
    return [...n.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [all]);
  const list = useMemo(() => all.filter((m) =>
    (!players || m.players === players) && (!folder || m.folder === folder) &&
    (!q || `${m.name ?? ""} ${m.file} ${m.folder}`.toLowerCase().includes(q.toLowerCase()))), [all, q, players, folder]);
  const chosen = all.find((m) => samePath(m.path, sel));
  const cv = chosen ? variants.get(chosen.path) : undefined;
  return (
    <Dialog open={open} wide title={tx("选择地图")} onClose={onClose}
      footer={<><span className="faint mono ellipsis" style={{ marginRight: "auto", fontSize: 12 }} title={chosen?.path}>{chosen ? mapRel(chosen) : ""}</span><button className="btn" onClick={onClose}>{tx("取消")}</button><button className="btn primary" disabled={!chosen} onClick={() => chosen && onPick(mapRel(chosen))}>{tx("用这张图")}</button></>}>
      {data?.root && (
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
          {tx("地图目录：")}<span className="mono">{data.root}</span>{tx("（游戏目录下的 Maps，所有子文件夹都算；换游戏目录在「控制中心」或「设置」）")}
        </div>
      )}
      <div className="row wrap" style={{ marginBottom: 14, gap: 8 }}>
        <div className="search"><Search size={14} /><input className="input" placeholder={tx("搜地图名或文件名")} value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} /></div>
        <Seg value={players} onChange={setPlayers} options={[{ value: 0, label: tx("全部") }, ...[2, 4, 6, 8, 12].map((n) => ({ value: n, label: tx("{0} 人", n) }))]} />
        <select className="select" value={folder} onChange={(e) => setFolder(e.target.value)} title={tx("只看这个文件夹")}>
          <option value="">{tx("全部文件夹（{0}）", all.length)}</option>
          {folders.map(([f, n]) => <option key={f} value={f}>{f === "." ? tx("Maps 根目录") : f}（{n}）</option>)}
        </select>
        <Badge>{list.length} {tx("张")}</Badge>
        {data && data.warm.done < data.warm.total && <Badge tone="info">{tx("预览生成中 {0}/", data.warm.done)}{data.warm.total}</Badge>}
      </div>
      {chosen && (
        <div className="map-detail">
          <div className="row wrap" style={{ gap: 8 }}>
            <b>{chosen.name ?? chosen.file}</b>
            {chosen.author && <span className="faint">{chosen.author}</span>}
            {cv && cv.dupes > 1 && <Badge>{tx("同名 {0} 份", cv.dupes)}</Badge>}
            <MapTags tags={cv?.tags ?? []} />
          </div>
          <div className="faint mono" style={{ fontSize: 11.5 }}>
            {chosen.path} · {kb(chosen.size)}{chosen.mtime ? ` · ${new Date(chosen.mtime * 1000).toLocaleDateString(locale)}` : ""}
            {chosen.seats?.length ? ` · ${seatsText(chosen)}` : ""}
          </div>
          {chosen.description && <div className="faint" style={{ fontSize: 12.5 }}>{chosen.description}</div>}
        </div>
      )}
      <div className="maps">
        {list.map((m) => {
          const v = variants.get(m.path);
          const isCur = samePath(m.path, current), isDef = samePath(m.path, DEFAULT_MAP);
          return (
            <div key={m.path} className={`map-card ${chosen === m ? "on" : ""}`} onClick={() => setSel(m.path)} onDoubleClick={() => onPick(mapRel(m))}
              title={`${mapRel(m)}\n${kb(m.size)}${m.mtime ? " · " + new Date(m.mtime * 1000).toLocaleString(locale) : ""}`}>
              {m.preview ? <img loading="lazy" src={`/api/maps/preview/${encodeURIComponent(m.preview)}`} alt="" /> : <div style={{ aspectRatio: "1", background: "#111" }} />}
              <div className="meta">
                <div className="row" style={{ gap: 6 }}>
                  <div className="name ellipsis grow">{m.name ?? m.file}</div>
                  {v && v.dupes > 1 && <span className="faint" style={{ fontSize: 11, flex: "none" }} title={tx("同名的地图有 {0} 份，看下面的文件名和标签区分", v.dupes)}>×{v.dupes}</span>}
                </div>
                <div className="faint" style={{ fontSize: 11 }}>{m.players} {tx("人 · {0} ·", m.suggested ?? "")} {m.tft ? tx("冰封王座") : tx("混乱之治")}</div>
                <div className="map-file mono ellipsis">{mapRel(m)}</div>
                {(isCur || isDef || (v?.tags.length ?? 0) > 0) && (
                  <div className="map-tags">
                    {isCur && <Badge tone="accent">{tx("正在用")}</Badge>}
                    {isDef && !isCur && <Badge>{tx("默认")}</Badge>}
                    <MapTags tags={v?.tags ?? []} />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Dialog>
  );
}
