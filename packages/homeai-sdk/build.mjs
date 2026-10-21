// Runtime bundle: React + react-dom/client + react-native-web + the router
// shim + SDK + bridge, as one IIFE per SDK version (docs/PLATFORM.md §7).
// The builder image and the Caddy image each build it with their own
// esbuild from `runtimeBuildOptions`, so this file imports nothing at the top.
//
//   node build.mjs [--out DIR]    -> DIR/runtime.js, DIR/runtime.dev.js, DIR/three.js, DIR/three.dev.js (default dist/)
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
    // react-native-web's Animated calls global.cancelAnimationFrame.
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production'), global: 'globalThis' },
    logLevel: 'warning',
  };
}

export async function buildRuntime(esbuild, { dev, nodePaths }) {
  const res = await esbuild.build(runtimeBuildOptions({ dev, nodePaths }));
  return res.outputFiles[0].text;
}

/**
 * Allowed modules too big to put in every app's document (three is ~700 KB):
 * each is its own IIFE, `<name>.js` next to the runtime, that registers the
 * module with the runtime. Hosts add it after the runtime only for bundles
 * that require it (OPTIONAL_MODULES in src/protocol.ts, the same list).
 */
export const OPTIONAL_MODULES = ['three'];

/** The optional modules a built bundle requires (its externals stay `require("<name>")`; optionalModulesOf in src/protocol.ts). */
export function optionalModulesOf(code) {
  return OPTIONAL_MODULES.filter((m) => new RegExp(`\\brequire\\(\\s*["'\`]${m}["'\`]\\s*\\)`).test(code));
}

export function moduleBuildOptions(name, { dev, nodePaths = [] }) {
  const id = JSON.stringify(name);
  return {
    stdin: { contents: `import * as m from ${id};\nwindow.__homeai_provide(${id}, m);`, resolveDir: SDK_ROOT, loader: 'js' },
    absWorkingDir: SDK_ROOT,
    nodePaths,
    bundle: true,
    write: false,
    outfile: path.join(SDK_ROOT, `dist/${name}.js`),
    format: 'iife',
    platform: 'browser',
    target: 'es2020',
    minify: !dev,
    define: { 'process.env.NODE_ENV': JSON.stringify(dev ? 'development' : 'production') },
    logLevel: 'warning',
  };
}

export async function buildModule(esbuild, name, { dev, nodePaths }) {
  const res = await esbuild.build(moduleBuildOptions(name, { dev, nodePaths }));
  return res.outputFiles[0].text;
}

/** runtime.js, runtime.dev.js and each optional module's <name>.js / <name>.dev.js into `out`. */
export async function buildAll(esbuild, out, { nodePaths } = {}) {
  fs.mkdirSync(out, { recursive: true });
  const files = [];
  for (const dev of [false, true]) {
    const suffix = dev ? '.dev' : '';
    files.push([`runtime${suffix}.js`, await buildRuntime(esbuild, { dev, nodePaths })]);
    for (const name of OPTIONAL_MODULES) files.push([`${name}${suffix}.js`, await buildModule(esbuild, name, { dev, nodePaths })]);
  }
  for (const [name, code] of files) {
    fs.writeFileSync(path.join(out, name), code);
    console.log(`${path.relative(process.cwd(), path.join(out, name))}: ${Buffer.byteLength(code)} bytes`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const i = process.argv.indexOf('--out');
  const out = i >= 0 ? path.resolve(process.argv[i + 1]) : path.join(SDK_ROOT, 'dist');
  await buildAll(await import('esbuild'), out);
}
