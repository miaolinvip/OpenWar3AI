// 首页「控制中心」（09-26）：本机哪些服务在跑、还能启动什么、游戏实例概况、环境自检、扩展入口 —— 一眼看全、就地起停。
// 后端 GET /api/services、POST /api/services/{key}/{action}、GET /api/services/{key}/log、GET /api/health（console/server/services.py）。
// 以前网关、气泡、官网预览各有一个 bat；09-26 起只剩 start.bat（部署 + 起远见），其余都在这一页点。
// 给人看的文字都在这里按 key / code 写（要翻译），后端只给状态和数据。
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity, ArrowRight, Bot, CircleCheck, CircleX, Clapperboard, Cpu, ExternalLink, FileText, Globe, HardDrive, Info, LayoutGrid,
  MessageSquareText, Monitor, MousePointerClick, Package, Play, Plug, Power, RefreshCw, RotateCw, Server, Settings, ShieldCheck, Square,
  TerminalSquare, TriangleAlert, MessageCircleHeart, ArrowUpCircle,
} from "lucide-react";
import { Badge, Card, Dialog, Empty, FetchState, Stat, useFetch } from "../components/ui";
import { api } from "../lib/api";
import { navigate, setInst, store, toast, useApp } from "../lib/store";
import { broadcast, onBroadcast } from "../lib/tabs";
import GameDirSetup, { type GameDirStatus } from "../components/GameDirSetup";
import { useUpdate, type UpdateInfo } from "../lib/update";
import { aiSentence, aiState } from "../lib/game";
import { tx, locale } from "../lib/i18n";
import type { HealthCheck, HealthResp, InstanceStatus, ServiceRow, ServicesResp, ServiceState } from "../lib/types";

const SVC: Record<string, { name: string; desc: string; icon: ReactNode; page?: string; pageLabel?: string; open?: string }> = {
  console: { name: tx("远见后台"), icon: <Monitor size={18} />,
    desc: tx("就是这个网页指挥台：实例与开局、实时战况、AI 方案、日志都在这里。") },
  gateway: { name: tx("网关"), icon: <Plug size={18} />, page: "connect", pageLabel: tx("接法"), open: tx("演示页"),
    desc: tx("WebSocket / JSON 接口：任何语言、浏览器页面、另一台机器上的程序都能接进游戏。") },
  speech: { name: tx("头顶聊天气泡"), icon: <MessageSquareText size={18} />, page: "speech", pageLabel: tx("气泡台"),
    desc: tx("让单位头顶说话：农民茶话会、镜头对白、战况触发。要说话得先把本地大模型也启动。") },
  llm: { name: tx("本地大模型"), icon: <Cpu size={18} />, page: "coach", pageLabel: tx("运营顾问"),
    desc: tx("LM Studio 里的本地模型，给气泡、运营顾问、玩伴用。启动 = 开 LM Studio 的本地服务并把模型载入显存。") },
  website: { name: tx("官网本地预览"), icon: <Globe size={18} />, open: tx("打开"),
    desc: tx("war3ai.com 文档站在本机预览，改文档时看效果用（开发者）。第一次启动要先装依赖，一两分钟。") },
  mcp: { name: tx("MCP 服务器"), icon: <Bot size={18} />, page: "connect", pageLabel: tx("挂接命令"),
    desc: tx("给 Claude 等大模型用的工具接口。由大模型客户端按需拉起，不用在这里启动。") },
};

const STATE: Record<ServiceState, { label: string; tone: string; dot: string }> = {
  running: { label: tx("运行中"), tone: "success", dot: "success" },
  starting: { label: tx("启动中"), tone: "warning", dot: "warning pulse" },
  partial: { label: tx("服务在，模型没载入"), tone: "warning", dot: "warning" },
  stopped: { label: tx("未启动"), tone: "", dot: "" },
  conflict: { label: tx("端口被别的程序占着"), tone: "danger", dot: "danger" },
  unavailable: { label: tx("不可用"), tone: "", dot: "" },
  "on-demand": { label: tx("按需启动"), tone: "info", dot: "" },
  error: { label: tx("查询出错"), tone: "danger", dot: "danger" },
};

const REASON: Record<string, string> = {
  "missing-websockets": tx("缺 Python 包 websockets：重新双击 start.bat 会自动补装。"),
  "missing-lmstudio": tx("没装 LM Studio。要用本地大模型时去 lmstudio.ai 下载安装，装好后回来点启动。"),
  "missing-node": tx("官网预览要 Node.js：命令行运行 start.bat node 装一份私有的（约 33 MB，不影响系统）。"),
  "missing-website": tx("仓库里没有 website 目录。"),
};

