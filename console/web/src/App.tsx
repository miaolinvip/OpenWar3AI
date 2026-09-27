import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity, Bot, Brain, CircleCheck, CircleX, Clapperboard, Coins, Command, Gauge, Info, LayoutDashboard, Map as MapIcon, Moon,
  PanelLeftClose, PanelLeftOpen, ScrollText, Search, Server, Settings, Sun, Swords, Trees, Trophy, Users, Wifi, WifiOff, ChevronDown, Monitor, Zap, SquareX,
  NotebookPen, Crosshair, Package, TerminalSquare, Languages, MousePointerClick, Plug, Sparkles, RefreshCw, HardDrive, LayoutGrid,
  MessageSquareText, ExternalLink, Power, MessageCircleHeart, ArrowUpCircle,
} from "lucide-react";
import { navigate, pageHref, setInst, store, useApp } from "./lib/store";
import { setTheme, useTheme, resolvedMode } from "./lib/theme";
import { ATTACK_STATE, DEFENSE_MODE, aiState, fmtTime } from "./lib/game";
import { api, action } from "./lib/api";
import { Dialog } from "./components/ui";
import { CHANGELOG, VERSION, hasUnseenChanges, markChangesSeen, seenChange } from "./lib/changelog";
import type { InstanceStatus } from "./lib/types";
import Home from "./pages/Home";
import Overview from "./pages/Overview";
import Battlefield from "./pages/Battlefield";
import Units from "./pages/Units";
import BrainPage from "./pages/Brain";
import Director from "./pages/Director";
import ApmPage from "./pages/Apm";
import Logs from "./pages/Logs";
import Matches from "./pages/Matches";
import Instances from "./pages/Instances";
import SettingsPage from "./pages/Settings";
import IssuesPage from "./pages/Issues";
import CoachPage from "./pages/Coach";
import DecisionsPage from "./pages/Decisions";
import SchemesPage from "./pages/Schemes";
import JassConsolePage from "./pages/JassConsole";
import EventsPage from "./pages/Events";
import ConnectPage from "./pages/Connect";
import StoragePage from "./pages/Storage";
import SpeechPage from "./pages/Speech";
import FeedbackPage from "./pages/Feedback";
import { UPDATE_DISMISS, useUpdate } from "./lib/update";
import { LANGS, lang, setLang, tx } from "./lib/i18n";

const PAGES: { key: string; label: string; icon: ReactNode; section: string; hot: string }[] = [
  // 首页：本机各服务的状态和起停、环境自检（09-26，以前的几个 bat 都并到这里）
  { key: "home", label: tx("控制中心"), icon: <LayoutGrid size={17} />, section: tx("总控"), hot: "" },
  { key: "overview", label: tx("对局总览"), icon: <LayoutDashboard size={17} />, section: tx("对局"), hot: "1" },
  { key: "battlefield", label: tx("战场指挥"), icon: <Swords size={17} />, section: tx("对局"), hot: "2" },
  { key: "units", label: tx("单位数据"), icon: <Users size={17} />, section: tx("对局"), hot: "3" },
  { key: "brain", label: tx("AI 决策"), icon: <Brain size={17} />, section: tx("对局"), hot: "4" },
  { key: "decisions", label: tx("战斗决策"), icon: <Crosshair size={17} />, section: tx("对局"), hot: "" },
  { key: "coach", label: tx("运营顾问"), icon: <Bot size={17} />, section: tx("对局"), hot: "" },
  { key: "director", label: tx("导播台"), icon: <Clapperboard size={17} />, section: tx("对局"), hot: "5" },
  // 09-26 并进来的气泡调试台（以前是气泡服务自己的 8872 页）
  { key: "speech", label: tx("头顶气泡"), icon: <MessageSquareText size={17} />, section: tx("对局"), hot: "" },
  { key: "apm", label: tx("指令速度"), icon: <Zap size={17} />, section: tx("对局"), hot: "0" },
  { key: "events", label: tx("事件与输入"), icon: <MousePointerClick size={17} />, section: tx("对局"), hot: "" },
  { key: "logs", label: tx("日志"), icon: <ScrollText size={17} />, section: tx("记录"), hot: "6" },
  { key: "matches", label: tx("对局记录"), icon: <Trophy size={17} />, section: tx("记录"), hot: "7" },
  { key: "issues", label: tx("问题备忘"), icon: <NotebookPen size={17} />, section: tx("记录"), hot: "" },
  { key: "instances", label: tx("实例与开局"), icon: <Server size={17} />, section: tx("系统"), hot: "8" },
  { key: "schemes", label: tx("AI 方案"), icon: <Package size={17} />, section: tx("系统"), hot: "" },
  { key: "jass", label: tx("JASS 控制台"), icon: <TerminalSquare size={17} />, section: tx("系统"), hot: "" },
  { key: "connect", label: tx("接入与扩展"), icon: <Plug size={17} />, section: tx("系统"), hot: "" },
  { key: "storage", label: tx("数据与磁盘"), icon: <HardDrive size={17} />, section: tx("系统"), hot: "" },
  { key: "settings", label: tx("设置"), icon: <Settings size={17} />, section: tx("系统"), hot: "9" },
  // 09-26：遇到问题 / 有建议直接发给 War3AI.com（运行时签名 + 本机识别码，console/server/feedback.py）
  { key: "feedback", label: tx("反馈与建议"), icon: <MessageCircleHeart size={17} />, section: tx("系统"), hot: "" },
];

