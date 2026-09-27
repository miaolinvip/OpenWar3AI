import { useSyncExternalStore } from "react";
import { createStore } from "./store";
import { tx } from "./i18n";

export type Mode = "light" | "dark" | "system";
export type Style = "modern" | "war3";
export interface Theme { mode: Mode; style: Style; accent: string }

export const ACCENTS: { name: string; hex: string }[] = [
  { name: tx("湛蓝"), hex: "#3b82f6" },
  { name: tx("靛青"), hex: "#6366f1" },
  { name: tx("紫晶"), hex: "#8b5cf6" },
  { name: tx("青碧"), hex: "#06b6d4" },
  { name: tx("翠绿"), hex: "#10b981" },
  { name: tx("琥珀"), hex: "#f59e0b" },
  { name: tx("赤红"), hex: "#ef4444" },
  { name: tx("玫瑰"), hex: "#ec4899" },
  { name: tx("金"), hex: "#d4a93a" },
];

function load(): Theme {
  try {
    const t = JSON.parse(localStorage.getItem("farsight.theme") || "{}");
    return { mode: t.mode ?? "dark", style: t.style ?? "modern", accent: t.accent ?? (t.style === "war3" ? "#d4a93a" : "#3b82f6") };
  } catch {
    return { mode: "dark", style: "modern", accent: "#3b82f6" };
  }
}

export const themeStore = createStore<Theme>(load());

export function hexToHsl(hex: string): [number, number, number] {
  const m = hex.replace("#", "");
  const n = parseInt(m.length === 3 ? m.split("").map((c) => c + c).join("") : m, 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  return [Math.round(h * 360), Math.round(s * 100), Math.round(l * 100)];
}

const mq = matchMedia("(prefers-color-scheme: dark)");

export function resolvedMode(t: Theme = themeStore.get()): "light" | "dark" {
  return t.mode === "system" ? (mq.matches ? "dark" : "light") : t.mode;
}

export function applyTheme(t: Theme = themeStore.get()) {
  const root = document.documentElement;
  const mode = resolvedMode(t);
  root.dataset.mode = mode;
  root.dataset.style = t.style;
  const [h, s, l] = hexToHsl(t.accent);
  // 深色模式下太暗的主题色抬一点亮度，保证按钮文字和图标的对比度
  const ll = mode === "dark" ? Math.max(l, 52) : Math.min(Math.max(l, 38), 56);
  root.style.setProperty("--accent-h", String(h));
  root.style.setProperty("--accent-s", `${s}%`);
  root.style.setProperty("--accent-l", `${ll}%`);
  localStorage.setItem("farsight.theme", JSON.stringify(t));
}

export function setTheme(p: Partial<Theme>) {
  themeStore.set(p);
  applyTheme();
}

/** 别的标签页改了主题（lib/tabs.ts 收到 storage 事件）：这边照着换 */
export function reloadTheme() {
  themeStore.set(load());
  applyTheme();
}

mq.addEventListener("change", () => themeStore.get().mode === "system" && applyTheme());

export function useTheme() {
  return useSyncExternalStore(themeStore.subscribe, themeStore.get);
}