const RUNNING: ServiceState[] = ["running", "starting", "partial"];

function fmtDur(sec: number): string {
  if (sec < 60) return tx("刚刚");
  const m = Math.floor(sec / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d > 0) return tx("{0} 天 {1} 小时", d, h % 24);
  if (h > 0) return tx("{0} 小时 {1} 分", h, m % 60);
  return tx("{0} 分钟", m);
}

function fmtWhen(ts: number | string | null | undefined): string {
  if (!ts) return "—";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return isNaN(d.getTime()) ? String(ts) : d.toLocaleString(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function isLive(s: ServiceRow) {
  return RUNNING.includes(s.state) || (s.key === "mcp" && (s.extra?.clients ?? 0) > 0);
}

// ── 服务卡片 ──────────────────────────────────────────────────────────
function ServiceCard({ s, onChanged, onLog }: { s: ServiceRow; onChanged: () => void; onLog: (key: string) => void }) {
  const m = SVC[s.key] ?? { name: s.key, desc: "", icon: <Server size={18} /> };
  const st = STATE[s.state] ?? STATE.error;
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmRestart, setConfirmRestart] = useState(false);
  const now = Date.now() / 1000;

  const run = async (action: "start" | "stop" | "restart") => {
    setBusy(action);
    try {
      const r = await api.post<{ ok?: boolean; error?: string; note?: string }>(`/api/services/${s.key}/${action}`);
      if (r?.ok === false) toast(tx("{0}：{1}", m.name, r.error ?? tx("没成功")), "error", 8000);
      else if (s.key === "console") toast(tx("远见后台正在重启，几秒后页面自动重连"), "info", 5000);
      else if (r?.note === "starting") toast(tx("{0} 启动中（第一次要先装依赖，可以点「日志」看进度）", m.name), "info", 5000);
      else toast(action === "stop" ? tx("{0} 已停止", m.name) : action === "restart" ? tx("{0} 已重启", m.name) : tx("{0} 已启动", m.name), "success");
    } catch (e) {
      toast(tx("{0}：{1}", m.name, (e as Error).message), "error", 8000);
    }
    setBusy(null);
    onChanged();
    broadcast({ type: "services" });
  };

  const can = (a: "start" | "stop" | "restart") => s.actions?.includes(a);
  const live = isLive(s);
  const btn = (a: "start" | "stop" | "restart", label: string, icon: ReactNode, cls = "btn sm") => (
    <button className={cls} disabled={!!busy} onClick={() => (s.key === "console" && a === "restart" ? setConfirmRestart(true) : run(a))}>
      {busy === a ? <span className="spinner sm" /> : icon}{label}
    </button>
  );

  const meta: ReactNode[] = [];
  if (s.port) meta.push(<span key="port">{tx("端口 {0}", s.port)}</span>);
  if (s.pids?.length) meta.push(<span key="pid">pid {s.pids.slice(0, 3).join(", ")}</span>);
  if (s.started && live) meta.push(<span key="up">{tx("已运行 {0}", fmtDur(now - s.started))}</span>);

  let note: ReactNode = null;
  if (s.state === "unavailable" && s.reason) note = <div className="svc-note">{REASON[s.reason] ?? s.reason}</div>;
  else if (s.state === "conflict") note = <div className="svc-note warn">{tx("端口 {0} 被别的程序占着：关掉那个程序，或在 openwar3.json 的 ports 里给它换一个端口。", s.port ?? "?")}</div>;
  else if (s.state === "error") note = <div className="svc-note warn mono">{s.error}</div>;
  else if (s.key === "console") note = (
    <div className="svc-meta">
      <span>Python {s.extra?.python}</span>
      <span>{tx("网页编译于 {0}", fmtWhen(s.extra?.webBuilt))}</span>
      <span>{tx("实例连接 {0} 个", s.extra?.workers ?? 0)}</span>
    </div>
  );
  else if (s.key === "llm") note = (
    <div className="svc-meta">
      <span title={s.extra?.model}>{tx("模型 {0}", s.extra?.want || s.extra?.model || "—")}</span>
      {(s.extra?.loaded ?? []).length > 0 && <span>{tx("已载入 {0} 个", s.extra!.loaded.length)}</span>}
    </div>
  );
  else if (s.key === "website" && s.state === "stopped" && !s.extra?.installed) note = <div className="svc-meta"><span>{tx("第一次启动会先装依赖（一两分钟）")}</span></div>;
  else if (s.key === "mcp") note = <div className="svc-meta"><span>{(s.extra?.clients ?? 0) > 0 ? tx("{0} 个客户端连着", s.extra!.clients) : tx("现在没有客户端连着")}</span></div>;

  return (
    <div className={`card svc-card ${live ? "running" : s.state}`}>
      <div className="svc-head">
        <span className="svc-ico">{m.icon}</span>
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="svc-name">{m.name}</div>
          <div className="svc-state"><span className={`dot ${st.dot}`} />{s.key === "mcp" && live ? tx("有客户端连着") : st.label}</div>
        </div>
        {s.url && s.state === "running" && m.open && (
          <a className="btn sm ghost" href={s.url} target="_blank" rel="noreferrer" title={s.url}><ExternalLink size={13} />{m.open}</a>
        )}
      </div>
      <div className="svc-desc">{m.desc}</div>
      {note}
      {meta.length > 0 && <div className="svc-meta">{meta}</div>}
      <div className="svc-actions">
        {s.key === "console" && btn("restart", tx("重启后台"), <RotateCw size={13} />)}
        {s.key !== "console" && !live && can("start") && s.state !== "unavailable" &&
          btn("start", s.key === "llm" ? tx("启动并载入模型") : tx("启动"), <Play size={13} />, "btn sm primary")}
        {s.key === "llm" && s.state === "partial" && btn("start", tx("载入模型"), <Play size={13} />, "btn sm primary")}
        {s.key !== "console" && live && can("stop") && btn("stop", s.key === "llm" ? tx("卸载模型") : tx("停止"), <Square size={13} />)}
        {s.key !== "console" && live && can("restart") && btn("restart", tx("重启"), <RotateCw size={13} />)}
        {(s.log || s.key === "llm") && <button className="btn sm ghost" onClick={() => onLog(s.key)}><FileText size={13} />{tx("日志")}</button>}
        {m.page && <button className="btn sm ghost" onClick={() => navigate(m.page!)}>{m.pageLabel}<ArrowRight size={13} /></button>}
      </div>
      <Dialog open={confirmRestart} title={tx("重启远见后台")} onClose={() => setConfirmRestart(false)}
        footer={<>
          <button className="btn" onClick={() => setConfirmRestart(false)}>{tx("取消")}</button>
          <button className="btn primary" onClick={() => { setConfirmRestart(false); run("restart"); }}>{tx("重启")}</button>
        </>}>
        <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
          {tx("后台重启要几秒，页面会自动重连。游戏、farm、AI、网关、气泡这些都是单独的进程，不受影响。改了远见服务端代码之后用。")}
        </div>
      </Dialog>
    </div>
  );
}

// ── 日志弹窗 ──────────────────────────────────────────────────────────
function LogDialog({ svc, onClose }: { svc: string | null; onClose: () => void }) {
  const { data, error } = useFetch<{ lines: string[]; path: string | null; missing?: boolean; error?: string }>(
    svc ? `/api/services/${svc}/log?lines=400` : null, 2000, [svc]);
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => { if (ref.current && stick.current) ref.current.scrollTop = ref.current.scrollHeight; }, [data]);
  const name = svc ? SVC[svc]?.name ?? svc : "";
  return (
    <Dialog open={!!svc} title={tx("{0} · 日志", name)} onClose={onClose} wide
      footer={<>
        <span className="faint mono grow ellipsis" style={{ fontSize: 11.5 }} title={data?.path ?? ""}>{data?.path ?? ""}</span>
        <button className="btn" onClick={onClose}>{tx("关闭")}</button>
      </>}>
      {!data ? <FetchState error={error} /> : data.missing ? <Empty title={tx("还没有日志")}>{tx("这个服务还没启动过")}</Empty> : (
        <pre ref={ref} className="svc-log mono" onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}>{data.lines.length ? data.lines.join("\n") : tx("（空）")}</pre>
      )}
    </Dialog>
  );
}