// 不跟某个实例走的页面（标签页标题里不带实例号）
const GLOBAL_PAGES = new Set(["home", "instances", "schemes", "connect", "storage", "settings", "issues", "feedback"]);


/** 有新版本：页面顶上一条（点「稍后」这个版本就不再提示，出了更新的版本再提示） */
function NewVersionBanner() {
  const { data } = useUpdate();
  const other = useApp((s) => s.updateReady || s.langChanged);    // 同一个位置：「刷新载入」那条先处理
  const [dismissed, setDismissed] = useState(() => { try { return localStorage.getItem(UPDATE_DISMISS); } catch { return null; } });
  if (other || !data?.newer || !data.latest || dismissed === data.latest) return null;
  const note = data.notes?.[lang] ?? data.notes?.en ?? "";
  return (
    <div className="update-banner" role="status">
      <ArrowUpCircle size={15} className="accent" />
      <span>{tx("OpenWar3 有新版本 {0}（现在是 {1}）", data.latest, data.current)}{note ? ` · ${note}` : ""}</span>
      <div className="right row" style={{ gap: 6 }}>
        <button className="btn sm ghost" onClick={() => { try { localStorage.setItem(UPDATE_DISMISS, data.latest!); } catch { /* 这次打开有效 */ } setDismissed(data.latest); }}>{tx("稍后")}</button>
        <a className="btn sm primary" href={data.url ?? "https://war3ai.com/"} target="_blank" rel="noreferrer"><ExternalLink size={13} />{tx("查看更新")}</a>
      </div>
    </div>
  );
}

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 64 64" aria-hidden>
      <defs>
        <linearGradient id="fsg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="hsl(var(--accent-h) 90% 70%)" />
          <stop offset="1" stopColor="var(--accent)" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="var(--bg-sunken)" stroke="var(--border)" />
      <path d="M9 32c6-10 13.5-15 23-15s17 5 23 15c-6 10-13.5 15-23 15S15 42 9 32z" fill="none" stroke="url(#fsg)" strokeWidth="4" />
      <circle cx="32" cy="32" r="8" fill="url(#fsg)" />
      <circle cx="35" cy="29" r="2.4" fill="#fff" />
    </svg>
  );
}

