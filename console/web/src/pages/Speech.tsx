// 头顶气泡（09-26 并进远见；以前是气泡服务自己的调试页 http://127.0.0.1:8872/，speech/speak_api.html）。
// 后端：GET /api/speech-overview?inst=、GET|POST /api/speech/{path}（console/server/speechview.py 转发给 speech/speak_api.py）。
// 气泡服务本身不变：茶话会 / 镜头对白 / 战况触发的线程都在它里面跑，远见只是界面。单位列表用远见自己的实时快照。
// 多标签页：选中的单位、正在看的分栏只在这个标签页；各功能的设置存在气泡服务里，别的标签页改了这边几秒内跟着变
// （输入框正在编辑时不覆盖，离开输入框才提交）。
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Bot, Cpu, Eraser, Megaphone, MessageSquareText, Play, Send, Settings2, Square, Users, Video, Zap } from "lucide-react";
import { Badge, Card, Empty, FetchState, Seg, Switch, useFetch } from "../components/ui";
import { api } from "../lib/api";
import { navigate, toast, useApp } from "../lib/store";
import { broadcast } from "../lib/tabs";
import { useUnits } from "../lib/hooks";
import { category, unitName } from "../lib/game";
import { locale, tx } from "../lib/i18n";

type Tab = "say" | "banter" | "camtalk" | "events" | "model";
type Feature = "banter" | "camtalk" | "events";

interface Overview {
  up: boolean;
  port?: number;
  instanceUp?: boolean;
  features?: Record<Feature, { running: boolean; error: string | null }>;
  autostart?: Partial<Record<Feature, number[]>>;
  avoidOverlap?: boolean;
  llm?: { config: Record<string, any> | null; reachable: boolean; models: string[] };
}

const FEATURE_NAME: Record<Feature, string> = { banter: tx("农民茶话会"), camtalk: tx("镜头对白"), events: tx("战况触发") };
const RUNNING_WORD: Record<Feature, string> = { banter: tx("在聊"), camtalk: tx("在说"), events: tx("在盯") };
const MODE: Record<string, string> = { announce: tx("报幕"), chat: tx("闲聊"), react: tx("接话"), topic: tx("话题"), alarm: tx("敌袭") };
const ROLE: Record<string, string> = { lead: tx("首领"), follow: tx("随从"), soldier: tx("士兵"), foe: tx("敌方首领") };
const ROLE_COLOR: Record<string, string> = { lead: "#E0B040", follow: "#9ab4cc", soldier: "#9ab4cc", foe: "#e06060" };

const time = (t: number) => new Date(t * 1000).toLocaleTimeString(locale);

/** 发给气泡服务；出错 toast 出来，返回 null */
async function speech<T = any>(path: string, body?: Record<string, unknown>): Promise<T | null> {
  try {
    return body === undefined ? await api.get<T>(`/api/speech/${path}`) : await api.post<T>(`/api/speech/${path}`, body);
  } catch (e) {
    const msg = (e as Error).message;
    toast(msg === "speech-down" ? tx("气泡服务没在跑") : msg, "error", 6000);
    return null;
  }
}

// ── 小部件：输入框编辑时不被轮询覆盖，离开输入框 / 回车才提交 ──────────────────
function NumField({ label, value, onCommit, min, max, step = 1, title, width = 70 }: {
  label: string; value: number | undefined; onCommit: (v: number) => void; min?: number; max?: number; step?: number; title?: string; width?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft == null) return;
    const v = Number(draft);
    setDraft(null);
    if (Number.isFinite(v) && v !== value) onCommit(v);
  };
  return (
    <label className="sp-field" title={title}>
      <span>{label}</span>
      <input className="input sm" type="number" value={draft ?? String(value ?? 0)} min={min} max={max} step={step} style={{ width }}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
    </label>
  );
}

function TextField({ label, value, onCommit, placeholder, grow }: {
  label: string; value: string | undefined; onCommit: (v: string) => void; placeholder?: string; grow?: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft == null) return;
    const v = draft;
    setDraft(null);
    if (v !== (value ?? "")) onCommit(v);
  };
  return (
    <label className={`sp-field ${grow ? "grow" : ""}`}>
      <span>{label}</span>
      <input className="input sm" value={draft ?? value ?? ""} placeholder={placeholder} style={grow ? { flex: 1, minWidth: 160 } : { width: 110 }}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
    </label>
  );
}

function Check({ label, checked, onChange, title }: { label: string; checked: boolean; onChange: (v: boolean) => void; title?: string }) {
  return (
    <label className="sp-check" title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{label}
    </label>
  );
}

/** 自动开启实例号：填了的实例，气泡服务重启、游戏每局重开、实例晚起来，都会自动开起来 */
function AutoField({ kind, ov, onDone }: { kind: Feature; ov: Overview; onDone: () => void }) {
  return (
    <TextField label={tx("自动开启实例号")} value={(ov.autostart?.[kind] ?? []).join(",")} placeholder={tx("如 5 或 5,7")}
      onCommit={async (v) => {
        const r = await speech<{ autostart: Record<string, number[]> }>("autostart", { [kind]: v });
        if (r) toast(tx("{0} 自动开启：{1}（5 秒内生效）", FEATURE_NAME[kind], (r.autostart[kind] ?? []).join(",") || tx("无")), "success");
        onDone();
      }} />
  );
}

