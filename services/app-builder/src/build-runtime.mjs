// The SDK's runtime bundle and optional modules (packages/homeai-sdk/build.mjs),
// built with this builder's esbuild and node_modules into dist/ when the image
// is built; the smoke render uses runtime.dev.js (and three.dev.js for apps
// that import three).
//
//   node src/build-runtime.mjs    -> dist/runtime.js, dist/runtime.dev.js, dist/three.js, dist/three.dev.js
import * as esbuild from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildAll } from './sdk.mjs';

const builderRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

if (import.meta.url === `file://${process.argv[1]}`) {
  await buildAll(esbuild, path.join(builderRoot, 'dist'), { nodePaths: [path.join(builderRoot, 'node_modules')] });
}
