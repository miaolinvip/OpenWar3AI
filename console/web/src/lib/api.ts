import { store, toast } from "./store";
import { tx } from "./i18n";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) throw new ApiError(r.status, (data && (data.detail || data.error)) || `${r.status} ${r.statusText}`);
  return data as T;
}

export const api = {
  get: <T>(url: string) => req<T>("GET", url),
  post: <T>(url: string, body: unknown = {}) => req<T>("POST", url, body),
  put: <T>(url: string, body: unknown) => req<T>("PUT", url, body),
  del: <T>(url: string) => req<T>("DELETE", url),
};

export const inst = () => store.get().inst;
export const I = (path: string) => `/api/i/${inst()}${path}`;

/** 手动下令：成功条数 toast 出来。 */
export async function command(body: Record<string, unknown>, label?: string) {
  try {
    const r = await api.post<{ okCount: number; results: { ok: boolean; error: string | null }[] }>(I("/command"), body);
    const n = r.results.length;
    const err = r.results.find((x) => x.error)?.error;
    if (r.okCount === n) toast(tx("{0}：{1} 个单位接令", label ?? body.kind, n), "success", 1800);
    else toast(tx("{0}：{1}/{2} 接令{3}", label ?? body.kind, r.okCount, n, err ? `（${err}）` : ""), r.okCount ? "info" : "error");
    return r;
  } catch (e) {
    toast(tx("下令失败：{0}", (e as Error).message), "error");
    return null;
  }
}

export async function cam(op: string, payload: Record<string, unknown> = {}) {
  try {
    const r = await api.post<{ ok: boolean; error?: string }>(I(`/cam/${op}`), payload);
    if (!r.ok) toast(tx("镜头 {0}：{1}", op, r.error ?? tx("没成功")), "error");
    return r;
  } catch (e) {
    toast(tx("镜头 {0}：{1}", op, (e as Error).message), "error");
    return null;
  }
}

export async function action<T = any>(fn: () => Promise<T>, ok?: string): Promise<T | null> {
  try {
    const r = await fn();
    if (ok) toast(ok, "success");
    return r;
  } catch (e) {
    toast((e as Error).message, "error");
    return null;
  }
}
