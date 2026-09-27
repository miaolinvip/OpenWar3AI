// 实时连接：/ws/i/{n}（快照 / 大脑 / worker 状态 / 日志行）+ /ws/global（实例列表）
import { store, toast } from "./store";
import { tx } from "./i18n";

type LogHandler = (key: string, lines: string[]) => void;
const logHandlers = new Set<LogHandler>();
let wantLogs: string[] = [];
let ws: WebSocket | null = null;
let wsInst = -1;
let retry = 0;
let hz = Number(localStorage.getItem("farsight.hz") || 4);
let sentHz = -1;

// 要实时画面的页面按设置的速率收快照；别的页面只有顶栏的资源数在用，1 帧/秒够了；标签页在后台就暂停（0）。
// ⚠ 09-26 以前所有标签页、所有页面都按 4 帧/秒收整份快照：开几个标签页，浏览器和后台就白干几倍的活。
const LIVE_PAGES = new Set(["overview", "battlefield", "units", "director", "events"]);

function wantHz(): number {
  if (document.hidden) return 0;
  return LIVE_PAGES.has(store.get().page) ? hz : Math.min(hz, 1);
}

function syncRate(force = false) {
  const w = wantHz();
  if (!ws || ws.readyState !== WebSocket.OPEN || (w === sentHz && !force)) return;
  ws.send(JSON.stringify({ type: "rate", hz: w }));
  sentHz = w;
}

function url(path: string) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

function connect() {
  const inst = store.get().inst;
  wsInst = inst;
  store.set({ ws: "connecting" });
  const sock = new WebSocket(url(`/ws/i/${inst}`));
  ws = sock;
  sock.onopen = () => {
    retry = 0;
    store.set({ ws: "open" });
    syncRate(true);
    if (wantLogs.length) sock.send(JSON.stringify({ type: "logs", keys: wantLogs }));
  };
  sock.onmessage = (ev) => {
    if (ws !== sock) return;
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "hello":
        store.set({ worker: msg.data.status, brain: msg.data.brain ?? store.get().brain, snapshot: msg.data.snapshot ?? store.get().snapshot });
        break;
      case "snapshot":
        store.set({ snapshot: msg.data, snapAt: performance.now() });
        break;
      case "brain":
        store.set({ brain: msg.data });
        break;
      case "status":
        store.set({ worker: msg.data });
        break;
      case "log":
        logHandlers.forEach((h) => h(msg.data.key, msg.data.lines));
        break;
    }
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    store.set({ ws: "closed" });
    const wait = Math.min(8000, 500 * 2 ** retry++);
    setTimeout(() => {
      if (ws === sock) connect();
    }, wait);
  };
}

export function startLive() {
  connect();
  store.subscribe(() => {
    if (store.get().inst !== wsInst) {
      const old = ws;
      ws = null;
      old?.close();
      connect();
    } else {
      syncRate();                                  // 换了页面：要不要实时画面变了
    }
  });
  document.addEventListener("visibilitychange", () => syncRate());
  // 实例列表
  const g = () => {
    const sock = new WebSocket(url("/ws/global"));
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "instances") store.set({ instances: msg.data });
      if (msg.type === "issues") {
        // 游戏里按了问题快捷键：待填写数变多就提示一下（第一次收到不提示）
        const before = store.get().issuesPending;
        if (before != null && msg.pending > before) toast(tx("游戏里记下了 {0} 个问题，到「问题备忘」补一句描述", msg.pending - before), "info", 6000);
        store.set({ issuesPending: msg.pending });
      }
    };
    sock.onclose = () => setTimeout(g, 3000);
  };
  g();
}

export function setRate(v: number) {
  hz = v;
  localStorage.setItem("farsight.hz", String(v));
  syncRate(true);
}

export function followLogs(keys: string[], handler: LogHandler) {
  wantLogs = keys;
  logHandlers.add(handler);
  ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "logs", keys }));
  return () => {
    logHandlers.delete(handler);
    wantLogs = [];
    ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "logs", keys: [] }));
  };
}
