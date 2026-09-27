import { createRoot } from "react-dom/client";
import "./styles.css";
import App from "./App";
import { applyTheme } from "./lib/theme";
import { startLive } from "./lib/live";
import { startTabs } from "./lib/tabs";
import { store, toast } from "./lib/store";
import { api } from "./lib/api";
import type { Meta } from "./lib/types";
import { addTranslations, lang, tx } from "./lib/i18n";

applyTheme();
startTabs();
startLive();
// 名字表按界面语言要（英文界面 = 英文单位/技能名 + 中文名 -> 英文名的对照，给后端直接给中文名的地方用）
api.get<Meta & { nameMap?: Record<string, string> }>(`/api/meta?lang=${lang}`).then((meta) => {
  addTranslations(meta.nameMap);
  store.set({ meta });
  // 控制台自己更新：构建产物变了就在顶部亮一条横幅，点「刷新」再刷（09-25 以前是 1.2 秒后强制刷新，
  // 正在写的 JASS 脚本、问题备忘会丢）。
  const ver = (meta as unknown as { webVer?: number }).webVer;
  if (ver) {
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        // 只问版本号（以前问整份 /api/meta，一次 196 KB）；老后端没有 /api/version 就退回 /api/meta
        const m2 = await api.get<{ webVer?: number }>("/api/version").catch(() => api.get<{ webVer?: number }>("/api/meta"));
        if (m2.webVer && m2.webVer !== ver) {
          clearInterval(timer);
          store.set({ updateReady: true });
          toast(tx("控制台有新版本：点顶部的「刷新」载入"), "info", 5000);
        }
      } catch { /* 取不到就算了 */ }
    }, 20000);
  }
}).catch(() => {});

createRoot(document.getElementById("root")!).render(<App />);
