#!/usr/bin/env bash
# Experiment 4: what react-navigation would add to the runtime, vs the shim.
#   bash tests/router-size.sh    -> results/router-size.json (installs into a temp dir; needs npm registry access)
set -euo pipefail
spike="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d /tmp/homeai-rn-nav-XXXXXX)"
trap 'rm -rf "$work"' EXIT
cd "$work"
npm init -y >/dev/null
npm i --silent --no-audit --no-fund @react-navigation/native@7 @react-navigation/stack@7 @react-navigation/native-stack@7 \
  react-native-gesture-handler@~2.32.0 react-native-screens@~4.26.0 react-native-safe-area-context@~5.7.0 \
  react@19.2.3 react-dom@19.2.3 react-native-web@0.21.2 esbuild@0.25 >/dev/null 2>&1
printf "import { NavigationContainer } from '@react-navigation/native';\nimport { createStackNavigator } from '@react-navigation/stack';\nexport { NavigationContainer, createStackNavigator };\n" > stack.jsx
printf "import { NavigationContainer } from '@react-navigation/native';\nimport { createNativeStackNavigator } from '@react-navigation/native-stack';\nexport { NavigationContainer, createNativeStackNavigator };\n" > native-stack.jsx
SPIKE="$spike" node --input-type=module -e "
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import zlib from 'node:zlib';
const size = async (entry, opts) => {
  const r = await esbuild.build({ entryPoints: [entry], bundle: true, write: false, minify: true, format: 'esm', jsx: 'automatic', metafile: true,
    define: { 'process.env.NODE_ENV': '\"production\"' }, logLevel: 'silent', ...opts });
  const c = r.outputFiles[0].contents;
  return { bytes: c.length, gzip: zlib.gzipSync(c, { level: 9 }).length };
};
const nav = { alias: { 'react-native': 'react-native-web' }, external: ['react', 'react-dom', 'react-native-web', 'react/jsx-runtime'],
  loader: { '.js': 'jsx', '.png': 'dataurl' }, resolveExtensions: ['.web.js', '.web.tsx', '.js', '.jsx', '.ts', '.tsx', '.json'] };
const pkg = (p) => JSON.parse(fs.readFileSync('node_modules/' + p + '/package.json')).version;
const out = {
  shim: await size(process.env.SPIKE + '/runtime/router.tsx', { absWorkingDir: process.env.SPIKE, external: ['react', 'react-native', 'react/jsx-runtime'] }),
  reactNavigationStack: { ...(await size('stack.jsx', nav)), versions: { native: pkg('@react-navigation/native'), stack: pkg('@react-navigation/stack') } },
  reactNavigationNativeStackOnWeb: { ...(await size('native-stack.jsx', nav)), versions: { 'native-stack': pkg('@react-navigation/native-stack') } },
  note: 'Delta on top of react + react-dom + react-native-web (all external). PNG assets inlined as data URLs.',
};
fs.writeFileSync(process.env.SPIKE + '/results/router-size.json', JSON.stringify(out, null, 2) + '\n');
console.log(JSON.stringify(out, null, 2));
"
