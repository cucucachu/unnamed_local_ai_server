// Runtime bundle: React + react-dom/client + react-native-web + the router
// shim + SDK + bridge, as one IIFE (docs/PLATFORM.md §7). Built into dist/
// when the image is built; the smoke render uses runtime.dev.js.
//
//   node src/build-runtime.mjs    -> dist/runtime.js, dist/runtime.dev.js
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const builderRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export async function buildRuntime({ dev }) {
  const res = await esbuild.build({
    entryPoints: [path.join(builderRoot, 'runtime/index.tsx')],
    absWorkingDir: builderRoot,
    bundle: true,
    write: false,
    outfile: path.join(builderRoot, 'dist/runtime.js'),
    format: 'iife',
    platform: 'browser',
    target: 'es2020',
    jsx: 'automatic',
    minify: !dev,
    alias: { 'react-native': 'react-native-web' },
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
    logLevel: 'warning',
  });
  return res.outputFiles[0].text;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  fs.mkdirSync(path.join(builderRoot, 'dist'), { recursive: true });
  for (const dev of [false, true]) {
    const code = await buildRuntime({ dev });
    fs.writeFileSync(path.join(builderRoot, `dist/runtime${dev ? '.dev' : ''}.js`), code);
    console.log(`dist/runtime${dev ? '.dev' : ''}.js: ${Buffer.byteLength(code)} bytes`);
  }
}
