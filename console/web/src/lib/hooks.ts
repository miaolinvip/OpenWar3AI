import { useEffect, useMemo, useState } from "react";
import { useApp } from "./store";
import { api } from "./api";
import type { CamStatus, MapInfo, Unit } from "./types";
import { toUnit } from "./types";

// 相对地图目录（openwar3.json 的 maps_dir）；runtime/farm.py 和 mapinfo 都按地图目录补全
export const DEFAULT_MAP = "FrozenThrone\\(2)EchoIsles.w3x";

const mapCache = new Map<string, MapInfo>();

export function useInstanceMap(): MapInfo | null {
  const inst = useApp((s) => s.inst);
  const path = useApp((s) => {
    const i = s.instances.find((x) => x.inst === s.inst);
    return (i as any)?.map || i?.nextGame?.map || DEFAULT_MAP;
  });
  const [info, setInfo] = useState<MapInfo | null>(mapCache.get(path) ?? null);
  useEffect(() => {
    let alive = true;
    if (mapCache.has(path)) { setInfo(mapCache.get(path)!); return; }
    api.get<MapInfo>(`/api/maps/info?path=${encodeURIComponent(path)}`).then((m) => { mapCache.set(path, m); if (alive) setInfo(m); }).catch(() => {});
    return () => { alive = false; };
  }, [path, inst]);
  return info;
}

export function useCam(ms = 1500): CamStatus | null {
  return useCamState(ms).cam;
}

/** 镜头状态 + 最近一次取失败的原因。★ 09-18：原来要「worker 说连上了」才去取，而那个状态只在换局时发过一次，
 *  网页刷新后拿到的是旧值，导播台就一直停在「读不到镜头」。现在无条件轮询，取不到把原因显示出来。 */
export function useCamState(ms = 1500): { cam: CamStatus | null; error: string | null } {
  const inst = useApp((s) => s.inst);
  const [cam, setCam] = useState<CamStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let busy = false;
    const run = async () => {
      if (busy || document.hidden) return;         // 页面藏起来就不拉
      busy = true;
      try {
        const c = await api.get<CamStatus>(`/api/i/${inst}/cam`);
        if (alive) { setCam(c); setError(null); }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally { busy = false; }
    };
    run();
    const id = setInterval(run, ms);
    return () => { alive = false; clearInterval(id); };
  }, [inst, ms]);
  return { cam, error };
}

export function useUnits(): { units: Unit[]; me: number } {
  const snap = useApp((s) => s.snapshot);
  return useMemo(() => ({ units: (snap?.units ?? []).map(toUnit), me: snap?.me ?? 0 }), [snap]);
}
