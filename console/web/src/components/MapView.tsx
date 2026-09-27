import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BrainLive, MapInfo, Meta, Snapshot, Unit } from "../lib/types";
import { toUnit } from "../lib/types";
import { category, ownerColor, unitName, orderLabel, NEUTRAL_HOSTILE } from "../lib/game";
import { tx } from "../lib/i18n";

/** 导播台用：最近几次切镜的落点。n 是编号（越大越新），kind 决定颜色。战场页不传，行为不变。 */
export interface CamMark { x: number; y: number; n: number; kind: string }
const MARK_COLOR: Record<string, string> = {
  combat: "#ef4444", event: "#f59e0b", anticipate: "#a855f7", idle: "#94a3b8", hero: "#facc15",
};

export interface Layers { terrain: boolean; units: boolean; creeps: boolean; armies: boolean; threat: boolean; target: boolean; intents: boolean; camera: boolean; grid: boolean; hp: boolean }
export const DEFAULT_LAYERS: Layers = { terrain: true, units: true, creeps: true, armies: true, threat: true, target: true, intents: true, camera: true, grid: false, hp: true };

interface Props {
  snapshot: Snapshot | null;
  meta: Meta | null;
  mapInfo: MapInfo | null;
  brain: BrainLive | null;
  cam: { x: number; y: number; distance: number } | null;
  selection: number[];
  layers: Layers;
  armedLabel: string | null;
  onSelect: (addrs: number[], additive: boolean) => void;
  onPrimary: (x: number, y: number, target: Unit | null, shift: boolean) => boolean; // armed 模式下左键；返回 true = 已处理
  onSecondary: (x: number, y: number, target: Unit | null) => void;
  onDouble: (x: number, y: number, target: Unit | null) => void;
  fitKey?: string;
  compact?: boolean;
  marks?: CamMark[];
}

function footprint(u: Unit, meta: Meta | null): number {
  const c = category(u.type, meta);
  if (c === "hall") return 420;
  if (c === "mine") return 380;
  if (c === "tower") return 190;
  if (c === "building") return ["hhou", "uzig", "otrb", "emow"].includes(u.type) ? 200 : 300;
  return 0;
}