// ── 环境自检 ──────────────────────────────────────────────────────────
const CHECK_LABEL: Record<string, string> = {
  os: tx("操作系统"), python: "Python", packages: tx("Python 依赖包"), powershell: "Windows PowerShell", runtime: tx("注入运行时（DLL）"),
  game: tx("魔兽争霸 III"), map: tx("默认地图"), gamedata: tx("游戏数据"), amai: tx("AMAI 战略数据"), web: tx("远见网页"),
  node: tx("Node.js（可选）"), lmstudio: tx("LM Studio（可选）"), disk: tx("磁盘空间"),
};

function checkText(c: HealthCheck): string | null {
  const x = c.extra ?? {};
  switch (c.code) {
    case "os-error": return tx("需要 64 位 Windows 10 / 11。");
    case "python-error": return tx("需要 3.11 以上的 64 位 Python：命令行运行 start.bat setup 会装一份私有的。");
    case "packages-ok": return tx("全部就绪");
    case "packages-error": return tx("缺 {0}：重新双击 start.bat 会自动补装。", (x.missing ?? []).join(", "));
    case "packages-warn": return tx("可选的 {0} 没装（只有参考大脑的自动调参器用）。", (x.optional ?? []).join(", "));
    case "powershell-ok": return tx("Windows 自带，不用另装（远见靠它在后台起游戏和服务）");
    case "powershell-error": return tx("没找到 Windows 自带的 powershell.exe：远见靠它在后台起游戏和服务。系统文件可能损坏了，试试 sfc /scannow。");
    case "runtime-source": return tx("本机编译（有 native/ 源码）");
    case "runtime-prebuilt": return tx("随仓库发布的版本 {0}", x.version ?? "");
    case "runtime-ok": return tx("已就绪");
    case "runtime-stale": return tx("和 prebuilt/ 里发布的版本不一致（{0}）：先关掉游戏，再双击 start.bat 同步。", (x.diff ?? []).join(", "));
    case "runtime-missing": return tx("缺 {0}：双击 start.bat 会从 prebuilt/ 装上。", (x.missing ?? []).join(", "));
    case "game-ok": return tx("1.27a，版本对");
    case "game-unset": return tx("还没设置：在本页最上面设置魔兽争霸 III 的目录。");
    case "game-missing": return tx("游戏目录里没有 War3.exe / Game.dll：在本页最上面重新设置。");
    case "game-version": return tx("版本是 {0}，目前只支持 {1}：看本页最上面的温馨提示。", c.value ?? "?", x.want ?? "");
    case "map-warn": return tx("找不到默认地图，开局会失败：在 openwar3.json 里改 default_map。");
    case "gamedata-ok": return tx("已从你的游戏里提取");
    case "gamedata-error": return tx("缺 {0}：重新双击 start.bat 会从你的游戏里提取。", (x.missing ?? []).join(", "));
    case "amai-ok": return tx("已生成");
    case "amai-warn": return tx("没有：参考大脑跑不起来（示例 Bot 和别的方案不受影响）。命令行运行 start.bat setup 会下载并生成。");
    case "web-ok": return tx("随仓库发的编译结果，和源码一致（不用 Node.js）");
    case "web-stale": return tx("网页源码改过，和编好的对不上：重新双击 start.bat 会自动重编（要用 Node.js，没有会下一份）。");
    case "web-error": return tx("网页没编译：重新双击 start.bat 会自动编。");
    case "node-info": return tx("不用装：只有改了网页源码要重编、或者起官网预览时才要（命令行运行 start.bat node 装一份）。");
    case "lmstudio-ok": return tx("已安装");
    case "lmstudio-info": return tx("没装：气泡、运营顾问、玩伴的本地大模型要用。");
    case "disk-warn": case "disk-error": return tx("磁盘快满了（剩 {0}%）：去「数据与磁盘」看看哪些能清。", x.pct ?? "?");
    default: return null;
  }
}

