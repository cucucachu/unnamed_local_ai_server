#!/usr/bin/env bash
# Experiment 5: smoke-render in a network-less, read-only container.
#   bash smoke/run-docker.sh    -> results/smoke.json
# Expects `groceries` to pass and `render-throw` (deliberate bugs) to fail with diagnostics.
set -euo pipefail
spike="$(cd "$(dirname "$0")/.." && pwd)"
cd "$spike"
node build/build-runtime.mjs --dev >/dev/null
set +e
docker run --rm --network none --read-only --cap-drop ALL --user "$(id -u):$(id -g)" \
  -v "$spike":/spike:ro -w /spike node:22-alpine \
  node smoke/smoke-render.mjs apps/groceries apps/render-throw 2>/dev/null > results/smoke.json
code=$?
set -e
node -e '
const r = JSON.parse(require("fs").readFileSync("results/smoke.json", "utf8"));
for (const a of r) {
  console.log(`${a.app}: ok=${a.ok} totalMs=${a.totalMs} routes=${a.routes.map((x) => `${x.path}(${x.ms}ms)`).join(" ")}`);
  for (const d of a.diagnostics) console.log(`  [${d.kind}] ${d.file ? `${d.file}:${d.line}:${d.column} ` : ""}${d.message}${d.sql ? `  sql=${d.sql}` : ""}${d.componentStack ? `  in ${d.componentStack.join(" < ")}` : ""}`);
}
const [good, bad] = r;
const pass = good.ok && !bad.ok && bad.diagnostics.some((d) => d.kind === "render" && d.file === "app/item/[id].tsx" && d.line === 14) && bad.diagnostics.some((d) => d.kind === "sql");
console.log(pass ? "PASS  expected outcome" : "FAIL  unexpected outcome");
process.exit(pass ? 0 : 1);
'
echo "(container exit code $code; 1 is expected because render-throw fails)"
