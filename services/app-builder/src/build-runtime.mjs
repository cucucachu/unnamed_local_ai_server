// The SDK's runtime bundle (packages/homeai-sdk/build.mjs), built with this
// builder's esbuild and node_modules into dist/ when the image is built; the
// smoke render uses runtime.dev.js.
//
//   node src/build-runtime.mjs    -> dist/runtime.js, dist/runtime.dev.js
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildRuntime } from './sdk.mjs';

const builderRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

if (import.meta.url === `file://${process.argv[1]}`) {
  fs.mkdirSync(path.join(builderRoot, 'dist'), { recursive: true });
  for (const dev of [false, true]) {
    const code = await buildRuntime(esbuild, { dev, nodePaths: [path.join(builderRoot, 'node_modules')] });
    fs.writeFileSync(path.join(builderRoot, `dist/runtime${dev ? '.dev' : ''}.js`), code);
    console.log(`dist/runtime${dev ? '.dev' : ''}.js: ${Buffer.byteLength(code)} bytes`);
  }
}
