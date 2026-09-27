// Node 22+ 示例：连网关、看局面、让所有农民说一句话、屏幕上放个按钮等人点。
//   node gateway/clients/js/example.mjs 9          （先起网关：python gateway/server.py）
import { OpenWar3, unit } from "./openwar3.mjs";

const inst = Number(process.argv[2] ?? 9);
const ow = new OpenWar3("ws://127.0.0.1:8870/ws", { inst, role: "dev" });
const hello = await ow.connect();
console.log(`连上 pid=${hello.pid}，${hello.methods.length} 个方法`);

const ov = await ow.overview();
console.log(`游戏 ${ov.clock}s · 金 ${ov.gold} 木 ${ov.lumber} 人口 ${ov.food} · 我方`, ov.my_units);

const peasants = await ow.api.units("me", ["hpea", "opeo", "uaco", "ewsp"]);
for (const p of peasants.slice(0, 3)) await ow.api.say(unit(p), "来自 JavaScript 的问候！", 3);

await ow.api.ui.button("js.btn", "JS 放的按钮（点我结束）", { screen: [40, 360], size: 18 });
await ow.subscribe({ events: true });
console.log("在游戏里点一下那个按钮……");
await new Promise((resolve) => ow.on("event:ui.click", (e) => { if (e.key === "js.btn") resolve(); }));
await ow.api.ui.toast("收到点击，再见！", { seconds: 3 });
await ow.api.ui.remove("js.btn");
ow.close();
console.log("完成");