function CheckIcon({ s }: { s: HealthCheck["status"] }) {
  if (s === "ok") return <CircleCheck size={16} className="success" />;
  if (s === "warn") return <TriangleAlert size={16} className="warning" />;
  if (s === "error") return <CircleX size={16} className="danger" />;
  return <Info size={16} className="faint" />;
}

function HealthCard({ health, error, onRefresh }: { health: HealthResp | null; error: string | null; onRefresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const order = { error: 0, warn: 1, info: 2, ok: 3 };
  const rows = useMemo(() => [...(health?.checks ?? [])].sort((a, b) => order[a.status] - order[b.status]), [health]);
  return (
    <Card title={tx("环境与部署")} icon={<ShieldCheck size={15} />} bodyClass="card-body tight"
      actions={<button className="btn sm ghost" disabled={busy} onClick={async () => { setBusy(true); await onRefresh(); setBusy(false); }}>
        {busy ? <span className="spinner sm" /> : <RefreshCw size={13} />}{tx("重新检查")}</button>}>
      {!health ? <FetchState error={error} /> : (
        <>
          {rows.map((c) => {
            const text = checkText(c);
            return (
              <div key={c.key} className={`check-row ${c.status}`}>
                <CheckIcon s={c.status} />
                <div className="check-label">{CHECK_LABEL[c.key] ?? c.key}</div>
                <div style={{ minWidth: 0 }}>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    {c.value && <b className="mono" style={{ fontSize: 12 }}>{c.key === "disk" ? tx("可用 {0}", c.value) : c.value}</b>}
                    {text && <span className="check-hint">{text}</span>}
                    {c.key === "disk" && c.status !== "ok" && <button className="btn sm ghost" onClick={() => navigate("storage")}>{tx("数据与磁盘")}<ArrowRight size={12} /></button>}
                  </div>
                  {c.path && <div className="check-path mono" title={c.path}>{c.path}</div>}
                </div>
              </div>
            );
          })}
          <div className="faint" style={{ fontSize: 12, padding: "10px 16px", lineHeight: 1.7 }}>
            {health.deploy?.at ? tx("上次部署 {0}（start.bat）", fmtWhen(health.deploy.at)) : tx("还没有部署记录")}
            {" · "}{tx("缺什么，重新双击 start.bat 会自动补齐；要强制重装 Python 包、重试 AMAI，在命令行运行 start.bat setup。游戏目录在本页最上面或「设置」页改。")}
          </div>
        </>
      )}
    </Card>
  );
}

// ── 游戏实例概况 ──────────────────────────────────────────────────────
function InstancesCard({ instances }: { instances: InstanceStatus[] }) {
  const now = Date.now() / 1000;
  const rows = [...instances].sort((a, b) => Number(b.game.alive) - Number(a.game.alive) || a.inst - b.inst).slice(0, 8);
  const alive = instances.filter((i) => i.game.alive);
  return (
    <Card title={tx("游戏实例")} icon={<Server size={15} />} bodyClass="card-body tight"
      actions={<button className="btn sm ghost" onClick={() => navigate("instances")}>{tx("实例与开局")}<ArrowRight size={13} /></button>}>
      {!alive.length ? (
        <Empty icon={<Server size={22} />} title={tx("还没有实例在跑")}>
          <div className="col gap8" style={{ alignItems: "center" }}>
            <span>{tx("在「实例与开局」选地图、种族、AI，点开始测试：游戏会自动开局，AI 接管。")}</span>
            <button className="btn sm primary" onClick={() => navigate("instances")}><Play size={13} />{tx("开第一局")}</button>
          </div>
        </Empty>
      ) : (
        <table className="table">
          <thead><tr><th>{tx("实例")}</th><th>{tx("游戏")}</th><th>{tx("AI")}</th><th>{tx("导播")}</th><th /></tr></thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.inst}>
                <td><b>{tx("实例 {0}", i.inst)}</b></td>
                <td>{i.game.alive
                  ? <span className="row" style={{ gap: 6 }}><span className="dot success" />{i.game.started ? tx("已运行 {0}", fmtDur(now - i.game.started)) : tx("运行中")}</span>
                  : <span className="faint">{i.held ? tx("已停止") : tx("没在跑")}</span>}</td>
                <td>{aiState(i).kind === "none" ? <span className="faint">{tx("没有 AI")}</span> : aiSentence(i)}</td>
                <td>{i.director.length ? <Badge tone="accent">{tx("开")}</Badge> : <span className="faint">—</span>}</td>
                <td className="num">
                  <button className="btn sm ghost" onClick={() => { setInst(i.inst); navigate("overview"); }}>{tx("查看")}<ArrowRight size={12} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ── 全部停止 ──────────────────────────────────────────────────────────
// 和命令行 start.bat stop / stop.bat 是同一套（console/server/services.py stop_everything）
interface StopResult {
  instances: { stopped: number[] };
  services: Record<string, number[]>;
  leftovers: number[];
  llm?: { ok: boolean; unloaded?: string[]; error?: string | null; note?: string };
  mcp: number;
  console?: string;
}

function StopAllDialog({ open, onClose, svcs, alive, onDone }: {
  open: boolean; onClose: () => void; svcs: ServiceRow[]; alive: number; onDone: (r: StopResult, consoleToo: boolean) => void;
}) {
  const llm = svcs.find((s) => s.key === "llm");
  const llmOn = !!llm && ["running", "partial", "starting"].includes(llm.state);
  const running = svcs.filter((s) => ["gateway", "speech", "website"].includes(s.key) && RUNNING.includes(s.state));
  const [unload, setUnload] = useState(true);
  const [consoleToo, setConsoleToo] = useState(true);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post<StopResult>("/api/system/stop", { llm: unload && llmOn, console: consoleToo });
      onDone(r, consoleToo);
    } catch (e) {
      toast(tx("全部停止：{0}", (e as Error).message), "error", 8000);
    }
    setBusy(false);
  };
  return (
    <Dialog open={open} title={<span className="row" style={{ gap: 8 }}><Power size={16} />{tx("全部停止")}</span>} onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>{tx("取消")}</button>
        <button className="btn danger" disabled={busy} onClick={go}>{busy ? <span className="spinner sm" /> : <Power size={14} />}{tx("全部停止")}</button>
      </>}>
      <div className="col gap12" style={{ fontSize: 13, lineHeight: 1.7 }}>
        <div className="muted">{tx("按顺序停掉这台机器上 OpenWar3 起的所有东西：")}</div>
        <div className="stop-list">
          <div><b>{tx("游戏实例")}</b><span>{alive ? tx("{0} 个在跑：游戏、farm、AI、导播一起停，之后不会自己重开", alive) : tx("没有在跑的")}</span></div>
          <div><b>{tx("服务")}</b><span>{running.length ? running.map((s) => SVC[s.key]?.name ?? s.key).join("、") : tx("没有在跑的")}</span></div>
          <label className={llmOn ? "" : "faint"}>
            <input type="checkbox" checked={unload && llmOn} disabled={!llmOn} onChange={(e) => setUnload(e.target.checked)} />
            <span><b>{tx("本地大模型")}</b> {llmOn ? tx("卸载本系统用的模型，释放显存（LM Studio 程序本身不关）") : tx("没载入")}</span>
          </label>
          <label>
            <input type="checkbox" checked={consoleToo} onChange={(e) => setConsoleToo(e.target.checked)} />
            <span><b>{tx("远见后台")}</b> {tx("最后把它也关掉：这个网页会断开，要再用就双击 start.bat")}</span>
          </label>
        </div>
        <div className="faint" style={{ fontSize: 12 }}>
          {tx("不会停：MCP 服务器（归 Claude 等大模型客户端管）、不是本系统起的魔兽窗口。命令行同款：双击 stop.bat。")}
        </div>
      </div>
    </Dialog>
  );
}

function stopSummary(r: StopResult): string[] {
  const names: Record<string, string> = { gateway: SVC.gateway.name, speech: SVC.speech.name, website: SVC.website.name };
  const svc = Object.entries(r.services).filter(([, v]) => v.length).map(([k]) => names[k] ?? k);
  const out = [
    r.instances.stopped.length ? tx("游戏实例：停了 {0}", r.instances.stopped.map((n) => tx("实例 {0}", n)).join("、")) : tx("游戏实例：没有在跑的"),
    svc.length ? tx("服务：停了 {0}", svc.join("、")) : tx("服务：没有在跑的"),
  ];
  if (r.leftovers.length) out.push(tx("另外收掉 {0} 个漏网的组件进程", r.leftovers.length));
  if (r.llm?.unloaded?.length) out.push(tx("本地大模型：已卸载 {0}", r.llm.unloaded.join(", ")));
  else if (r.llm && !r.llm.ok) out.push(tx("本地大模型没卸成：{0}", r.llm.error ?? "?"));
  if (r.mcp) out.push(tx("还有 {0} 个 MCP 服务器连着大模型客户端，关掉客户端就会退", r.mcp));
  return out;
}

// ── 版本 / 检查更新 ───────────────────────────────────────────────────
function VersionChip() {
  const { data, reload } = useUpdate();
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try {
      const r = await api.post<UpdateInfo>("/api/update/check");
      toast(r.error ? tx("连不上 War3AI.com，稍后再试") : r.newer ? tx("有新版本 {0}", r.latest ?? "") : tx("已经是最新版本"), r.error ? "error" : "success");
    } catch (e) { toast((e as Error).message, "error"); }
    setBusy(false);
    reload();
  };
  if (!data) return null;
  return (
    <span className="row" style={{ gap: 4 }}>
      {data.newer
        ? <a className="badge accent" href={data.url ?? "https://war3ai.com/"} target="_blank" rel="noreferrer" title={tx("去 War3AI.com 看更新")}>{tx("有新版本 v{0}", data.latest ?? "")}</a>
        : <Badge tone={data.latest ? "success" : ""} title={data.error ? tx("还没问到 War3AI.com：{0}", data.error) : undefined}>v{data.current}{data.latest ? tx(" · 最新") : ""}</Badge>}
      <button className="btn sm ghost icon" disabled={busy} onClick={check} title={tx("检查更新")}>{busy ? <span className="spinner sm" /> : <ArrowUpCircle size={13} />}</button>
    </span>
  );
}

