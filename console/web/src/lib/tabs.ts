// 多标签页（09-26）：常常同时开好几个远见标签页看不同的东西（一个看战场、一个看气泡、一个看日志……）。
//
//   * 每个标签页各看各的实例：实例号在地址里（#/页面?i=实例号，见 lib/store.ts 的 pageHref / setInst）
//   * 标签页之间同步：主题（换了马上跟着换）、界面语言（提示刷新）、「最近更新」看过了（别的标签页的弹窗一起关）、
//     系统已全部停止（所有标签页都显示「已停止」）、服务起停了（控制中心马上刷新，不等下一次轮询）
//   * 后台标签页不收实时快照（lib/live.ts 按「在不在前台 + 这页要不要实时画面」要速率）
//   * 标签页标题 = 页面名 · 实例号（App.tsx），一排标签页也分得清
//
// 主题 / 语言 / 看过的更新本来就存在 localStorage：别的标签页改了会收到 storage 事件，不用另外发消息；
// 「已停止」「服务变了」这种一次性的事用 BroadcastChannel。
import { parseHash, pageHref, store } from "./store";
import { reloadTheme } from "./theme";

export type TabMsg = { type: "stopped"; lines: string[] } | { type: "services" };

const chan: BroadcastChannel | null = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("farsight") : null;
const handlers = new Set<(m: TabMsg) => void>();

/** 告诉同一个远见的其它标签页（自己收不到） */
export function broadcast(m: TabMsg) {
  try { chan?.postMessage(m); } catch { /* 关着的频道 */ }
}

export function onBroadcast(fn: (m: TabMsg) => void): () => void {
  handlers.add(fn);
  return () => { handlers.delete(fn); };
}

export function startTabs() {
  // 地址里还没有实例号（老书签、直接打开根地址）：补上，刷新 / 复制链接都能回到同一个实例
  const { page, inst } = parseHash();
  if (!inst) history.replaceState(null, "", pageHref(page));

  chan?.addEventListener("message", (ev: MessageEvent<TabMsg>) => {
    const m = ev.data;
    if (m?.type === "stopped") store.set({ stopped: m.lines, changelogOpen: false, paletteOpen: false });
    handlers.forEach((h) => h(m));
  });

  window.addEventListener("storage", (ev) => {
    if (ev.key === "farsight.theme") reloadTheme();
    else if (ev.key === "farsight.lang") store.set({ langChanged: true });
    else if (ev.key === "farsight.seenChange") store.set({ changelogOpen: false });
  });
}
