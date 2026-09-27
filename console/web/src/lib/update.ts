// 新版本提醒（09-26）：后端 console/server/update.py 启动时和每 6 小时问 war3ai.com/version.json，这里只读它的结果。
// 顶上的横幅、侧栏底部的「有新版本」、控制中心的版本标签都用这一份。
import { useFetch } from "../components/ui";

export interface UpdateInfo {
  current: string; latest: string | null; newer: boolean; url: string | null; released: string | null;
  notes: Record<string, string> | null; checkedAt: number | null; error: string | null;
}

export const UPDATE_DISMISS = "farsight.update.dismissed";

export function useUpdate() {
  return useFetch<UpdateInfo>("/api/update", 30 * 60 * 1000);
}