// ── 扩展入口 ──────────────────────────────────────────────────────────
const EXT: { page: string; icon: ReactNode; title: string; desc: string }[] = [
  { page: "instances", icon: <Play size={17} />, title: tx("实例与开局"), desc: tx("开新局：地图、种族、难度、倍速、用哪个 AI") },
  { page: "schemes", icon: <Package size={17} />, title: tx("AI 方案"), desc: tx("切换 Bot 和玩法模组，导入别人的、导出自己的") },
  { page: "connect", icon: <Plug size={17} />, title: tx("接入与扩展"), desc: tx("网关、MCP、Python SDK：把游戏接到别的程序和大模型上") },
  { page: "jass", icon: <TerminalSquare size={17} />, title: tx("JASS 控制台"), desc: tx("按名字调 1291 个 JASS 函数，写脚本点运行") },
  { page: "events", icon: <MousePointerClick size={17} />, title: tx("事件与输入"), desc: tx("这一局的事件流、按钮热键、鼠标和选中") },
  { page: "director", icon: <Clapperboard size={17} />, title: tx("导播台"), desc: tx("自动运镜、头顶血条、镜头预设") },
  { page: "speech", icon: <MessageSquareText size={17} />, title: tx("头顶气泡"), desc: tx("让单位说话、和 AI 对话；茶话会、镜头对白、战况触发") },
  { page: "storage", icon: <HardDrive size={17} />, title: tx("数据与磁盘"), desc: tx("录制、对局记录、日志各占多少，磁盘还能撑几天") },
  { page: "settings", icon: <Settings size={17} />, title: tx("设置"), desc: tx("外观、语言、对局运行方式") },
  { page: "feedback", icon: <MessageCircleHeart size={17} />, title: tx("反馈与建议"), desc: tx("遇到问题、有想法，直接发给 War3AI.com") },
];

