import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { tx } from "../lib/i18n";

export function Card({ title, icon, actions, children, className = "", bodyClass = "card-body", style }: {
  title?: ReactNode; icon?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string; bodyClass?: string; style?: CSSProperties;
}) {
  return (
    <section className={`card ${className}`} style={style}>
      {(title || actions) && (
        <header className="card-head">
          <div className="card-title">{icon}{title}</div>
          <div className="right row">{actions}</div>
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, icon, tone }: { label: ReactNode; value: ReactNode; sub?: ReactNode; icon?: ReactNode; tone?: string }) {
  return (
    <div className="card stat">
      <div className="stat-label">{icon}{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Switch({ on, onChange, disabled, title }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; title?: string }) {
  return <button type="button" role="switch" aria-checked={on} title={title} disabled={disabled} className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)} />;
}

export function Badge({ tone = "", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Seg<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode; icon?: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={String(o.value)} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.icon}{o.label}
        </button>
      ))}
    </div>
  );
}

export function Bar({ value, max, kind = "", color }: { value: number; max: number; kind?: string; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const c = color ?? (kind ? undefined : pct > 60 ? "var(--success)" : pct > 30 ? "var(--warning)" : "var(--danger)");
  return <div className={`bar ${kind}`}><i style={{ width: `${pct}%`, background: c }} /></div>;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <div style={{ fontWeight: 600, color: "var(--text-2)" }}>{title}</div>
      {children && <div style={{ fontSize: 12.5 }}>{children}</div>}
    </div>
  );
}

/** 还没有数据时的占位：读取中，或者读取失败（带原因和「重试」）。以前失败了也一直显示「读取中」。 */
export function FetchState({ error, onRetry, title }: { error: string | null; onRetry?: () => void; title?: ReactNode }) {
  if (!error) return <Empty icon={<span className="spinner" />} title={title ?? tx("读取中…")} />;
  return (
    <Empty title={tx("读取失败")}>
      <div className="col gap8" style={{ alignItems: "center" }}>
        <span className="mono" style={{ wordBreak: "break-word" }}>{error}</span>
        {onRetry && <button className="btn sm" onClick={onRetry}>{tx("重试")}</button>}
      </div>
    </Empty>
  );
}

/** 点了就在忙的按钮：执行期间禁用、显示转圈，防止连点（开始测试、保存、导入这类） */
export function BusyButton({ onClick, children, className = "btn", disabled, title }: {
  onClick: () => Promise<unknown> | unknown; children: ReactNode; className?: string; disabled?: boolean; title?: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button className={className} title={title} disabled={disabled || busy} aria-busy={busy}
      onClick={async () => { setBusy(true); try { await onClick(); } finally { setBusy(false); } }}>
      {busy && <span className="spinner sm" />}{children}
    </button>
  );
}