export default function MapView(p: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const view = useRef({ cx: 0, cy: 0, scale: 0.05, fitted: "", touched: false });
  const drag = useRef<null | { kind: "pan" | "box"; sx: number; sy: number; x: number; y: number; cx: number; cy: number; moved: boolean; shift: boolean }>(null);
  const [box, setBox] = useState<null | { x0: number; y0: number; x1: number; y1: number }>(null);
  const [hover, setHover] = useState<{ u: Unit; x: number; y: number } | null>(null);
  const [, force] = useState(0);
  const space = useRef(false);
  const img = useRef<HTMLImageElement | null>(null);

  const units = useMemo(() => (p.snapshot?.units ?? []).map(toUnit).filter((u) => u.hp > 0 || category(u.type, p.meta) === "mine"), [p.snapshot, p.meta]);
  const me = p.snapshot?.me ?? 0;

  const bounds = useMemo(() => {
    const t = p.mapInfo?.terrain;
    if (t) return { minX: t.minX, maxX: t.maxX, minY: t.minY, maxY: t.maxY };
    if (units.length) {
      const xs = units.map((u) => u.x), ys = units.map((u) => u.y);
      return { minX: Math.min(...xs) - 1500, maxX: Math.max(...xs) + 1500, minY: Math.min(...ys) - 1500, maxY: Math.max(...ys) + 1500 };
    }
    return { minX: -8000, maxX: 8000, minY: -6000, maxY: 6000 };
  }, [p.mapInfo, units.length > 0]);

  // 预览图
  useEffect(() => {
    img.current = null;
    if (!p.mapInfo?.preview) return;
    const im = new Image();
    im.onload = () => { img.current = im; force((n) => n + 1); };
    im.src = `/api/maps/preview/${encodeURIComponent(p.mapInfo.preview)}`;
  }, [p.mapInfo?.preview]);

  useEffect(() => {
    const ro = new ResizeObserver((es) => {
      const r = es[0].contentRect;
      setSize({ w: Math.max(100, r.width), h: Math.max(100, r.height) });
    });
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    const W = bounds.maxX - bounds.minX, H = bounds.maxY - bounds.minY;
    view.current.scale = Math.min(size.w / W, size.h / H) * 0.96;
    view.current.cx = (bounds.minX + bounds.maxX) / 2;
    view.current.cy = (bounds.minY + bounds.maxY) / 2;
    force((n) => n + 1);
  }, [bounds, size]);

  useEffect(() => {
    // 用户没拖过/缩放过就一直跟着容器尺寸铺满；换实例/换地图重新铺
    const key = `${p.fitKey ?? ""}|${bounds.minX}|${bounds.maxX}`;
    if (view.current.fitted !== key) { view.current.fitted = key; view.current.touched = false; fit(); }
    else if (!view.current.touched) fit();
  }, [bounds, size.w, size.h, fit, p.fitKey]);

  const w2s = (x: number, y: number) => {
    const v = view.current;
    return [size.w / 2 + (x - v.cx) * v.scale, size.h / 2 - (y - v.cy) * v.scale] as const;
  };
  const s2w = (sx: number, sy: number) => {
    const v = view.current;
    return [v.cx + (sx - size.w / 2) / v.scale, v.cy - (sy - size.h / 2) / v.scale] as const;
  };

  const hit = (sx: number, sy: number): Unit | null => {
    const [wx, wy] = s2w(sx, sy);
    const v = view.current;
    let best: Unit | null = null, bd = Infinity;
    for (const u of units) {
      const fp = footprint(u, p.meta);
      const r = fp ? fp / 2 : Math.max(40, 9 / v.scale);
      const d = Math.hypot(u.x - wx, u.y - wy);
      const pri = fp ? d + 60 : d; // 单位优先于脚下的建筑
      if (d <= r && pri < bd) { best = u; bd = pri; }
    }
    return best;
  };

  // 绘制
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size.w * dpr)) { c.width = Math.round(size.w * dpr); c.height = Math.round(size.h * dpr); }
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue("--accent").trim() || "#3b82f6";
    const textCol = css.getPropertyValue("--text").trim() || "#fff";
    const mapBg = css.getPropertyValue("--map-bg").trim() || "#000";
    g.fillStyle = mapBg;
    g.fillRect(0, 0, size.w, size.h);
    const v = view.current;
    const L = p.layers;

    // 地形图：小地图按整张地形的长边铺成正方形（上下或左右留黑边）
    const W = bounds.maxX - bounds.minX, H = bounds.maxY - bounds.minY;
    if (L.terrain && img.current && p.mapInfo?.terrain) {
      const side = Math.max(W, H);
      const cx = (bounds.minX + bounds.maxX) / 2, cy = (bounds.minY + bounds.maxY) / 2;
      const [x0, y0] = w2s(cx - side / 2, cy + side / 2);
      g.imageSmoothingEnabled = true;
      g.globalAlpha = 0.92;
      g.drawImage(img.current, x0, y0, side * v.scale, side * v.scale);
      g.globalAlpha = 1;
    } else {
      const [x0, y0] = w2s(bounds.minX, bounds.maxY);
      g.strokeStyle = "rgba(128,128,128,0.35)";
      g.strokeRect(x0, y0, W * v.scale, H * v.scale);
    }
    if (L.grid) {
      g.strokeStyle = "rgba(255,255,255,0.06)";
      g.lineWidth = 1;
      for (let x = Math.ceil(bounds.minX / 1024) * 1024; x <= bounds.maxX; x += 1024) { const [sx] = w2s(x, 0); g.beginPath(); g.moveTo(sx, 0); g.lineTo(sx, size.h); g.stroke(); }
      for (let y = Math.ceil(bounds.minY / 1024) * 1024; y <= bounds.maxY; y += 1024) { const [, sy] = w2s(0, y); g.beginPath(); g.moveTo(0, sy); g.lineTo(size.w, sy); g.stroke(); }
    }

    const b = p.brain;
    // 城镇威胁
    if (L.threat && b) {
      for (const tw of b.world.towns) {
        if (tw.owner !== me || tw.threat <= 0) continue;
        const [sx, sy] = w2s(tw.x, tw.y);
        const k = Math.min(1, tw.threat / Math.max(1, b.world.acceptedThreat * 3));
        g.beginPath();
        g.arc(sx, sy, 1600 * v.scale, 0, Math.PI * 2);
        g.fillStyle = `rgba(239,68,68,${0.05 + 0.18 * k})`;
        g.fill();
        g.strokeStyle = `rgba(239,68,68,${0.35 + 0.5 * k})`;
        g.setLineDash([6, 5]);
        g.stroke();
        g.setLineDash([]);
      }
    }
    // 打野营地
    if (L.target && b?.attack.camp && b.attack.state === "creep") {
      const cp = b.attack.camp;
      const [sx, sy] = w2s(cp.x, cp.y);
      g.beginPath();
      g.arc(sx, sy, Math.max(10, 700 * v.scale), 0, Math.PI * 2);
      g.strokeStyle = "rgba(250,204,21,0.9)";
      g.setLineDash([4, 4]);
      g.lineWidth = 1.5;
      g.stroke();
      g.setLineDash([]);
      label(g, sx, sy - Math.max(10, 700 * v.scale) - 6, tx("打野 强度{0}", cp.strength), "#facc15");
    }

    // 单位
    const sel = new Set(p.selection);
    const drawUnits = [...units].sort((a, b2) => footprint(b2, p.meta) - footprint(a, p.meta));
    for (const u of drawUnits) {
      const cat = category(u.type, p.meta);
      if (!L.units && cat !== "mine") continue;
      if (!L.creeps && u.owner === NEUTRAL_HOSTILE) continue;
      const [sx, sy] = w2s(u.x, u.y);
      if (sx < -60 || sy < -60 || sx > size.w + 60 || sy > size.h + 60) continue;
      const col = cat === "mine" ? "#f5c542" : ownerColor(u.owner, me);
      const fp = footprint(u, p.meta);
      g.lineWidth = 1;
      if (fp) {
        const s = Math.max(cat === "mine" ? 9 : 6, fp * v.scale);
        g.fillStyle = col;
        g.globalAlpha = cat === "mine" ? 0.95 : 0.85;
        if (cat === "mine") {
          g.beginPath();
          g.moveTo(sx, sy - s / 2); g.lineTo(sx + s / 2, sy); g.lineTo(sx, sy + s / 2); g.lineTo(sx - s / 2, sy); g.closePath();
          g.fill();
          g.strokeStyle = "rgba(0,0,0,0.6)"; g.stroke();
        } else {
          g.fillRect(sx - s / 2, sy - s / 2, s, s);
          g.strokeStyle = "rgba(0,0,0,0.65)";
          g.strokeRect(sx - s / 2 + 0.5, sy - s / 2 + 0.5, s - 1, s - 1);
          if (cat === "hall") { g.strokeStyle = "rgba(255,255,255,0.7)"; g.strokeRect(sx - s / 2 - 2.5, sy - s / 2 - 2.5, s + 5, s + 5); }
          if (u.hp < u.hpMax * 0.97 && u.hpMax > 0) { // 工地/受伤
            g.fillStyle = "rgba(0,0,0,0.45)";
            g.fillRect(sx - s / 2, sy - s / 2, s, s * (1 - u.hp / u.hpMax));
          }
        }
        g.globalAlpha = 1;
        if (sel.has(u.addr)) { g.strokeStyle = accent; g.lineWidth = 2; g.strokeRect(sx - s / 2 - 4, sy - s / 2 - 4, s + 8, s + 8); }
      } else {
        const hero = cat === "hero";
        const r = Math.max(hero ? 5.5 : u.owner === NEUTRAL_HOSTILE ? 2.8 : 3.4, (hero ? 70 : 40) * v.scale);
        g.beginPath();
        g.arc(sx, sy, r, 0, Math.PI * 2);
        g.fillStyle = col;
        g.fill();
        g.strokeStyle = hero ? "#fff" : "rgba(0,0,0,0.7)";
        g.lineWidth = hero ? 2 : 1;
        g.stroke();
        if (cat === "worker") { g.fillStyle = "rgba(255,255,255,0.8)"; g.beginPath(); g.arc(sx, sy, Math.max(1, r * 0.35), 0, Math.PI * 2); g.fill(); }
        if (sel.has(u.addr)) { g.beginPath(); g.arc(sx, sy, r + 4, 0, Math.PI * 2); g.strokeStyle = accent; g.lineWidth = 2; g.stroke(); }
      }
      if (L.hp && u.hpMax > 0 && cat !== "mine" && (sel.has(u.addr) || v.scale > 0.09) && u.hp < u.hpMax) {
        const bw = Math.max(14, (fp || 90) * v.scale);
        const f = u.hp / u.hpMax;
        const by = sy - Math.max(8, (fp || 60) * v.scale / 2) - 5;
        g.fillStyle = "rgba(0,0,0,0.6)"; g.fillRect(sx - bw / 2, by, bw, 3);
        g.fillStyle = f > 0.6 ? "#22c55e" : f > 0.3 ? "#eab308" : "#ef4444"; g.fillRect(sx - bw / 2, by, bw * f, 3);
      }
    }

    // 军团
    if (L.armies && b) {
      for (const a of b.world.enemyArmies) {
        const [sx, sy] = w2s(a.x, a.y);
        const r = Math.max(14, Math.sqrt(a.strength) * 220 * v.scale);
        g.beginPath(); g.arc(sx, sy, r, 0, Math.PI * 2);
        g.fillStyle = "rgba(239,68,68,0.10)"; g.fill();
        g.strokeStyle = "rgba(239,68,68,0.75)"; g.lineWidth = 1.5; g.stroke();
        if (Math.hypot(a.dx, a.dy) > 30) arrow(g, sx, sy, sx + a.dx * 3 * v.scale, sy - a.dy * 3 * v.scale, "rgba(239,68,68,0.9)");
        label(g, sx, sy - r - 5, tx("敌 {0} · {1}", a.n, a.strength), "#f87171");
      }
      for (const a of b.world.myArmies) {
        const [sx, sy] = w2s(a.x, a.y);
        const r = Math.max(14, Math.sqrt(a.strength) * 220 * v.scale);
        g.beginPath(); g.arc(sx, sy, r, 0, Math.PI * 2);
        g.strokeStyle = "rgba(96,165,250,0.7)"; g.lineWidth = 1.2; g.setLineDash([3, 3]); g.stroke(); g.setLineDash([]);
        label(g, sx, sy + r + 12, tx("我 {0} · {1}", a.n, a.strength), "#93c5fd");
      }
    }
    // 出击目标
    if (L.target && b?.attack.target && ["attack", "gather"].includes(b.attack.state)) {
      const tg = b.attack.target;
      const [sx, sy] = w2s(tg.x, tg.y);
      const main = [...b.world.myArmies].sort((x, y) => y.strength - x.strength)[0];
      if (main) {
        const [ax, ay] = w2s(main.x, main.y);
        arrow(g, ax, ay, sx, sy, b.attack.state === "attack" ? "rgba(250,204,21,0.95)" : "rgba(250,204,21,0.5)", true);
      }
      g.strokeStyle = "#facc15"; g.lineWidth = 2;
      g.beginPath(); g.arc(sx, sy, 14, 0, Math.PI * 2); g.moveTo(sx - 20, sy); g.lineTo(sx + 20, sy); g.moveTo(sx, sy - 20); g.lineTo(sx, sy + 20); g.stroke();
      label(g, sx, sy - 24, `${b.attack.state === "gather" ? tx("集结 → ") : tx("出击 → ")}${b.attack.targetKind}`, "#facc15");
    }
    // 意图
    if (L.intents && b) {
      const seen = new Set<string>();
      for (const it of b.intents) {
        const key = `${Math.round(it.x / 200)}|${Math.round(it.y / 200)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const [sx, sy] = w2s(it.x, it.y);
        g.fillStyle = it.kind === "move" ? "rgba(96,165,250,0.9)" : "rgba(250,204,21,0.9)";
        g.beginPath(); g.moveTo(sx, sy - 6); g.lineTo(sx + 5, sy + 4); g.lineTo(sx - 5, sy + 4); g.closePath(); g.fill();
        if (it.why) label(g, sx, sy + 16, it.why, textCol, 0.85);
      }
    }
    // 镜头
    if (L.camera && p.cam) {
      const hw = p.cam.distance * 0.78, hh = p.cam.distance * 0.46;
      const [x0, y0] = w2s(p.cam.x - hw, p.cam.y + hh * 1.25);
      g.strokeStyle = "rgba(255,255,255,0.85)"; g.lineWidth = 1.2;
      g.strokeRect(x0, y0, hw * 2 * v.scale, hh * 2 * v.scale);
    }
    // 导播：最近的切镜落点 —— 虚线连成轨迹，编号越大越新，越旧越淡
    if (p.marks && p.marks.length) {
      const pts = p.marks.map((m) => { const [sx, sy] = w2s(m.x, m.y); return { m, sx, sy }; });
      g.setLineDash([4, 4]); g.lineWidth = 1; g.strokeStyle = "rgba(255,255,255,0.35)";
      g.beginPath();
      pts.forEach((q, i) => (i ? g.lineTo(q.sx, q.sy) : g.moveTo(q.sx, q.sy)));
      g.stroke(); g.setLineDash([]);
      pts.forEach((q, i) => {
        const last = i === pts.length - 1;
        const a = 0.3 + 0.7 * ((i + 1) / pts.length);
        g.globalAlpha = a;
        g.fillStyle = MARK_COLOR[q.m.kind] ?? accent;
        g.beginPath(); g.arc(q.sx, q.sy, last ? 7 : 4.5, 0, Math.PI * 2); g.fill();
        if (last) { g.strokeStyle = "#fff"; g.lineWidth = 1.5; g.stroke(); }
        g.globalAlpha = 1;
        label(g, q.sx, q.sy - 9, String(q.m.n), textCol, a);
      });
    }
    // 框选
    if (box) {
      g.fillStyle = "rgba(59,130,246,0.12)";
      g.strokeStyle = accent;
      g.lineWidth = 1;
      g.fillRect(Math.min(box.x0, box.x1), Math.min(box.y0, box.y1), Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
      g.strokeRect(Math.min(box.x0, box.x1) + 0.5, Math.min(box.y0, box.y1) + 0.5, Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0));
    }
  });

  const pos = (e: React.MouseEvent | MouseEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };

  useEffect(() => {
    const kd = (e: KeyboardEvent) => { if (e.code === "Space" && (e.target as HTMLElement).tagName !== "INPUT") space.current = true; };
    const ku = (e: KeyboardEvent) => { if (e.code === "Space") space.current = false; };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    return () => { window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); };
  }, []);

  return (
    <div ref={wrap} style={{ position: "absolute", inset: 0 }}>
      <canvas
        ref={canvas}
        style={{ cursor: p.armedLabel ? "crosshair" : drag.current?.kind === "pan" ? "grabbing" : hover ? "pointer" : "default" }}
        onContextMenu={(e) => e.preventDefault()}
        onWheel={(e) => {
          const [sx, sy] = pos(e);
          const [wx, wy] = s2w(sx, sy);
          const v = view.current;
          v.scale = Math.min(1.2, Math.max(0.008, v.scale * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
          v.touched = true;
          v.cx = wx - (sx - size.w / 2) / v.scale;
          v.cy = wy + (sy - size.h / 2) / v.scale;
          force((n) => n + 1);
        }}
        onMouseDown={(e) => {
          const [sx, sy] = pos(e);
          if (e.button === 1 || (e.button === 0 && space.current)) {
            e.preventDefault();
            drag.current = { kind: "pan", sx, sy, x: sx, y: sy, cx: view.current.cx, cy: view.current.cy, moved: false, shift: e.shiftKey };
            return;
          }
          if (e.button === 2) {
            const [wx, wy] = s2w(sx, sy);
            p.onSecondary(wx, wy, hit(sx, sy));
            return;
          }
          if (e.button === 0) {
            if (p.armedLabel) {
              const [wx, wy] = s2w(sx, sy);
              if (p.onPrimary(wx, wy, hit(sx, sy), e.shiftKey)) return;
            }
            drag.current = { kind: "box", sx, sy, x: sx, y: sy, cx: 0, cy: 0, moved: false, shift: e.shiftKey };
          }
        }}
        onMouseMove={(e) => {
          const [sx, sy] = pos(e);
          const d = drag.current;
          if (d) {
            if (Math.hypot(sx - d.sx, sy - d.sy) > 4) d.moved = true;
            if (d.kind === "pan") {
              view.current.touched = true;
              view.current.cx = d.cx - (sx - d.sx) / view.current.scale;
              view.current.cy = d.cy + (sy - d.sy) / view.current.scale;
              force((n) => n + 1);
            } else if (d.moved) setBox({ x0: d.sx, y0: d.sy, x1: sx, y1: sy });
            return;
          }
          const u = hit(sx, sy);
          setHover(u ? { u, x: sx, y: sy } : null);
        }}
        onMouseUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (!d || d.kind === "pan") return;
          const [sx, sy] = pos(e);
          if (d.moved && box) {
            const [ax, ay] = s2w(Math.min(box.x0, box.x1), Math.max(box.y0, box.y1));
            const [bx, by] = s2w(Math.max(box.x0, box.x1), Math.min(box.y0, box.y1));
            const inBox = units.filter((u) => u.x >= ax && u.x <= bx && u.y >= ay && u.y <= by);
            // 框选优先我方可移动单位；没有再退到我方建筑，最后才是任何单位
            const mine = inBox.filter((u) => u.owner === me && !footprint(u, p.meta));
            const pick = mine.length ? mine : inBox.filter((u) => u.owner === me).length ? inBox.filter((u) => u.owner === me) : inBox;
            p.onSelect(pick.map((u) => u.addr), d.shift);
            setBox(null);
            return;
          }
          setBox(null);
          const u = hit(sx, sy);
          p.onSelect(u ? [u.addr] : [], d.shift);
        }}
        onDoubleClick={(e) => {
          const [sx, sy] = pos(e);
          const [wx, wy] = s2w(sx, sy);
          p.onDouble(wx, wy, hit(sx, sy));
        }}
        onMouseLeave={() => setHover(null)}
      />
      {hover && !drag.current && (
        <div className="glass" style={{ position: "absolute", left: Math.min(hover.x + 14, size.w - 220), top: Math.min(hover.y + 12, size.h - 90), padding: "6px 10px", fontSize: 12, pointerEvents: "none", minWidth: 160, zIndex: 5 }}>
          <div className="row" style={{ gap: 6 }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: ownerColor(hover.u.owner, me), display: "inline-block" }} />
            <b>{unitName(hover.u.type, p.meta)}</b>
            <span className="faint mono">{hover.u.type}</span>
          </div>
          <div className="muted">{tx("血 {0}/{1} ·", hover.u.hp, hover.u.hpMax)} {orderLabel(hover.u.order, p.meta)}</div>
          <div className="faint mono">({hover.u.x}, {hover.u.y})</div>
        </div>
      )}
      <MapControls onFit={() => { view.current.touched = false; fit(); }} onZoom={(k) => { view.current.touched = true; view.current.scale = Math.min(1.2, Math.max(0.008, view.current.scale * k)); force((n) => n + 1); }} compact={p.compact} />
    </div>
  );
}

function MapControls({ onFit, onZoom, compact }: { onFit: () => void; onZoom: (k: number) => void; compact?: boolean }) {
  if (compact) return null;
  return (
    <div className="glass" style={{ position: "absolute", right: 12, bottom: 12, display: "flex", flexDirection: "column", padding: 3, gap: 2, zIndex: 3 }}>
      <button className="btn ghost icon sm" title={tx("放大")} onClick={() => onZoom(1.3)}>＋</button>
      <button className="btn ghost icon sm" title={tx("缩小")} onClick={() => onZoom(1 / 1.3)}>－</button>
      <button className="btn ghost icon sm" title={tx("全图")} onClick={onFit}>⤢</button>
    </div>
  );
}

function label(g: CanvasRenderingContext2D, x: number, y: number, text: string, color: string, alpha = 1) {
  g.font = "600 11px Inter, 'Microsoft YaHei UI', sans-serif";
  g.textAlign = "center";
  g.globalAlpha = alpha;
  g.lineWidth = 3;
  g.strokeStyle = "rgba(0,0,0,0.75)";
  g.strokeText(text, x, y);
  g.fillStyle = color;
  g.fillText(text, x, y);
  g.globalAlpha = 1;
}

function arrow(g: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number, color: string, dashed = false) {
  const ang = Math.atan2(y1 - y0, x1 - x0);
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineWidth = 2;
  if (dashed) g.setLineDash([8, 6]);
  g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
  g.setLineDash([]);
  g.beginPath();
  g.moveTo(x1, y1);
  g.lineTo(x1 - 10 * Math.cos(ang - 0.4), y1 - 10 * Math.sin(ang - 0.4));
  g.lineTo(x1 - 10 * Math.cos(ang + 0.4), y1 - 10 * Math.sin(ang + 0.4));
  g.closePath();
  g.fill();
}