function Sidebar() {
  const page = useApp((s) => s.page);
  const collapsed = useApp((s) => s.collapsed);
  const ws = useApp((s) => s.ws);
  const pending = useApp((s) => s.issuesPending);
  const clOpen = useApp((s) => s.changelogOpen);
  const inst = useApp((s) => s.inst);
  const { data: upd } = useUpdate();
  const unseen = useMemo(() => hasUnseenChanges(), [clOpen]);
  let section = "";
  return (
    <aside className="sidebar">
      <div className="brand" onClick={() => navigate("home")} style={{ cursor: "pointer" }} title={tx("控制中心")}>
        <BrandMark />
        <div className="brand-text">
          <div className="brand-name">{tx("Farsight 远见")}</div>
          <div className="brand-sub">{tx("War3 AI 指挥台")}</div>
        </div>
      </div>
      <nav className="nav">
        {PAGES.map((p) => {
          const head = p.section !== section ? <div className="nav-section">{(section = p.section)}</div> : null;
          return (
            <div key={p.key}>
              {head}
              {/* 真链接：Ctrl / 中键点击在新标签页打开同一页、同一个实例（多标签页各看各的） */}
              <a className={`nav-item ${page === p.key ? "active" : ""}`} href={pageHref(p.key, inst)}
                title={collapsed ? p.label : tx("Ctrl + 点击：在新标签页打开")}>
                {p.icon}
                <span className="nav-label">{p.label}</span>
                {p.key === "issues" && pending ? <span className="nav-count" title={tx("{0} 条待填写（游戏里按快捷键记的）", pending)}>{pending}</span>
                  : p.hot ? <span className="nav-kbd">Alt {p.hot}</span> : null}
              </a>
            </div>
          );
        })}
      </nav>
      <div className="side-foot">
        <button className="nav-item" onClick={() => { const v = !store.get().collapsed; localStorage.setItem("farsight.collapsed", v ? "1" : "0"); store.set({ collapsed: v }); }}>
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          <span className="nav-label">{tx("收起侧栏")}</span>
        </button>
        <div className="row side-foot-text" style={{ padding: "2px 10px", fontSize: 12, color: "var(--text-3)" }}>
          <span className={`dot ${ws === "open" ? "success" : ws === "connecting" ? "warning pulse" : "danger"}`} />
          {ws === "open" ? tx("实时连接正常") : ws === "connecting" ? tx("正在连接…") : tx("连接断开，重连中")}
        </div>
        <button className="side-ver" onClick={() => store.set({ changelogOpen: true })} title={tx("最近更新")}>
          <Sparkles size={13} /> <span>{tx("最近更新")}</span>
          <span className="right mono">v{VERSION}</span>
          {unseen && <span className="dot" title={tx("有没看过的更新")} />}
        </button>
        {upd?.newer && upd.latest && (
          <a className="side-ver side-new" href={upd.url ?? "https://war3ai.com/"} target="_blank" rel="noreferrer" title={tx("去 War3AI.com 看更新")}>
            <ArrowUpCircle size={13} /> <span>{tx("有新版本")}</span><span className="right mono">v{upd.latest}</span>
          </a>
        )}
      </div>
    </aside>
  );
}

// 顶栏 / 实例菜单上显示「这个实例在跑哪个 AI」：参考大脑叫新大脑，玩家方案显示方案 id
function aiLabel(i: InstanceStatus) {
  const a = aiState(i);
  if (a.kind === "brain") return tx("新大脑");
  if (a.kind === "bot") return a.name ?? tx("方案");
  if (a.kind === "mod") return tx("模组 · {0}", a.name ?? "?");
  return a.kind === "old" ? tx("老 AI") : tx("无 AI");
}

/** 在新标签页打开「这一页 + 那个实例」：多开几个标签页各盯一个实例 */
function NewTabLink({ inst }: { inst: number }) {
  const page = useApp((s) => s.page);
  return (
    <a className="btn ghost icon sm" href={pageHref(page, inst)} target="_blank" rel="noopener"
      title={tx("在新标签页打开实例 {0}", inst)} onClick={(e) => e.stopPropagation()}>
      <ExternalLink size={13} />
    </a>
  );
}

