// Runtime bundle: React + react-dom + react-native-web + router shim + SDK +
// bridge, as one IIFE. Built once per SDK version, cached by the client.
//
//   node build/build-runtime.mjs            -> dist/runtime.js (+ dist/runtime.meta.json)
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const spike = path.resolve(here, '..');

export async function buildRuntime({ dev = false, curated = false } = {}) {
  const t0 = performance.now();
  const res = await esbuild.build({
    entryPoints: [path.join(spike, 'runtime/index.tsx')],
    absWorkingDir: spike,
    bundle: true,
    write: false,
    outfile: path.join(spike, 'dist/runtime.js'),
    format: 'iife',
    platform: 'browser',
    target: 'es2020',
    jsx: 'automatic',
    minify: !dev,
    metafile: true,
    alias: { 'react-native': curated ? path.join(spike, 'runtime/rn-curated.ts') : 'react-native-web' },
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
    logLevel: 'warning',
  });
  const code = res.outputFiles[0].text;
  const byPackage = {};
  for (const [file, info] of Object.entries(res.metafile.outputs[Object.keys(res.metafile.outputs)[0]].inputs)) {
    const m = file.match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
    const key = m ? m[1] : 'runtime (shim+sdk+bridge)';
    byPackage[key] = (byPackage[key] ?? 0) + info.bytesInOutput;
  }
  return {
    code,
    stats: {
      bytes: Buffer.byteLength(code),
      gzip: zlib.gzipSync(code, { level: 9 }).length,
      brotli: zlib.brotliCompressSync(code).length,
      buildMs: +(performance.now() - t0).toFixed(1),
      byPackage: Object.fromEntries(Object.entries(byPackage).sort((a, b) => b[1] - a[1])),
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dev = process.argv.includes('--dev');
  const curated = process.argv.includes('--curated');
  const { code, stats } = await buildRuntime({ dev, curated });
  fs.mkdirSync(path.join(spike, 'dist'), { recursive: true });
  fs.writeFileSync(path.join(spike, `dist/runtime${curated ? '.curated' : ''}${dev ? '.dev' : ''}.js`), code);
  console.log(JSON.stringify(stats, null, 2));
}