export function Dialog({ open, title, onClose, children, footer, wide }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;
  // 挂到 body 上：祖先带 backdrop-filter / transform（例如顶栏 .topbar）时，position: fixed 会以那个祖先为定位容器、
  // z-index 也只在它的层叠上下文里生效 —— 顶栏里的「结束本局」弹窗曾经只罩住顶栏一条、被页面内容盖住点不到。
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog ${wide ? "wide" : ""}`} role="dialog">
        <div className="dialog-head">
          <div className="dialog-title">{title}</div>
          <button className="btn ghost icon sm right" onClick={onClose} aria-label={tx("关闭")}><X size={16} /></button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Confirm({ open, title, body, confirm = tx("确定"), danger, onConfirm, onClose }: {
  open: boolean; title: string; body: ReactNode; confirm?: string; danger?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Dialog open={open} title={title} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{tx("取消")}</button><button className={`btn ${danger ? "danger" : "primary"}`} onClick={() => { onConfirm(); onClose(); }}>{confirm}</button></>}>
      <div className="muted">{body}</div>
    </Dialog>
  );
}

export function useInterval(fn: () => void, ms: number, deps: unknown[] = []) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ref.current();
    const id = setInterval(() => { if (!pageHidden()) ref.current(); }, ms);   // 页面藏起来就不跑
    const onVisible = () => { if (!pageHidden()) ref.current(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", onVisible); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

/** 翻页：后端分页的表格底下用（「第 3 / 65 页 · 共 3233 局」+ 首页 / 上一页 / 下一页 / 末页）。 */
export function Pager({ offset, size, total, onChange, unit }: { offset: number; size: number; total: number; onChange: (offset: number) => void; unit?: string }) {
  if (total <= size) return null;
  const page = Math.floor(offset / size) + 1, pages = Math.ceil(total / size);
  const go = (p: number) => onChange((Math.min(pages, Math.max(1, p)) - 1) * size);
  return (
    <div className="row" style={{ justifyContent: "center", gap: 6, padding: "10px 0", flexWrap: "wrap" }}>
      <button className="btn sm ghost" disabled={page <= 1} onClick={() => go(1)}>{tx("首页")}</button>
      <button className="btn sm" disabled={page <= 1} onClick={() => go(page - 1)}>{tx("上一页")}</button>
      <span className="faint mono" style={{ fontSize: 12 }}>{tx("第 {0} / {1} 页 · 共 {2}{3}", page, pages, total, unit ?? "")}</span>
      <button className="btn sm" disabled={page >= pages} onClick={() => go(page + 1)}>{tx("下一页")}</button>
      <button className="btn sm ghost" disabled={page >= pages} onClick={() => go(pages)}>{tx("末页")}</button>
    </div>
  );
}

/** 长列表只画前 step 条，底下一个「再显示 step 条」（事件流、决策明细几千条一次画出来，每秒重画一遍会拖慢整页）。
 *  resetKey 变了（换实例、换筛选）就回到第一页。 */
export function useShowMore<T>(list: T[], step = 200, resetKey: unknown = null): { shown: T[]; more: ReactNode } {
  const [n, setN] = useState(step);
  useEffect(() => { setN(step); }, [resetKey, step]);
  const shown = list.length > n ? list.slice(0, n) : list;
  const more = list.length > n ? (
    <div className="row" style={{ justifyContent: "center", gap: 8, padding: "10px 0" }}>
      <span className="faint" style={{ fontSize: 12 }}>{tx("已显示 {0} / {1} 条", n, list.length)}</span>
      <button className="btn sm" onClick={() => setN((x) => x + step)}>{tx("再显示 {0} 条", Math.min(step, list.length - n))}</button>
    </div>
  ) : null;
  return { shown, more };
}

/** 页面被切走 / 最小化时，定时刷新都先停下（浏览器后台标签里还一直拉数据，是白费 CPU 和网络）。 */
export const pageHidden = () => typeof document !== "undefined" && document.hidden;

/** 取一个接口；ms > 0 时定时刷新。★ 09-26（对局记录「所有实例」把浏览器拖死之后）：
 *  - 上一次还没回来就不发下一次（以前 setInterval 到点就发，后端一慢请求越堆越多）；
 *  - 页面藏起来就停，切回来马上补一次；
 *  - 换地址 / 离开页面时取消还在路上的请求；
 *  - 返回的内容和上次一模一样就不 setData —— 整页不重画（以前每次刷新都整表重画一遍）；
 *  - loading 只在第一次（还没有数据时）为真，定时刷新不闪。 */
export function useFetch<T>(url: string | null, ms = 0, deps: unknown[] = []): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [n, setN] = useState(0);
  const lastUrl = useRef<string | null>(url);
  useEffect(() => {
    // 换了地址（切实例、换筛选）：先清掉旧数据，免得短暂显示上一个实例的内容
    if (lastUrl.current !== url) {
      lastUrl.current = url;
      setData(null);
      setError(null);
    }
    if (!url) return;
    let alive = true;
    let busy = false;
    let waitingVisible = false;
    let timer = 0;
    let ctrl: AbortController | null = null;
    let lastText: string | null = null;
    const schedule = () => { if (alive && ms) timer = window.setTimeout(run, ms); };
    async function run() {
      if (!alive || busy) return;
      if (ms && lastText !== null && pageHidden()) { waitingVisible = true; return; }
      busy = true;
      ctrl = new AbortController();
      if (lastText === null) setLoading(true);
      try {
        const r = await fetch(url!, { signal: ctrl.signal });
        const text = await r.text();
        if (!alive) return;
        let j: any = null;
        try { j = text ? JSON.parse(text) : null; } catch { throw new Error(r.ok ? tx("返回的不是 JSON") : `${r.status}`); }
        if (!r.ok) throw new Error(j?.detail ?? `${r.status}`);
        if (text !== lastText) { lastText = text; setData(j); }
        setError(null);
      } catch (e) {
        if (alive && (e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        busy = false;
        if (alive) { setLoading(false); schedule(); }
      }
    }
    const onVisible = () => {
      if (!pageHidden() && waitingVisible) { waitingVisible = false; window.clearTimeout(timer); run(); }
    };
    run();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      ctrl?.abort();
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, ms, n, ...deps]);
  return { data, error, loading, reload: () => setN((x) => x + 1) };
}

/** 滑块：拖的时候数值跟着变，松手 / 键盘方向键 / 触屏 / 失焦时保存一次。
 *  （以前只在 onMouseUp 保存：键盘和触屏改了不保存，拖动时旁边的数字也不动） */
export function RangeInput({ value, min, max, step = 1, onCommit, width = 200, format }: {
  value: number; min: number; max: number; step?: number; onCommit: (v: number) => void; width?: number; format?: (v: number) => ReactNode;
}) {
  const [v, setV] = useState(value);
  const committed = useRef(value);
  useEffect(() => { setV(value); committed.current = value; }, [value]);
  const commit = () => {
    if (v === committed.current) return;
    committed.current = v;
    onCommit(v);
  };
  return (
    <>
      <input type="range" min={min} max={max} step={step} value={v} style={{ width }}
        onChange={(e) => setV(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
      <span className="mono" style={{ minWidth: 48, textAlign: "right" }}>{format ? format(v) : v}</span>
    </>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

export function Field({ label, desc, children, inline }: { label: ReactNode; desc?: ReactNode; children: ReactNode; inline?: boolean }) {
  if (inline)
    return (
      <div className="row" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)", alignItems: "center" }}>
        <div className="grow">
          <div style={{ fontWeight: 550 }}>{label}</div>
          {desc && <div className="field-desc">{desc}</div>}
        </div>
        {children}
      </div>
    );
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {desc && <span className="field-desc">{desc}</span>}
    </label>
  );
}

export function UnitIcon({ code, color, size = 28 }: { code: string; color: string; size?: number }) {
  return (
    <span className="unit-ico" style={{ background: color, width: size, height: size, fontSize: size * 0.38 }}>
      {code.slice(1, 3).toUpperCase()}
    </span>
  );
}
