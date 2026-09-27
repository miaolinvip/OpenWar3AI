// 编完网页写 dist/.source-hash：源码指纹（09-26）。start.bat 按它判断随仓库发的 dist/ 是不是这份源码编的 ——
// 对得上就不用装 Node.js、不用重编。⚠ 和 console/server/webstamp.py 是同一个算法，改一处要改两处
// （console/tests/test_webstamp.py 核对两边算得一样）。
//     node scripts/stamp.mjs           写 dist/.source-hash（npm run build 最后一步）
//     node scripts/stamp.mjs --print   只打印指纹
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const TOP_FILES = ["index.html", "live.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts"];
const SRC_DIRS = ["src", "public"];

function walk(web, rel, out) {
  for (const e of readdirSync(join(web, rel), { withFileTypes: true })) {
    const r = `${rel}/${e.name}`;
    if (e.isDirectory()) walk(web, r, out);
    else if (e.isFile()) out.push(r);
  }
}

export function fingerprint(web = WEB) {
  const rels = TOP_FILES.filter((f) => existsSync(join(web, f)));
  for (const d of SRC_DIRS) if (existsSync(join(web, d))) walk(web, d, rels);
  rels.sort();
  const h = createHash("sha256");
  for (const rel of rels) {
    // CRLF -> LF（git 在 Windows 上检出可能改成 CRLF）；latin1 来回转换逐字节不变
    const body = Buffer.from(readFileSync(join(web, rel)).toString("latin1").replaceAll("\r\n", "\n"), "latin1");
    h.update(`${rel}\0${createHash("sha256").update(body).digest("hex")}\n`, "utf8");
  }
  return h.digest("hex");
}

const fp = fingerprint();
if (process.argv.includes("--print")) console.log(fp);
else {
  writeFileSync(join(WEB, "dist", ".source-hash"), `${fp}\n`);
  console.log(`dist/.source-hash ${fp.slice(0, 12)}…`);
}