function InstancePicker() {
  const inst = useApp((s) => s.inst);
  const instances = useApp((s) => s.instances);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, []);
  const cur = instances.find((i) => i.inst === inst);
  const alive = instances.filter((i) => i.game.alive);
  const rest = instances.filter((i) => !i.game.alive);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="inst-pick" onClick={() => setOpen(!open)}>
        <span className={`dot ${cur?.game.alive ? "success" : ""}`} />
        {tx("实例 {0}", inst)}
        {cur?.brain.length || cur?.bot?.length ? <span className="badge accent" style={{ height: 18 }}>{aiLabel(cur)}</span> : cur?.oldAi.length ? <span className="badge">{tx("老 AI")}</span> : null}
        <ChevronDown size={14} className="faint" />
      </button>
      {open && (
        <div className="menu" style={{ top: 38, left: 0, width: 310 }}>
          <div className="palette-group">{tx("在跑的")}</div>
          {alive.length === 0 && <div className="menu-item faint">{tx("没有在跑的实例")}</div>}
          {alive.map((i) => (
            <div key={i.inst} className={`menu-item ${i.inst === inst ? "on" : ""}`} onClick={() => { setInst(i.inst); setOpen(false); }}>
              <span className="dot success" /> {tx("实例 {0}", i.inst)}
              <span className="faint mono">{i.game.exe}</span>
              <span className="right faint">{aiLabel(i)}</span>
              <NewTabLink inst={i.inst} />
            </div>
          ))}
          {rest.length > 0 && <div className="palette-group">{tx("已停止")}</div>}
          {rest.slice(0, 12).map((i) => (
            <div key={i.inst} className={`menu-item ${i.inst === inst ? "on" : ""}`} onClick={() => { setInst(i.inst); setOpen(false); }}>
              <span className="dot" /> {tx("实例 {0}", i.inst)}
              <span className="right" />
              <NewTabLink inst={i.inst} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EndMatchButton() {
  const inst = useApp((s) => s.inst);
  const alive = useApp((s) => s.instances.find((i) => i.inst === s.inst)?.game.alive);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const run = async (what: "end-game" | "kill-game") => {
    setBusy(true);
    const r = await action(() => api.post<any>(`/api/instances/${inst}/${what}`, {}),
      what === "end-game" ? tx("结束令已发出，farm 会按下一局设置重开") : tx("已强制关闭游戏进程"));
    setBusy(false);
    if (r) setOpen(false);
  };
  return (
    <>
      <button className="btn ghost sm" title={tx("结束这一局（farm 会按下一局设置自动重开）")} disabled={!alive} onClick={() => setOpen(true)}>
        <SquareX size={15} /> <span className="hide-sm">{tx("结束本局")}</span>
      </button>
      <Dialog open={open} title={tx("结束实例 {0} 这一局", inst)} onClose={() => setOpen(false)}
        footer={<>
          <button className="btn" onClick={() => setOpen(false)}>{tx("取消")}</button>
          <button className="btn danger" disabled={busy} onClick={() => run("kill-game")}>{tx("强制关闭游戏进程")}</button>
          <button className="btn primary" disabled={busy} onClick={() => run("end-game")}>{tx("结束本局")}</button>
        </>}>
        <div className="col gap8 muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
          <div><b>{tx("结束本局")}</b>{tx("：给游戏发结束令，这局记成「手动结束」，farm 按下一局设置重开。设置页「对局运行 → 结束后强制关闭游戏进程」开着时，游戏若没退会自动补一刀。")}</div>
          <div><b>{tx("强制关闭游戏进程")}</b>{tx("：直接关掉游戏（关不掉就关它的启动器守护），用于结束令不管用的时候。")}</div>
        </div>
      </Dialog>
    </>
  );
}

function Topbar() {
  const snap = useApp((s) => s.snapshot);
  const brain = useApp((s) => s.brain);
  const worker = useApp((s) => s.worker);
  const theme = useTheme();
  const mode = resolvedMode(theme);
  const t = snap?.t;
  return (
    <header className="topbar">
      <InstancePicker />
      <div className="chips">
        <span className="chip" title={tx("游戏时间")}><Activity size={14} /><b>{fmtTime(t)}</b></span>
        <span className="chip"><Coins size={14} style={{ color: "#e2b53b" }} /><b>{snap?.gold ?? "—"}</b></span>
        <span className="chip"><Trees size={14} style={{ color: "#3fa561" }} /><b>{snap?.lumber ?? "—"}</b></span>
        <span className="chip opt"><Users size={14} /><b>{snap && snap.foodCap != null ? `${snap.foodUsed ?? "—"}/${snap.foodCap}` : "—"}</b></span>
        {brain && (
          <>
            <span className="chip opt" title={tx("出击状态机")}><Bot size={14} />{ATTACK_STATE[brain.attack.state] ?? brain.attack.state}</span>
            <span className="chip opt" title={tx("守家")}><Gauge size={14} />{DEFENSE_MODE[brain.defense.mode] ?? brain.defense.mode}</span>
            <span className="chip opt" title={tx("我方强度 / 敌方强度")}>
              <b className="success">{brain.world.ownStrength}</b><span className="faint">vs</span><b className="danger">{brain.world.foeStrength}</b>
            </span>
          </>
        )}
      </div>
      <div className="right row" style={{ gap: 6 }}>
        <span className="chip opt" title={worker?.error ?? ""}>
          {worker?.connected ? <Wifi size={14} className="success" /> : <WifiOff size={14} className="danger" />}
          {worker?.connected ? `pid ${worker.pid}` : tx("没连上游戏")}
        </span>
        <EndMatchButton />
        <button className="btn ghost sm" onClick={() => store.set({ paletteOpen: true })} title={tx("命令面板")}>
          <Search size={14} /> <span className="faint hide-sm">{tx("搜索或执行…")}</span> <span className="kbd hide-sm">Ctrl K</span>
        </button>
        {/* 语言：选了就存进浏览器并刷新（见 lib/i18n.ts）；名字用各自语言写，切错了也认得回来 */}
        <label className="chip lang-pick" title="语言 / Language">
          <Languages size={14} />
          <select value={lang} onChange={(e) => setLang(e.target.value as typeof lang)}>
            {LANGS.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
          </select>
        </label>
        <button className="btn ghost icon" title={tx("切换深浅色")} onClick={() => setTheme({ mode: mode === "dark" ? "light" : "dark" })}>
          {mode === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </div>
    </header>
  );
}

function Palette() {
  const open = useApp((s) => s.paletteOpen);
  const inst = useApp((s) => s.inst);
  const instances = useApp((s) => s.instances);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(() => {
    const list: { group: string; label: string; icon: ReactNode; run: () => void; hint?: string }[] = [];
    PAGES.forEach((p) => list.push({ group: tx("跳转"), label: p.label, icon: p.icon, run: () => navigate(p.key), hint: p.hot ? `Alt ${p.hot}` : undefined }));
    list.push({ group: tx("跳转"), label: tx("最近更新"), icon: <Sparkles size={16} />, run: () => store.set({ changelogOpen: true }) });
    instances.forEach((i) => list.push({ group: tx("切换实例"), label: tx("实例 {0}{1}", i.inst, i.game.alive ? tx("（在跑）") : ""), icon: <Server size={16} />, run: () => setInst(i.inst) }));
    list.push(
      { group: tx("操作"), label: tx("在新标签页打开这一页"), icon: <ExternalLink size={16} />, run: () => window.open(pageHref(store.get().page), "_blank", "noopener") },
      { group: tx("操作"), label: tx("结束当前这一局（farm 会按下一局设置重开）"), icon: <CircleX size={16} />, run: () => action(() => api.post(`/api/instances/${inst}/end-game`), tx("已发出结束这局")) },
      { group: tx("操作"), label: tx("启动 AI（按这个实例选的方案）"), icon: <Bot size={16} />, run: () => action(() => api.post(`/api/instances/${inst}/brain-start`, { hours: 6 }), tx("AI 启动中")) },
      { group: tx("外观"), label: tx("深色模式"), icon: <Moon size={16} />, run: () => setTheme({ mode: "dark" }) },
      { group: tx("外观"), label: tx("浅色模式"), icon: <Sun size={16} />, run: () => setTheme({ mode: "light" }) },
      { group: tx("外观"), label: tx("跟随系统"), icon: <Monitor size={16} />, run: () => setTheme({ mode: "system" }) },
      { group: tx("外观"), label: tx("现代风格"), icon: <LayoutDashboard size={16} />, run: () => setTheme({ style: "modern" }) },
      { group: tx("外观"), label: tx("魔兽风格"), icon: <Swords size={16} />, run: () => setTheme({ style: "war3" }) },
      ...LANGS.filter((l) => l.code !== lang).map((l) => ({ group: "语言 / Language", label: l.name, icon: <Languages size={16} />, run: () => setLang(l.code) })),
    );
    const k = q.trim().toLowerCase();
    return k ? list.filter((x) => (x.label + x.group).toLowerCase().includes(k)) : list;
  }, [q, instances, inst]);
  useEffect(() => { if (open) { setQ(""); setIdx(0); setTimeout(() => inputRef.current?.focus(), 10); } }, [open]);
  useEffect(() => setIdx(0), [q]);
  if (!open) return null;
  const close = () => store.set({ paletteOpen: false });
  let group = "";
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="dialog" style={{ width: "min(620px, calc(100vw - 24px))" }}>
        <div style={{ position: "relative" }}>
          <Command size={17} style={{ position: "absolute", left: 18, top: 17, color: "var(--text-3)" }} />
          <input ref={inputRef} className="palette-input" placeholder={tx("跳转页面、切换实例、执行操作…")} value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") close();
              if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter" && items[idx]) { items[idx].run(); close(); }
            }} />
        </div>
        <div className="palette-list">
          {items.length === 0 && <div className="empty">{tx("没有匹配的命令")}</div>}
          {items.map((it, i) => {
            const head = it.group !== group ? <div className="palette-group">{(group = it.group)}</div> : null;
            return (
              <div key={it.group + it.label}>
                {head}
                <div className={`palette-item ${i === idx ? "on" : ""}`} onMouseEnter={() => setIdx(i)} onClick={() => { it.run(); close(); }}>
                  {it.icon}<span>{it.label}</span>{it.hint && <span className="right kbd">{it.hint}</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** 最近更新：侧栏底部「最近更新」、命令面板打开；控制台换了新版本、有没看过的条目时自动弹一次 */
function Changelog() {
  const open = useApp((s) => s.changelogOpen);
  const close = () => { markChangesSeen(); store.set({ changelogOpen: false }); };
  const seen = useMemo(() => seenChange() ?? 0, [open]);
  return (
    <Dialog open={open} title={<span className="row" style={{ gap: 8 }}><Sparkles size={16} />{tx("最近更新")} <span className="faint mono" style={{ fontSize: 12 }}>v{VERSION}</span></span>} onClose={close} wide
      footer={<button className="btn primary" onClick={close}>{tx("知道了")}</button>}>
      <div style={{ maxHeight: "62vh", overflow: "auto" }}>
        {CHANGELOG.map((c) => (
          <div key={c.id} className="cl-entry">
            <div className="cl-head">
              <b>{c.title}</b>
              <span className="faint mono" style={{ fontSize: 12 }}>{c.date}</span>
              {c.id > seen && <span className="badge accent">{tx("新")}</span>}
            </div>
            <ul>
              {c.items.map((it, i) => (
                <li key={i}>
                  {it.text}
                  {it.page && <button className="btn ghost sm" onClick={() => { close(); navigate(it.page!); }}>{tx("去看看")}</button>}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Dialog>
  );
}

function UpdateBanner() {
  const ready = useApp((s) => s.updateReady);
  const langChanged = useApp((s) => s.langChanged);
  if (!ready && !langChanged) return null;
  return (
    <div className="update-banner" role="status">
      <RefreshCw size={15} className="accent" />
      <span>{ready ? tx("远见有新版本了。手上的输入保存好之后点「刷新」载入。")
        : tx("界面语言在别的标签页换了，刷新这个标签页就换过来。")}</span>
      <div className="right row" style={{ gap: 6 }}>
        <button className="btn sm ghost" onClick={() => store.set({ updateReady: false, langChanged: false })}>{tx("稍后")}</button>
        <button className="btn sm primary" onClick={() => location.reload()}>{tx("刷新")}</button>
      </div>
    </div>
  );
}

/** 系统已「全部停止」（这个或别的标签页点的，lib/tabs.ts 广播）：整页换成这一屏，别再显示「连接断开，重连中」 */
function StoppedScreen() {
  const lines = useApp((s) => s.stopped);
  if (!lines) return null;
  return (
    <div className="shutdown-screen" role="status">
      <div className="card" style={{ padding: "26px 30px", maxWidth: 520, width: "calc(100vw - 32px)" }}>
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>
          <Power size={22} className="faint" />
          <h2 style={{ margin: 0, fontSize: 20 }}>{tx("OpenWar3 已全部停止")}</h2>
        </div>
        <ul className="muted" style={{ margin: "0 0 14px", paddingLeft: 18, fontSize: 13, lineHeight: 1.8 }}>
          {lines.map((l, i) => <li key={i}>{l}</li>)}
          <li>{tx("远见后台：已关闭")}</li>
        </ul>
        <div style={{ fontSize: 13.5 }}>{tx("可以关掉这个网页了。要再用，双击 start.bat。")}</div>
      </div>
    </div>
  );
}

function Toasts() {
  const toasts = useApp((s) => s.toasts);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.kind === "success" ? <CircleCheck size={17} /> : t.kind === "error" ? <CircleX size={17} /> : <Info size={17} />}
          <div>{t.text}</div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const page = useApp((s) => s.page);
  const inst = useApp((s) => s.inst);
  const collapsed = useApp((s) => s.collapsed);
  // 标签页标题 = 页面名（· 实例号）：开一排远见标签页也一眼分得清
  useEffect(() => {
    const label = PAGES.find((p) => p.key === page)?.label ?? tx("控制中心");
    document.title = `${GLOBAL_PAGES.has(page) ? label : `${label} · ${tx("实例 {0}", inst)}`} — Farsight`;
  }, [page, inst]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); store.set({ paletteOpen: !store.get().paletteOpen }); }
      if (e.altKey && /^[0-9]$/.test(e.key)) { const hit = PAGES.find((p) => p.hot === e.key); if (hit) { e.preventDefault(); navigate(hit.key); } }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  // 有没看过的更新（控制台刚升级过）：打开时自动弹一次，关掉就记成看过
  useEffect(() => {
    if ((seenChange() ?? 0) < CHANGELOG[0].id) store.set({ changelogOpen: true });
  }, []);
  const full = page === "battlefield" || page === "logs";
  let body: ReactNode;
  switch (page) {
    case "battlefield": body = <Battlefield />; break;
    case "units": body = <Units />; break;
    case "brain": body = <BrainPage />; break;
    case "director": body = <Director />; break;
    case "apm": body = <ApmPage />; break;
    case "logs": body = <Logs />; break;
    case "matches": body = <Matches />; break;
    case "instances": body = <Instances />; break;
    case "settings": body = <SettingsPage />; break;
    case "issues": body = <IssuesPage />; break;
    case "coach": body = <CoachPage />; break;
    case "decisions": body = <DecisionsPage />; break;
    case "schemes": body = <SchemesPage />; break;
    case "jass": body = <JassConsolePage />; break;
    case "events": body = <EventsPage />; break;
    case "connect": body = <ConnectPage />; break;
    case "storage": body = <StoragePage />; break;
    case "speech": body = <SpeechPage />; break;
    case "feedback": body = <FeedbackPage />; break;
    case "overview": body = <Overview />; break;
    default: body = <Home />;
  }
  return (
    <div className={`app ${collapsed ? "collapsed" : ""}`}>
      <Sidebar />
      <div className="main">
        <Topbar />
        <main className="content" style={full ? { overflow: "hidden" } : undefined}>{body}</main>
      </div>
      <UpdateBanner />
      <NewVersionBanner />
      <StoppedScreen />
      <Palette />
      <Changelog />
      <Toasts />
    </div>
  );
}

export { MapIcon };