export default function Home() {
  const instances = useApp((s) => s.instances);
  const { data, error, reload } = useFetch<ServicesResp>("/api/services", 3000);
  const hf = useFetch<HealthResp>("/api/health", 30000);
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [logKey, setLogKey] = useState<string | null>(null);
  const [stopOpen, setStopOpen] = useState(false);
  // 游戏目录（玩家自己设）：还没设 / 不对 / 版本不支持 / 正在提取数据时，最上面放设置卡片；提取中每 2 秒刷新
  const [gdPoll, setGdPoll] = useState(15000);
  const gd = useFetch<GameDirStatus>("/api/gamedir", gdPoll, [gdPoll]);
  useEffect(() => setGdPoll(gd.data?.job.running ? 2000 : 15000), [gd.data?.job.running]);
  const g = gd.data;
  const needGameDir = !!g && (!g.configured || !g.ok || !g.supported || !g.dataReady || g.job.running || g.job.ok === false);
  useEffect(() => { if (hf.data) setHealth(hf.data); }, [hf.data]);
  // 别的标签页起停了服务：马上刷新，不等下一次轮询
  useEffect(() => onBroadcast((m) => { if (m.type === "services") reload(); }), [reload]);
  const refreshHealth = async () => {
    try { setHealth(await api.get<HealthResp>("/api/health?refresh=1")); } catch (e) { toast((e as Error).message, "error"); }
  };

  const svcs = data?.services ?? [];
  const live = svcs.filter(isLive);
  const idle = svcs.filter((s) => !isLive(s));
  const managed = svcs.filter((s) => s.key !== "mcp");
  const alive = instances.filter((i) => i.game.alive);
  const withAi = alive.filter((i) => aiState(i).kind !== "none").length;
  const withDirector = alive.filter((i) => i.director.length).length;
  const sum = health?.summary;
  const disk = health?.checks.find((c) => c.key === "disk");
  const errors = (health?.checks ?? []).filter((c) => c.status === "error");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("控制中心")}</h1>
          <p className="page-desc">
            {tx("这台机器上 OpenWar3 的全部服务：哪些在跑、还能启动什么、环境是否就绪。游戏实例在「实例与开局」页开和停。")}
          </p>
        </div>
        <div className="right row">
          <VersionChip />
          <button className="btn sm" onClick={() => navigate("feedback")} title={tx("遇到问题、有建议，直接发给 War3AI.com")}><MessageCircleHeart size={13} />{tx("反馈问题")}</button>
          <button className="btn sm" onClick={() => { reload(); refreshHealth(); }}><RefreshCw size={13} />{tx("刷新")}</button>
          <button className="btn sm danger" onClick={() => setStopOpen(true)}
            title={tx("停掉游戏实例、AI、所有服务，最后关闭远见后台（同 stop.bat）")}><Power size={13} />{tx("全部停止")}</button>
        </div>
      </div>
      {stopOpen && (
        <StopAllDialog open onClose={() => setStopOpen(false)} svcs={svcs} alive={alive.length}
          onDone={(r, consoleToo) => {
            setStopOpen(false);
            const lines = stopSummary(r);
            if (consoleToo) {
              // 整页换成「已停止」；别的标签页也一起换（它们的后台连接马上也会断）
              store.set({ stopped: lines });
              broadcast({ type: "stopped", lines });
            } else {
              toast(lines.join("；"), "success", 8000);
              reload();
              broadcast({ type: "services" });
            }
          }} />
      )}

      {needGameDir && (
        <div style={{ marginBottom: 14 }}>
          <GameDirSetup status={g} title={g?.configured ? tx("魔兽争霸 III 目录") : tx("第一步：设置魔兽争霸 III 目录")}
            onChanged={() => { gd.reload(); refreshHealth(); }} />
        </div>
      )}

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label={tx("服务在跑")} icon={<Activity size={14} />}
          value={data ? `${managed.filter(isLive).length} / ${managed.length}` : "—"}
          sub={live.length ? live.map((s) => SVC[s.key]?.name ?? s.key).join(" · ") : undefined} />
        <Stat label={tx("游戏实例")} icon={<Server size={14} />} value={tx("{0} 个在跑", alive.length)}
          sub={alive.length ? tx("AI 在指挥 {0} · 导播 {1}", withAi, withDirector) : tx("还没开局")} />
        <Stat label={tx("环境自检")} icon={<ShieldCheck size={14} />}
          tone={!sum ? "" : sum.error ? "danger" : sum.warn ? "warning" : "success"}
          value={!sum ? "—" : sum.error ? tx("{0} 项没就绪", sum.error) : sum.warn ? tx("{0} 项提醒", sum.warn) : tx("全部就绪")}
          sub={sum ? tx("通过 {0} / {1}", sum.ok, health!.checks.length) : undefined} />
        <Stat label={tx("磁盘 {0}", disk?.path ?? "")} icon={<HardDrive size={14} />}
          tone={disk?.status === "error" ? "danger" : disk?.status === "warn" ? "warning" : ""}
          value={disk?.value ?? "—"} sub={disk?.extra?.pct != null ? tx("剩 {0}%", disk.extra.pct) : undefined} />
      </div>

      {errors.length > 0 && (
        <div className="card home-alert">
          <CircleX size={16} className="danger" style={{ flex: "none", marginTop: 2 }} />
          <div>
            <b>{tx("环境有 {0} 项没就绪：{1}", errors.length, errors.map((c) => CHECK_LABEL[c.key] ?? c.key).join("、"))}</b>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>{tx("关掉这个页面，重新双击 start.bat 会自动补齐；详情见下面「环境与部署」。")}</div>
          </div>
        </div>
      )}

      {!data ? <FetchState error={error} onRetry={reload} /> : (
        <>
          <div className="home-section"><span className="dot success" />{tx("正在运行")}<span className="faint">{live.length}</span></div>
          <div className="svc-grid">
            {live.map((s) => <ServiceCard key={s.key} s={s} onChanged={reload} onLog={setLogKey} />)}
          </div>
          <div className="home-section"><LayoutGrid size={14} />{tx("可以启动")}<span className="faint">{idle.length}</span>
            <span className="faint" style={{ fontWeight: 400, fontSize: 12 }}>{tx("按需要点启动；这些都是可选的，不开也能跑游戏和 AI。")}</span></div>
          <div className="svc-grid">
            {idle.map((s) => <ServiceCard key={s.key} s={s} onChanged={reload} onLog={setLogKey} />)}
          </div>
        </>
      )}

      <div className="grid g2" style={{ marginTop: 22, alignItems: "start" }}>
        <InstancesCard instances={instances} />
        <HealthCard health={health} error={hf.error} onRefresh={refreshHealth} />
      </div>

      <div className="home-section" style={{ marginTop: 22 }}><ArrowRight size={14} />{tx("扩展与开发")}</div>
      <div className="grid g4">
        {EXT.map((e) => (
          <button key={e.page} className="ext-tile" onClick={() => navigate(e.page)}>
            <span className="svc-ico">{e.icon}</span>
            <span className="col" style={{ gap: 2, minWidth: 0 }}>
              <b style={{ fontSize: 13.5 }}>{e.title}</b>
              <span className="faint" style={{ fontSize: 12, lineHeight: 1.5 }}>{e.desc}</span>
            </span>
          </button>
        ))}
      </div>
      <LogDialog svc={logKey} onClose={() => setLogKey(null)} />
    </div>
  );
}