function FeatureHead({ kind, ov, running, disabled, onToggle, children }: {
  kind: Feature; ov: Overview; running: boolean; disabled: boolean; onToggle: (on: boolean) => void; children?: ReactNode;
}) {
  return (
    <div className="sp-feature-head">
      <Switch on={running} disabled={disabled} onChange={onToggle} title={disabled ? tx("这个实例的游戏没在跑") : undefined} />
      <b>{FEATURE_NAME[kind]}</b>
      <Badge tone={running ? "success" : ""}>{running ? RUNNING_WORD[kind] : tx("没开")}</Badge>
      {(ov.autostart?.[kind] ?? []).length > 0 && (
        <Badge tone="info" title={tx("这几个实例会自动开")}>{tx("自动开启：{0}", (ov.autostart?.[kind] ?? []).join(","))}</Badge>
      )}
      <div className="right row wrap" style={{ gap: 8 }}>{children}</div>
    </div>
  );
}

function Talk({ children }: { children: ReactNode }) {
  return <div className="sp-talk">{children}</div>;
}

// ── 说话：选单位、写字、调样式、和 AI 对话 ─────────────────────────────────
const STYLE_DEFAULT = {
  font_px: 0, name_font_px: 0, max_width_px: 0, pad_px: -1, radius_px: -1, border_px: -1, type_ms: 45,
  text_color: "#f4f4f4", name_color: "#ffd060", bg_color: "#0c0e16", bg_alpha: 215, border_color: "#c49c40",
  font: "", side_px: 0, tail_len_px: -1, opacity: 100, tail: true, shadow: true, fade: true, duration_s: 0,
};
type StyleState = typeof STYLE_DEFAULT;

function loadStyle(): StyleState {
  try { return { ...STYLE_DEFAULT, ...JSON.parse(localStorage.getItem("farsight.speech.style") || "{}") }; } catch { return { ...STYLE_DEFAULT }; }
}

function styleBody(s: StyleState) {
  const a = Math.max(0, Math.min(255, s.bg_alpha)).toString(16).padStart(2, "0");
  return {
    font_px: s.font_px, name_font_px: s.name_font_px, max_width_px: s.max_width_px, pad_px: s.pad_px, radius_px: s.radius_px,
    border_px: s.border_px, type_ms: s.type_ms, text_color: s.text_color, name_color: s.name_color,
    bg_color: "#" + a + s.bg_color.slice(1), border_color: s.border_color, font: s.font.trim(),
    tail: s.tail, shadow: s.shadow, fade: s.fade, side_px: s.side_px, tail_len_px: s.tail_len_px, opacity: s.opacity / 100,
  };
}

