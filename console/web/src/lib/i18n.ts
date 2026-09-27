// 界面语言（09-25）。源码里照写中文、包一层 tx("中文")；别的语言按「中文原文」查字典，查不到就显示原文。
//
//   tx("保存")                       -> "Save"
//   tx("共 {0} 局，赢 {1}", n, w)     -> "{0} games, {1} won"（占位符按参数顺序替换，译文里可以换位置）
//
// 加一种语言：写 src/i18n/<代码>.json（键 = 中文原文，值 = 译文；可以只翻一部分，没翻的显示中文），
// 再在下面 LANGS 和 DICTS 各加一行。console/tests/test_i18n.py 会检查源码里的每个 tx 键在 en.json 里都有译文。
//
// 切换语言 = 存进 localStorage 再刷新页面：模块顶层的表（页面名、单位名…）在加载时就按当前语言算好了，
// 刷新一次最简单也最不容易漏。
import en from "../i18n/en.json";

export type Lang = "zh" | "en";

export const LANGS: { code: Lang; name: string; htmlLang: string }[] = [
  { code: "zh", name: "中文", htmlLang: "zh-CN" },
  { code: "en", name: "English", htmlLang: "en" },
];

const DICTS: Partial<Record<Lang, Record<string, string>>> = { en };
const KEY = "farsight.lang";

function detect(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && LANGS.some((l) => l.code === saved)) return saved as Lang;
  } catch { /* 隐私模式读不了就按浏览器语言 */ }
  const nav = (typeof navigator !== "undefined" && navigator.language) || "zh";
  return nav.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export const lang: Lang = detect();
const dict: Record<string, string> | null = DICTS[lang] || null;
try { if (lang !== "zh") document.title = "Farsight"; } catch { /* 非浏览器环境 */ }
// 没翻到的键（调试用：浏览器控制台看 window.__i18nMissing）
const missing = new Set<string>();
try {
  document.documentElement.lang = LANGS.find((l) => l.code === lang)?.htmlLang || "zh-CN";
  (window as unknown as { __i18nMissing: Set<string> }).__i18nMissing = missing;
} catch { /* 非浏览器环境 */ }

export function tx(s: string, ...args: unknown[]): string {
  let out = s;
  if (dict) {
    const hit = dict[s] || runtime[s];
    if (hit) out = hit;
    else missing.add(s);
  }
  if (args.length) out = out.replace(/\{(\d+)\}/g, (m, i) => (+i < args.length ? String(args[+i]) : m));
  return out;
}

// 运行时补充的译文：后端数据里的名字（单位/技能中文名 -> 英文名，/api/meta 的 nameMap）。
// 只查「整段等于这个名字」的，所以 tx(后端给的中文名) 在英文界面能显示成英文名
const runtime: Record<string, string> = {};
export function addTranslations(map: Record<string, string> | undefined | null) {
  if (dict && map) Object.assign(runtime, map);
}

/** 按界面语言挑数据里的多语言字段：方案说明书的 i18n: {en: {name, description}} */
export function localized<T extends { i18n?: Record<string, Record<string, string> | undefined> }>(obj: T | null | undefined, field: string): string {
  if (!obj) return "";
  const v = obj.i18n?.[lang]?.[field];
  return v || String((obj as unknown as Record<string, unknown>)[field] ?? "");
}

/** 方案列表：名字/简介换成当前语言的（scheme.json 的 i18n 字段；作者没写就保持原样） */
export function localizeSchemes<T extends { schemes: { name: string; description?: string; i18n?: Record<string, Record<string, string> | undefined> }[] }>(d: T | null | undefined): T | null {
  if (!d || lang === "zh") return d ?? null;
  return { ...d, schemes: d.schemes.map((s) => ({ ...s, name: localized(s, "name"), description: localized(s, "description") })) };
}

/** 当前语言是不是中文（有些地方要按语言挑数据字段，例如 native 说明的 zh / en） */
export const isZh = lang === "zh";

export function setLang(code: Lang) {
  try { localStorage.setItem(KEY, code); } catch { /* 存不了也照样刷新 */ }
  location.reload();
}

/** 日期/数字格式用的区域码 */
export const locale = lang === "zh" ? "zh-CN" : "en-US";
