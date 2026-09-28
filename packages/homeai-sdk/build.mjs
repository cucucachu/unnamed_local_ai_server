// Runtime bundle: React + react-dom/client + react-native-web + the router
// shim + SDK + bridge, as one IIFE per SDK version (docs/PLATFORM.md §7).
// The builder image and the Caddy image each build it with their own
// esbuild from `runtimeBuildOptions`, so this file imports nothing at the top.
//
//   node build.mjs [--out DIR]    -> DIR/runtime.js, DIR/runtime.dev.js (default dist/)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SDK_ROOT = path.dirname(fileURLToPath(import.meta.url));
export const SDK_VERSION = '1';
export const SDK_TYPES = path.join(SDK_ROOT, 'types', 'homeai.d.ts');
export const ALLOWED_MODULES = JSON.parse(fs.readFileSync(path.join(SDK_ROOT, 'modules.json'), 'utf8'));

/** esbuild options; `nodePaths` is where react & co. are installed when not below this package. */
export function runtimeBuildOptions({ dev, nodePaths = [] }) {
  return {
    entryPoints: [path.join(SDK_ROOT, 'src/runtime.tsx')],
    absWorkingDir: SDK_ROOT,
    nodePaths,
    bundle: true,
    write: false,
    outfile: path.join(SDK_ROOT, 'dist/runtime.js'),
    format: 'iife',
    platform: 'browser',
    target: 'es2020',
    jsx: 'automatic',
    minify: !dev,
    alias: { 'react-native': 'react-native-web' },
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
    logLevel: 'warning',
  };
}

export async function buildRuntime(esbuild, { dev, nodePaths }) {
  const res = await esbuild.build(runtimeBuildOptions({ dev, nodePaths }));
  return res.outputFiles[0].text;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const i = process.argv.indexOf('--out');
  const out = i >= 0 ? path.resolve(process.argv[i + 1]) : path.join(SDK_ROOT, 'dist');
  const esbuild = await import('esbuild');
  fs.mkdirSync(out, { recursive: true });
  for (const dev of [false, true]) {
    const code = await buildRuntime(esbuild, { dev });
    const name = `runtime${dev ? '.dev' : ''}.js`;
    fs.writeFileSync(path.join(out, name), code);
    console.log(`${path.relative(process.cwd(), path.join(out, name))}: ${Buffer.byteLength(code)} bytes`);
  }
}