function SayPanel({ inst, ov, reloadOv }: { inst: number; ov: Overview; reloadOv: () => void }) {
  const meta = useApp((s) => s.meta);
  const { units, me } = useUnits();
  const [scope, setScope] = useState<"mine" | "heroes" | "all">("mine");
  const [selected, setSelected] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [persona, setPersona] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ t: number; text: string; bad?: boolean }[]>([]);
  const [st, setSt] = useState<StyleState>(loadStyle);
  useEffect(() => setSelected([]), [inst]);
  useEffect(() => { try { localStorage.setItem("farsight.speech.style", JSON.stringify(st)); } catch { /* 存不了就这次有效 */ } }, [st]);
  const put = (k: keyof StyleState, v: any) => setSt((s) => ({ ...s, [k]: v }));
  const note = (t: string, bad = false) => setLog((l) => [{ t: Date.now() / 1000, text: t, bad }, ...l].slice(0, 60));

  const rows = useMemo(() => units
    .filter((u) => u.hp > 0 && u.owner < 16)
    .filter((u) => scope === "all" || (scope === "mine" ? u.owner === me : category(u.type, meta) === "hero"))
    .sort((a, b) => Number(category(b.type, meta) === "hero") - Number(category(a.type, meta) === "hero") || a.owner - b.owner),
  [units, me, meta, scope]);
  const alive = new Set(units.map((u) => u.addr));
  const sel = selected.filter((a) => alive.has(a));
  const toggle = (a: number) => setSelected((s) => (s.includes(a) ? s.filter((x) => x !== a) : [...s, a]));

  const say = async () => {
    if (!sel.length) return note(tx("先在左边选单位"), true);
    const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!lines.length) return note(tx("先写要说的话"), true);
    const bubbles = sel.map((u, i) => ({ unit: u, name, text: lines.length > 1 ? (lines[i] ?? lines[lines.length - 1]) : lines[0],
      duration_ms: Math.round(st.duration_s * 1000), style: styleBody(st) }));
    const r = await speech<{ receipts: string[] }>("say", { inst, bubbles });
    if (r) note(tx("说话 {0} 个：{1}", bubbles.length, r.receipts.join(" | ")));
  };
  const clearSel = async () => {
    for (const u of sel) await speech("clear", { inst, unit: u });
    note(tx("清掉 {0} 个", sel.length));
  };
  const clearAll = async () => {
    const r = await speech<{ receipt: string }>("clear", { inst });
    if (r) note(r.receipt);
  };
  const chat = async () => {
    const message = msg.trim();
    if (!message) return;
    if (!sel.length) return note(tx("先在左边选一个单位"), true);
    const u = units.find((x) => x.addr === sel[0]);
    const who = name || (u ? unitName(u.type, meta) : "");
    setMsg("");
    setBusy(true);
    note(tx("你：{0}", message));
    const r = await speech<{ reply: string; first_token_ms: number; total_ms: number }>("chat", {
      inst, unit: sel[0], name: who, persona, message, style: styleBody(st), stream: true, duration_ms: Math.round(st.duration_s * 1000) });
    setBusy(false);
    if (r) note(tx("{0}：{1}（首字 {2} ms，总 {3} ms）", who, r.reply, r.first_token_ms, r.total_ms));
  };
  const setAvoid = async (on: boolean) => {
    // 实例没在跑时不带实例号：设置照样存盘，那个实例的游戏起来后第一次说话时自动设好
    const r = await speech<{ applied: Record<string, string> }>("config", { ...(ov.instanceUp ? { inst } : {}), avoid_overlap: on });
    if (r) note(tx("避免遮挡 {0}：{1}", on ? tx("开") : tx("关"), Object.values(r.applied).join(" | ")));
    reloadOv();
  };

  const num = (k: keyof StyleState, label: string, min: number, max: number, step = 1, title?: string) => (
    <label className="sp-field" title={title}>
      <span>{label}</span>
      <input className="input sm" type="number" value={st[k] as number} min={min} max={max} step={step} style={{ width: 70 }}
        onChange={(e) => put(k, Number(e.target.value) || 0)} />
    </label>
  );
  const color = (k: keyof StyleState, label: string) => (
    <label className="sp-field"><span>{label}</span><input type="color" value={st[k] as string} onChange={(e) => put(k, e.target.value)} /></label>
  );

  return (
    <div className="grid g-main-side" style={{ alignItems: "start" }}>
      <Card title={tx("单位")} icon={<Users size={15} />} bodyClass="card-body tight"
        actions={<>
          <span className="faint" style={{ fontSize: 12 }}>{tx("已选 {0}", sel.length)}</span>
          {sel.length > 0 && <button className="btn sm ghost" onClick={() => setSelected([])}>{tx("取消选择")}</button>}
          <Seg value={scope} onChange={setScope} options={[{ value: "mine", label: tx("我方") }, { value: "heroes", label: tx("英雄") }, { value: "all", label: tx("全部") }]} />
        </>}>
        {!units.length ? <Empty title={tx("没连上游戏")}>{tx("这个实例的游戏没在跑，或者还没开局")}</Empty> : (
          <div className="sp-units">
            <table className="table">
              <thead><tr><th style={{ width: 24 }} /><th>{tx("名字")}</th><th>{tx("玩家")}</th><th className="num">{tx("血")}</th><th className="num">{tx("坐标")}</th></tr></thead>
              <tbody>
                {rows.map((u) => {
                  const on = sel.includes(u.addr);
                  return (
                    <tr key={u.addr} className={`sp-row ${on ? "sel" : ""}`} onClick={() => toggle(u.addr)}>
                      <td>{on ? "✔" : ""}</td>
                      <td>{unitName(u.type, meta)}{category(u.type, meta) === "hero" ? " ★" : ""} <span className="faint mono">{u.type}</span></td>
                      <td>{u.owner === me ? <b className="success">{tx("我方")}</b> : tx("玩家 {0}", u.owner)}</td>
                      <td className="num">{u.hp}/{u.hpMax}</td>
                      <td className="num mono faint">{Math.round(u.x)},{Math.round(u.y)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="col gap12">
        <Card title={tx("说话")} icon={<MessageSquareText size={15} />}>
          <div className="col gap8">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tx("显示名字：留空 = 不显示；写谁都行")} />
            <textarea className="input" rows={3} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={tx("要说的话。选了多个单位时一行一句，按顺序分给每个单位；只有一行就大家说同一句。")} />
            <div className="row wrap" style={{ gap: 6 }}>
              <button className="btn sm primary" onClick={say}><Play size={13} />{tx("让选中的单位说话")}</button>
              <button className="btn sm" onClick={clearSel} disabled={!sel.length}><Eraser size={13} />{tx("清掉选中的")}</button>
              <button className="btn sm" onClick={clearAll}><Eraser size={13} />{tx("全部清掉")}</button>
              <label className="sp-check right" title={tx("叠在一起时气泡往侧面 / 上面让开、尾巴斜着指回去（每个实例生效，存盘）")}>
                <Switch on={!!ov.avoidOverlap} onChange={setAvoid} />{tx("避免遮挡")}
              </label>
            </div>
            <details className="sp-style">
              <summary>{tx("样式（0 / -1 = 自动；本机记住）")}</summary>
              <div className="sp-grid">
                {num("font_px", tx("正文字号"), 0, 200)}
                {num("name_font_px", tx("名字字号"), 0, 200)}
                {num("max_width_px", tx("最大宽度"), 0, 1600)}
                {num("pad_px", tx("内边距"), -1, 200)}
                {num("radius_px", tx("圆角"), -1, 200)}
                {num("border_px", tx("边框粗细"), -1, 20, 0.5)}
                {num("type_ms", tx("打字（毫秒/字）"), 0, 2000)}
                {num("duration_s", tx("显示（秒）"), 0, 120, 0.5, tx("0 = 自动 3~5 秒"))}
                {num("side_px", tx("侧偏（像素）"), -800, 800, 1, tx("气泡主体往右（负数往左）偏，尾巴斜过去仍指着单位"))}
                {num("tail_len_px", tx("尾巴长"), -1, 600)}
                {color("text_color", tx("正文颜色"))}
                {color("name_color", tx("名字颜色"))}
                <label className="sp-field"><span>{tx("底色")}</span>
                  <input type="color" value={st.bg_color} onChange={(e) => put("bg_color", e.target.value)} />
                  <input type="range" min={0} max={255} value={st.bg_alpha} onChange={(e) => put("bg_alpha", Number(e.target.value))} style={{ width: 80 }} title={tx("底色不透明度")} />
                </label>
                {color("border_color", tx("边框颜色"))}
                <label className="sp-field"><span>{tx("字体")}</span>
                  <input className="input sm" value={st.font} placeholder={tx("微软雅黑")} onChange={(e) => put("font", e.target.value)} style={{ width: 110 }} /></label>
                <label className="sp-field"><span>{tx("不透明度")}</span>
                  <input type="range" min={5} max={100} value={st.opacity} onChange={(e) => put("opacity", Number(e.target.value))} style={{ width: 90 }} />
                  <span className="faint mono">{st.opacity}%</span></label>
                <div className="row" style={{ gap: 12 }}>
                  <Check label={tx("尾巴")} checked={st.tail} onChange={(v) => put("tail", v)} />
                  <Check label={tx("阴影")} checked={st.shadow} onChange={(v) => put("shadow", v)} />
                  <Check label={tx("淡出")} checked={st.fade} onChange={(v) => put("fade", v)} />
                </div>
                <button className="btn sm ghost" onClick={() => setSt({ ...STYLE_DEFAULT })}>{tx("恢复默认样式")}</button>
              </div>
            </details>
          </div>
        </Card>

        <Card title={tx("和 AI 对话")} icon={<Bot size={15} />}
          actions={<Badge tone={ov.llm?.reachable ? "success" : "danger"}>{ov.llm?.reachable ? tx("模型在线") : tx("模型连不上")}</Badge>}>
          <div className="col gap8">
            <input className="input" value={persona} onChange={(e) => setPersona(e.target.value)} placeholder={tx("人设：留空 = 默认（魔兽里并肩作战的角色，一两句口语）")} />
            <div className="row" style={{ gap: 6 }}>
              <input className="input grow" value={msg} onChange={(e) => setMsg(e.target.value)} placeholder={tx("你对它说…（回车发送）")}
                onKeyDown={(e) => e.key === "Enter" && !busy && chat()} />
              <button className="btn primary" disabled={busy} onClick={chat}>{busy ? <span className="spinner sm" /> : <Send size={14} />}{tx("发送")}</button>
            </div>
            <div className="faint" style={{ fontSize: 12 }}>{tx("本地模型回一句，第一个选中的单位说出来；同一个单位记得最近几轮对话。")}</div>
          </div>
        </Card>

        <Card title={tx("回执")} icon={<Zap size={15} />} bodyClass="card-body tight">
          {!log.length ? <Empty title={tx("还没有操作")} /> : (
            <div className="sp-log mono">
              {log.map((l, i) => <div key={i} className={l.bad ? "danger" : ""}><span className="faint">{time(l.t)}</span> {l.text}</div>)}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

// ── 农民茶话会 ────────────────────────────────────────────────────────
function BanterPanel({ inst, ov, reloadOv }: { inst: number; ov: Overview; reloadOv: () => void }) {
  const { data, error, reload } = useFetch<any>(`/api/speech/banter?inst=${inst}`, 2000, [inst]);
  const [topic, setTopic] = useState("");
  if (!data) return <FetchState error={error} onRetry={reload} />;
  const cfg = data.config || {};
  const post = async (body: Record<string, unknown>) => { await speech("banter", { inst, ...body }); reload(); reloadOv(); };
  const poke = async () => {
    if (!topic.trim()) return;
    if (await speech("banter/poke", { inst, topic: topic.trim() })) { toast(tx("话题：{0}", topic.trim()), "success"); setTopic(""); }
  };
  const colors: Record<string, string> = Object.fromEntries((data.cast || []).map((c: any) => [c.name, c.color]));
  return (
    <Card title={null} bodyClass="card-body">
      <div className="col gap12">
        <FeatureHead kind="banter" ov={ov} running={!!data.running} disabled={!ov.instanceUp} onToggle={(on) => post({ enabled: on })}>
          <AutoField kind="banter" ov={ov} onDone={reloadOv} />
        </FeatureHead>
        <div className="faint" style={{ fontSize: 12.5 }}>{tx("家里的工人按人设互相吐槽；每局开局报幕场次和上一局。设置改了从下一句起生效，会存盘；0 = 自动。")}</div>
        <div className="row wrap" style={{ gap: 10 }}>
          <NumField label={tx("间隔（秒）")} value={cfg.interval_s} min={2} max={120} step={0.5} onCommit={(v) => post({ interval_s: v })} />
          <NumField label={tx("每句字数")} value={cfg.max_chars} min={10} max={80} onCommit={(v) => post({ max_chars: v })} />
          <NumField label={tx("战绩来源实例")} value={cfg.stats_inst} min={0} max={64} title={tx("0 = 跟游戏同一个实例；测试实例没有战绩时填有战绩的实例号")} onCommit={(v) => post({ stats_inst: v })} />
          <NumField label={tx("每几句提一次高手")} value={cfg.pro_every} min={1} max={20} onCommit={(v) => post({ pro_every: v })} />
          <NumField label={tx("每句显示（秒）")} value={cfg.duration_s} min={0} max={30} step={0.5} onCommit={(v) => post({ duration_s: v })} />
          <NumField label={tx("字号")} value={cfg.font_px} min={0} max={120} onCommit={(v) => post({ font_px: v })} />
          <NumField label={tx("名字字号")} value={cfg.name_font_px} min={0} max={120} onCommit={(v) => post({ name_font_px: v })} />
        </div>
        <div className="row" style={{ gap: 6 }}>
          <input className="input grow" value={topic} onChange={(e) => setTopic(e.target.value)} onKeyDown={(e) => e.key === "Enter" && poke()}
            placeholder={tx("抛个话题 / 转一条弹幕，比如：你们为什么不去打野？")} disabled={!data.running} />
          <button className="btn sm" onClick={poke} disabled={!data.running}><Megaphone size={13} />{tx("抛给他们")}</button>
          <button className="btn sm" disabled={!data.running} onClick={async () => { if (await speech("banter/announce", { inst })) toast(tx("下一句报幕"), "success"); }}>{tx("现在报幕")}</button>
        </div>
        {data.facts && <pre className="sp-pre">{data.facts}</pre>}
        {data.last_error && <div className="danger" style={{ fontSize: 12.5 }}>{data.last_error}</div>}
        <div className="grid g2" style={{ alignItems: "start" }}>
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>{tx("角色")}</div>
            <table className="table">
              <thead><tr><th>{tx("名字")}</th><th>{tx("性格")}</th><th>{tx("心情")}</th><th>{tx("单位")}</th><th className="num">{tx("句")}</th></tr></thead>
              <tbody>
                {(data.cast || []).filter((c: any) => c.unit || c.lines).map((c: any) => (
                  <tr key={c.name}>
                    <td style={{ color: c.color }}>{c.name}</td><td>{c.traits}</td><td>{c.mood}</td>
                    <td className="mono">{c.unit || <span className="faint">{tx("不在家")}</span>}</td><td className="num">{c.lines}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>{tx("说了什么")}</div>
            <Talk>
              {!(data.transcript || []).length && <div className="faint">{tx("还没说话")}</div>}
              {(data.transcript || []).slice().reverse().map((l: any, i: number) => (
                <div key={i}>
                  <b style={{ color: colors[l.name] || undefined }}>{l.name}</b> {l.text}
                  <span className="faint sp-meta">{time(l.t)} · {MODE[l.mode] ?? l.mode} · {l.ms} ms</span>
                </div>
              ))}
            </Talk>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── 镜头对白 ──────────────────────────────────────────────────────────
function CamtalkPanel({ inst, ov, reloadOv }: { inst: number; ov: Overview; reloadOv: () => void }) {
  const { data, error, reload } = useFetch<any>(`/api/speech/camtalk?inst=${inst}`, 2000, [inst]);
  if (!data) return <FetchState error={error} onRetry={reload} />;
  const cfg = data.config || {};
  const post = async (body: Record<string, unknown>) => { await speech("camtalk", { inst, ...body }); reload(); reloadOv(); };
  return (
    <Card title={null} bodyClass="card-body">
      <div className="col gap12">
        <FeatureHead kind="camtalk" ov={ov} running={!!data.running} disabled={!ov.instanceUp} onToggle={(on) => post({ enabled: on })}>
          <AutoField kind="camtalk" ov={ov} onDone={reloadOv} />
        </FeatureHead>
        <div className="faint" style={{ fontSize: 12.5 }}>{tx("镜头里的英雄用首领口吻、随从用随从口吻说话；气泡不显示名字；工人和建筑不参与。改了从下一轮起生效，会存盘。")}</div>
        <div className="row wrap" style={{ gap: 10 }}>
          <NumField label={tx("间隔（秒）")} value={cfg.interval_s} min={2} max={120} step={0.5} title={tx("两轮之间；一轮 = 英雄一句 + 随从接几句")} onCommit={(v) => post({ interval_s: v })} />
          <NumField label={tx("每句显示（秒）")} value={cfg.duration_s} min={0} max={30} step={0.5} onCommit={(v) => post({ duration_s: v })} />
          <NumField label={tx("字号")} value={cfg.font_px} min={0} max={120} onCommit={(v) => post({ font_px: v })} />
          <NumField label={tx("每句字数")} value={cfg.max_chars} min={8} max={80} onCommit={(v) => post({ max_chars: v })} />
          <NumField label={tx("随从接几句")} value={cfg.replies} min={0} max={3} onCommit={(v) => post({ replies: v })} />
          <Check label={tx("敌方英雄也放狠话")} checked={!!cfg.include_foes} onChange={(v) => post({ include_foes: v })} />
        </div>
        <TextField label={tx("额外要求")} value={cfg.extra} grow placeholder={tx("可留空。比如：整体搞笑一点 / 多说战术 / 英雄说话带点文言")} onCommit={(v) => post({ extra: v })} />
        {data.scene && <pre className="sp-pre">{data.scene}</pre>}
        {data.last_error && <div className="danger" style={{ fontSize: 12.5 }}>{data.last_error}</div>}
        <div className="grid g2" style={{ alignItems: "start" }}>
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>{tx("画面里")}</div>
            <table className="table">
              <thead><tr><th>{tx("单位")}</th><th>{tx("阵营")}</th><th className="num">{tx("血")}</th></tr></thead>
              <tbody>
                {(data.view || []).map((v: any) => (
                  <tr key={v.unit}><td>{v.name}{v.hero ? " ★" : ""}</td><td>{v.side}</td><td className="num">{v.hp}%</td></tr>
                ))}
              </tbody>
            </table>
            {!(data.view || []).length && <div className="faint" style={{ fontSize: 12, padding: 6 }}>{tx("镜头里没有会说话的单位")}</div>}
          </div>
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>{tx("说了什么")}</div>
            <Talk>
              {!(data.transcript || []).length && <div className="faint">{tx("还没说话")}</div>}
              {(data.transcript || []).slice().reverse().map((l: any, i: number) => (
                <div key={i}>
                  <b style={{ color: ROLE_COLOR[l.role] }}>{l.who}·{ROLE[l.role] ?? l.role}</b> {l.text}
                  <span className="faint sp-meta">{time(l.t)} · {l.ms} ms</span>
                </div>
              ))}
            </Talk>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── 战况触发 ──────────────────────────────────────────────────────────
function EventsPanel({ inst, ov, reloadOv }: { inst: number; ov: Overview; reloadOv: () => void }) {
  const { data, error, reload } = useFetch<any>(`/api/speech/events?inst=${inst}`, 2000, [inst]);
  if (!data) return <FetchState error={error} onRetry={reload} />;
  const cfg = data.config || {};
  const post = async (body: Record<string, unknown>) => { await speech("events", { inst, ...body }); reload(); reloadOv(); };
  return (
    <Card title={null} bodyClass="card-body">
      <div className="col gap12">
        <FeatureHead kind="events" ov={ov} running={!!data.running} disabled={!ov.instanceUp} onToggle={(on) => post({ enabled: on })}>
          <AutoField kind="events" ov={ov} onDone={reloadOv} />
        </FeatureHead>
        <div className="faint" style={{ fontSize: 12.5 }}>{tx("出事了就让相关的人冒一句：开战、打完、英雄残血 / 阵亡 / 复活、升本、开矿、被拆、家被打、死一片、人口到坎……改了马上生效，会存盘。")}</div>
        <div className="row wrap" style={{ gap: 12 }}>
          <Check label={tx("只让镜头里的人说")} checked={!!cfg.only_in_view} onChange={(v) => post({ only_in_view: v })} />
          <Check label={tx("镜头外的事让镜头里的人转述")} checked={!!cfg.bystander} onChange={(v) => post({ bystander: v })}
            title={tx("出事的人不在镜头里（比如英雄在别处阵亡）时，镜头里的自己人听说了、说一句")} />
          <Check label={tx("开战时敌方英雄也放狠话")} checked={!!cfg.include_foes} onChange={(v) => post({ include_foes: v })} />
        </div>
        <div className="row wrap" style={{ gap: 10 }}>
          <NumField label={tx("两次最少隔（秒）")} value={cfg.min_gap_s} min={0} max={120} step={0.5} onCommit={(v) => post({ min_gap_s: v })} />
          <NumField label={tx("开战人数")} value={cfg.fight_min} min={1} max={30} title={tx("双方各至少几个作战单位贴在一起才算开战")} onCommit={(v) => post({ fight_min: v })} />
          <NumField label={tx("死几个算一片")} value={cfg.big_loss_n} min={2} max={50} title={tx("20 秒内")} onCommit={(v) => post({ big_loss_n: v })} />
          <NumField label={tx("英雄残血（%）")} value={cfg.hero_low_pct} min={5} max={90} onCommit={(v) => post({ hero_low_pct: v })} />
          <NumField label={tx("旁人接几句")} value={cfg.replies} min={0} max={2} onCommit={(v) => post({ replies: v })} />
          <NumField label={tx("每句显示（秒）")} value={cfg.duration_s} min={0} max={30} step={0.5} onCommit={(v) => post({ duration_s: v })} />
          <NumField label={tx("字号")} value={cfg.font_px} min={0} max={120} onCommit={(v) => post({ font_px: v })} />
          <NumField label={tx("每句字数")} value={cfg.max_chars} min={8} max={80} onCommit={(v) => post({ max_chars: v })} />
        </div>
        <TextField label={tx("额外要求")} value={cfg.extra} grow placeholder={tx("可留空。比如：多玩梗 / 英雄死了要悲壮一点")} onCommit={(v) => post({ extra: v })} />
        {data.last_error && <div className="danger" style={{ fontSize: 12.5 }}>{data.last_error}</div>}
        <div className="grid g2" style={{ alignItems: "start" }}>
          <table className="table">
            <thead><tr><th style={{ width: 36 }}>{tx("开")}</th><th>{tx("事件")}</th><th>{tx("冷却（秒）")}</th><th className="num">{tx("认出 / 说了")}</th></tr></thead>
            <tbody>
              {(data.events || []).map((e: any) => {
                const c = (data.counts || {})[e.key] || [0, 0];
                return (
                  <tr key={e.key}>
                    <td><input type="checkbox" checked={(cfg.enabled_events || {})[e.key] !== false}
                      onChange={(ev) => post({ enabled_events: { [e.key]: ev.target.checked } })} /></td>
                    <td>{tx(e.name)}</td>
                    <td><NumField label="" value={(cfg.cooldowns || {})[e.key] ?? 0} min={0} max={3600} onCommit={(v) => post({ cooldowns: { [e.key]: v } })} /></td>
                    <td className="num">{c[0]} / {c[1]}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>{tx("认出的事和台词")}</div>
            <Talk>
              {!(data.log || []).length && <div className="faint">{tx("还没有")}</div>}
              {(data.log || []).slice().reverse().map((l: any, i: number) => (
                <div key={i}>
                  <b className="warning">{tx(l.name)}</b> {l.facts}
                  <span className="faint sp-meta">{time(l.t)} · {l.result}</span>
                  {(l.lines || []).map((x: any, j: number) => (
                    <div key={j} className="sp-sub" style={{ color: ROLE_COLOR[x.role] }}>{x.who}：{x.text}</div>
                  ))}
                </div>
              ))}
            </Talk>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── 模型 ──────────────────────────────────────────────────────────────
function ModelPanel({ ov, reloadOv }: { ov: Overview; reloadOv: () => void }) {
  const cfg = ov.llm?.config || {};
  const [busy, setBusy] = useState<string | null>(null);
  const post = async (body: Record<string, unknown>) => { if (await speech("llm", body)) toast(tx("模型设置已保存"), "success"); reloadOv(); };
  const svc = async (action: "start" | "stop") => {
    setBusy(action);
    try {
      const r = await api.post<{ ok?: boolean; error?: string }>(`/api/services/llm/${action}`);
      if (r?.ok === false) toast(r.error ?? tx("没成功"), "error", 8000);
      else toast(action === "start" ? tx("本地大模型启动中（载入模型要十几秒到几十秒）") : tx("已卸载模型"), "success");
      broadcast({ type: "services" });
    } catch (e) { toast((e as Error).message, "error"); }
    setBusy(null);
    reloadOv();
  };
  return (
    <div className="grid g2" style={{ alignItems: "start" }}>
      <Card title={tx("本地大模型")} icon={<Cpu size={15} />}
        actions={<Badge tone={ov.llm?.reachable ? "success" : "danger"}>{ov.llm?.reachable ? tx("模型在线") : tx("模型连不上")}</Badge>}>
        <div className="col gap12">
          <div className="faint" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
            {tx("茶话会、镜头对白、战况触发、和 AI 对话都用它说话。默认走本机 LM Studio；改了马上生效，存在 speech/speak_api.json。")}
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn sm primary" disabled={!!busy} onClick={() => svc("start")}>{busy === "start" ? <span className="spinner sm" /> : <Play size={13} />}{tx("启动并载入模型")}</button>
            <button className="btn sm" disabled={!!busy} onClick={() => svc("stop")}>{busy === "stop" ? <span className="spinner sm" /> : <Square size={13} />}{tx("卸载模型")}</button>
            <button className="btn sm ghost" onClick={() => navigate("home")}>{tx("控制中心")}</button>
          </div>
          <div className="row wrap" style={{ gap: 10 }}>
            <TextField label={tx("接口地址")} value={cfg.base_url} grow onCommit={(v) => post({ base_url: v })} />
          </div>
          <div className="row wrap" style={{ gap: 10 }}>
            <label className="sp-field grow"><span>{tx("模型")}</span>
              <select className="select sm" value={cfg.model ?? ""} onChange={(e) => post({ model: e.target.value })} style={{ flex: 1, minWidth: 200 }}>
                {[...new Set([cfg.model, ...(ov.llm?.models ?? [])].filter(Boolean))].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="sp-field"><span>{tx("方式")}</span>
              <select className="select sm" value={cfg.mode ?? "qwen-nothink"} onChange={(e) => post({ mode: e.target.value })}>
                <option value="qwen-nothink">{tx("qwen-nothink（关思考，最快）")}</option>
                <option value="chat">{tx("chat（标准对话接口）")}</option>
              </select>
            </label>
          </div>
          <div className="row wrap" style={{ gap: 10 }}>
            <NumField label={tx("最多字数（token）")} value={cfg.max_tokens} min={16} max={2048} onCommit={(v) => post({ max_tokens: v })} />
            <NumField label={tx("随机性")} value={cfg.temperature} min={0} max={2} step={0.05} onCommit={(v) => post({ temperature: v })} />
          </div>
        </div>
      </Card>
      <Card title={tx("自动开启")} icon={<Settings2 size={15} />} bodyClass="card-body tight">
        <table className="table">
          <thead><tr><th>{tx("功能")}</th><th>{tx("自动开启的实例")}</th></tr></thead>
          <tbody>
            {(["banter", "camtalk", "events"] as Feature[]).map((k) => (
              <tr key={k}><td>{FEATURE_NAME[k]}</td><td>{(ov.autostart?.[k] ?? []).join(", ") || <span className="faint">{tx("无")}</span>}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="faint" style={{ fontSize: 12, padding: "10px 14px", lineHeight: 1.7 }}>
          {tx("填了的实例：气泡服务重启、游戏每局重开、实例晚起来，都会自动开起来。在各功能分栏里改。")}
        </div>
      </Card>
    </div>
  );
}

// ── 页面 ──────────────────────────────────────────────────────────────
const TAB_KEY = "farsight.speech.tab";

export default function Speech() {
  const inst = useApp((s) => s.inst);
  const { data: ov, error, reload } = useFetch<Overview>(`/api/speech-overview?inst=${inst}`, 3000, [inst]);
  const [tab, setTabState] = useState<Tab>(() => {
    try { return (sessionStorage.getItem(TAB_KEY) as Tab) || "say"; } catch { return "say"; }
  });
  const setTab = (t: Tab) => { setTabState(t); try { sessionStorage.setItem(TAB_KEY, t); } catch { /* 只是记住分栏 */ } };
  const [starting, setStarting] = useState<string | null>(null);

  const start = async (key: "speech" | "llm") => {
    setStarting(key);
    try {
      const r = await api.post<{ ok?: boolean; error?: string }>(`/api/services/${key}/start`);
      if (r?.ok === false) toast(r.error ?? tx("没成功"), "error", 8000);
      else toast(key === "speech" ? tx("气泡服务已启动") : tx("本地大模型启动中（载入模型要十几秒到几十秒）"), "success");
      broadcast({ type: "services" });
    } catch (e) { toast((e as Error).message, "error"); }
    setStarting(null);
    reload();
  };

  const head = (
    <div className="page-head">
      <div>
        <h1 className="page-title">{tx("头顶气泡 · 实例 {0}", inst)}</h1>
        <p className="page-desc">{tx("让游戏里的单位头顶说话：手动写、和 AI 对话，或者交给茶话会、镜头对白、战况触发自动说。换实例用顶栏的实例菜单。")}</p>
      </div>
      <div className="right row">
        {ov?.up && <Badge tone="success">{tx("气泡服务在跑 · 端口 {0}", ov.port ?? "")}</Badge>}
        {ov?.up && (ov.llm?.reachable ? <Badge tone="success">{tx("模型在线")}</Badge>
          : <button className="btn sm" disabled={!!starting} onClick={() => start("llm")}>{starting === "llm" ? <span className="spinner sm" /> : <Cpu size={13} />}{tx("启动本地大模型")}</button>)}
      </div>
    </div>
  );

  if (!ov) return <div className="page">{head}<FetchState error={error} onRetry={reload} /></div>;
  if (!ov.up) {
    return (
      <div className="page">
        {head}
        <div className="card">
          <Empty icon={<MessageSquareText size={26} />} title={tx("气泡服务没在跑")}>
            <div className="col gap8" style={{ alignItems: "center", maxWidth: 520 }}>
              <span>{tx("气泡在游戏里的绘制、茶话会等功能都在气泡服务里跑。启动它就能用；要让角色自己说话，还要启动本地大模型。")}</span>
              <div className="row" style={{ gap: 6 }}>
                <button className="btn sm primary" disabled={!!starting} onClick={() => start("speech")}>{starting === "speech" ? <span className="spinner sm" /> : <Play size={13} />}{tx("启动气泡服务")}</button>
                <button className="btn sm" disabled={!!starting} onClick={() => start("llm")}>{starting === "llm" ? <span className="spinner sm" /> : <Cpu size={13} />}{tx("启动本地大模型")}</button>
              </div>
            </div>
          </Empty>
        </div>
      </div>
    );
  }

  const feat = ov.features;
  const chip = (k: Feature, icon: ReactNode) => (
    <button className={`chip sp-chip ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>
      {icon}{FEATURE_NAME[k]}
      <span className={`dot ${feat?.[k]?.running ? "success" : ""}`} />
      <span className="faint">{feat?.[k]?.running ? RUNNING_WORD[k] : tx("没开")}</span>
    </button>
  );
  return (
    <div className="page">
      {head}
      {!ov.instanceUp && (
        <div className="card sp-warn">
          {tx("实例 {0} 的游戏没在跑：功能开关先点不了（自动开启表照样能填，游戏起来后 5 秒内自动开）。先在「实例与开局」开局，或者在顶栏换一个在跑的实例。", inst)}
          <button className="btn sm" onClick={() => navigate("instances")}>{tx("实例与开局")}</button>
        </div>
      )}
      <div className="row wrap" style={{ gap: 8, marginBottom: 14 }}>
        <Seg value={tab} onChange={setTab} options={[
          { value: "say", label: tx("说话"), icon: <MessageSquareText size={13} /> },
          { value: "banter", label: FEATURE_NAME.banter, icon: <Users size={13} /> },
          { value: "camtalk", label: FEATURE_NAME.camtalk, icon: <Video size={13} /> },
          { value: "events", label: FEATURE_NAME.events, icon: <Zap size={13} /> },
          { value: "model", label: tx("模型"), icon: <Cpu size={13} /> },
        ]} />
        <div className="right row wrap" style={{ gap: 6 }}>
          {chip("banter", <Users size={13} />)}
          {chip("camtalk", <Video size={13} />)}
          {chip("events", <Zap size={13} />)}
        </div>
      </div>
      {tab === "say" && <SayPanel inst={inst} ov={ov} reloadOv={reload} />}
      {tab === "banter" && <BanterPanel inst={inst} ov={ov} reloadOv={reload} />}
      {tab === "camtalk" && <CamtalkPanel inst={inst} ov={ov} reloadOv={reload} />}
      {tab === "events" && <EventsPanel inst={inst} ov={ov} reloadOv={reload} />}
      {tab === "model" && <ModelPanel ov={ov} reloadOv={reload} />}
    </div>
  );
}
