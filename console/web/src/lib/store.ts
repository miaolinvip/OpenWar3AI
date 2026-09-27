import { useSyncExternalStore } from "react";
import type { BrainLive, InstanceStatus, Meta, Snapshot, WorkerStatus } from "./types";

type Listener = () => void;

export function createStore<T extends object>(init: T) {
  let state = init;
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(patch: Partial<T> | ((s: T) => Partial<T>)) {
      const p = typeof patch === "function" ? (patch as (s: T) => Partial<T>)(state) : patch;
      state = { ...state, ...p };
      listeners.forEach((l) => l());
    },
    subscribe(l: Listener) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

export interface Toast { id: number; kind: "success" | "error" | "info"; text: string }
export type Armed = null | { kind: "move" | "attack" | "patrol" | "smart" | "rally" } | { kind: "cast"; code: string; order: number; target: string; caster: number } | { kind: "build"; code: string };

export interface AppState {
  inst: number;
  page: string;
  meta: Meta | null;
  instances: InstanceStatus[];
  snapshot: Snapshot | null;
  snapAt: number;
  brain: BrainLive | null;
  worker: WorkerStatus | null;
  ws: "connecting" | "open" | "closed";
  selection: number[];
  focus: number | null;
  armed: Armed;
  toasts: Toast[];
  paletteOpen: boolean;
  collapsed: boolean;
  issuesPending: number | null;   // 问题备忘「待填写」数（/ws/global 每 3 秒推）；null = 还没收到
  updateReady: boolean;           // 控制台有新构建：顶部横幅让人自己点刷新（以前 1.2 秒后强制刷新，没保存的输入会丢）
  changelogOpen: boolean;         // 「最近更新」弹窗
  langChanged: boolean;           // 别的标签页换了界面语言：顶部横幅提示刷新（lib/tabs.ts）
  stopped: string[] | null;       // 系统已「全部停止」（这个或别的标签页点的）：整页换成「已停止」，内容是停了什么
}

// ── 多标签页（09-26）：页面和实例号都在地址里（#/battlefield?i=6）──────────────
// 每个标签页各看各的实例：切实例只改这个标签页（地址 + sessionStorage），不会把别的标签页带走；
// 刷新、收藏、Ctrl+点击侧栏开新标签页都保持同一个页面和实例。localStorage 只记「最近用的」，给新开的标签页当默认值。
const INST_KEY = "farsight.inst";

export function parseHash(): { page: string; inst: number | null } {
  const [path, q] = location.hash.replace(/^#\/?/, "").split("?");
  const i = Number(new URLSearchParams(q || "").get("i"));
  return { page: path || "home", inst: Number.isInteger(i) && i > 0 ? i : null };
}

function initialInst(): number {
  const fromUrl = parseHash().inst;
  if (fromUrl) return fromUrl;
  for (const s of [() => sessionStorage.getItem(INST_KEY), () => localStorage.getItem(INST_KEY)]) {
    try {
      const n = Number(s());
      if (Number.isInteger(n) && n > 0) return n;
    } catch { /* 隐私模式读不了 */ }
  }
  return 5;
}

export const store = createStore<AppState>({
  inst: initialInst(),
  page: parseHash().page,
  meta: null,
  instances: [],
  snapshot: null,
  snapAt: 0,
  brain: null,
  worker: null,
  ws: "connecting",
  selection: [],
  focus: null,
  armed: null,
  toasts: [],
  paletteOpen: false,
  collapsed: localStorage.getItem("farsight.collapsed") === "1",
  issuesPending: null,
  updateReady: false,
  changelogOpen: false,
  langChanged: false,
  stopped: null,
});

export function useApp<S>(sel: (s: AppState) => S): S {
  return useSyncExternalStore(store.subscribe, () => sel(store.get()));
}

let toastSeq = 1;
export function toast(text: string, kind: Toast["kind"] = "info", ms = 3200) {
  const id = toastSeq++;
  store.set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
  setTimeout(() => store.set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), ms);
}

/** 页面的地址（带这个标签页的实例号）：侧栏链接、「在新标签页打开」都用它 */
export function pageHref(page: string, inst: number = store.get().inst): string {
  return `#/${page}?i=${inst}`;
}

export function navigate(page: string) {
  const target = pageHref(page);
  if (location.hash !== target) location.hash = target;
  store.set({ page });
}

window.addEventListener("hashchange", () => {
  const { page, inst } = parseHash();
  if (page !== store.get().page) store.set({ page });
  if (inst && inst !== store.get().inst) setInst(inst);          // 后退 / 前进到别的实例
});

export function setInst(inst: number) {
  try {
    sessionStorage.setItem(INST_KEY, String(inst));
    localStorage.setItem(INST_KEY, String(inst));
  } catch { /* 存不了就只在这次打开里生效 */ }
  if (inst !== store.get().inst) {
    store.set({ inst, snapshot: null, brain: null, worker: null, selection: [], focus: null, armed: null });
  }
  const target = pageHref(store.get().page, inst);
  if (location.hash !== target) history.replaceState(null, "", target);   // 只改地址、不多一条历史
}
