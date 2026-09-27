// OpenWar3 网关的 JS 客户端（09-25）：浏览器和 Node 22+（自带 WebSocket）都能用，零依赖。
// Node 20 / 21：node --experimental-websocket；或者自己带实现：new OpenWar3(url, { WebSocket: (await import("ws")).default })
//
//   import { OpenWar3, unit } from "./openwar3.mjs";
//   const ow = new OpenWar3("ws://127.0.0.1:8870/ws", { inst: 9, role: "dev" });
//   await ow.connect();
//   const mine = await ow.api.units("me");                    // 方法名 = docs/API_ZH.md 里 Game 的方法
//   await ow.api.move(mine.slice(0, 3).map(unit), 100, 200);   // 单位写 unit(u) = {unit: 地址}
//   await ow.api.ui.button("hi", "点我", { screen: [40, 300] });  // 关键字参数：最后一个参数给普通对象
//   ow.on("event:ui.click", (e) => console.log("点了", e.key));
//   await ow.subscribe({ state: { hz: 2, units: "mine" }, events: true });
//
// 协议见 docs/GATEWAY_ZH.md。
export const unit = (u) => ({ unit: typeof u === "number" ? u : u.addr });

export class OpenWar3 {
  constructor(url = "ws://127.0.0.1:8870/ws", { inst, pid, role = "dev", player, fair, token, WebSocket: WS } = {}) {
    this.WS = WS ?? globalThis.WebSocket;
    const q = new URLSearchParams();
    if (inst != null) q.set("inst", String(inst));
    if (pid != null) q.set("pid", String(pid));
    q.set("role", role);
    if (player != null) q.set("player", String(player));
    if (fair === false) q.set("fair", "0");
    if (token) q.set("token", token);
    this.url = `${url}?${q}`;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.hello = null;
    this.api = this.#proxy("");
  }

  connect() {
    return new Promise((resolve, reject) => {
      if (!this.WS) {
        reject(new Error("这个运行环境没有 WebSocket：Node 22+ 自带；Node 20/21 加 --experimental-websocket，或传 { WebSocket: 实现 }"));
        return;
      }
      const ws = new this.WS(this.url);
      this.ws = ws;
      ws.onmessage = (m) => this.#onMessage(JSON.parse(typeof m.data === "string" ? m.data : String(m.data)), resolve, reject);
      ws.onerror = (e) => reject(new Error(`连不上网关：${this.url}`));
      ws.onclose = () => {
        for (const [, p] of this.pending) p.reject(new Error("连接断了"));
        this.pending.clear();
        this.#emit("close", null);
      };
    });
  }

  #onMessage(m, resolve, reject) {
    if (m.type === "hello") { this.hello = m; resolve?.(m); return; }
    if (m.type === "error") {                     // 连上之前 = 连接失败；连上之后 = 推送出错之类（ow.on("error", ...) 收）
      if (!this.hello) reject?.(new Error(m.error)); else this.#emit("error", m.error);
      return;
    }
    if (m.type === "state") { this.#emit("state", m.state); return; }
    if (m.type === "events") {
      this.#emit("events", m.events);
      for (const e of m.events) { this.#emit("event", e); this.#emit(`event:${e.kind}`, e); }
      return;
    }
    const p = this.pending.get(m.id);
    if (!p) return;
    this.pending.delete(m.id);
    m.ok ? p.resolve(m.result) : p.reject(new Error(m.error));
  }

  #send(msg) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, ...msg }));
    });
  }

  // call("units", ["me"]) / call("ui.button", ["k", "文字"], { screen: [40, 300] })
  call(method, args = [], kwargs = {}) { return this.#send({ op: "call", method, args, kwargs }); }
  subscribe({ state = null, events = true } = {}) { return this.#send({ op: "subscribe", state, events }); }
  unsubscribe() { return this.#send({ op: "unsubscribe" }); }
  jass(code) { return this.#send({ op: "jass", code }); }
  overview() { return this.#send({ op: "overview" }); }
  methods() { return this.#send({ op: "api" }); }
  close() { this.ws?.close(); }

  on(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(fn);
    return () => this.handlers.get(type).delete(fn);
  }

  #emit(type, v) { for (const fn of this.handlers.get(type) ?? []) fn(v); }

  // ow.api.units("me") -> call("units", ["me"])；ow.api.ui.button(..., {screen: [..]}) -> 最后一个普通对象当关键字参数
  #proxy(prefix) {
    const self = this;
    return new Proxy(function () {}, {
      // then 不能给代理：否则 await ow.api.x 会把它当 Promise，调 x.then 永远等不到
      get(_t, name) { return typeof name === "string" && name !== "then" ? self.#proxy(prefix ? `${prefix}.${name}` : name) : undefined; },
      apply(_t, _this, args) {
        const last = args[args.length - 1];
        const isKw = last && typeof last === "object" && !Array.isArray(last) && !("unit" in last) && !("addr" in last);
        return self.call(prefix, isKw ? args.slice(0, -1) : args, isKw ? last : {});
      },
    });
  }
}
