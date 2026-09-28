// `tsc --noEmit` of the app against React, React Native and the SDK/shim
// typings (packages/homeai-sdk/types/homeai.d.ts), with the ES2020 lib only: no DOM, no Node.
//
// React Native's own typings declare `fetch`, `XMLHttpRequest`, `WebSocket`
// and `require` as globals (they exist in a real RN app), so leaving out the
// DOM lib alone doesn't keep them out of app code (D9). Every reference to
// one of FORBIDDEN_GLOBALS that resolves to a global declaration is reported
// too, as is reaching one through globalThis (by name, computed, or with
// globalThis used as a value), and so are triple-slash directives, which
// could pull the DOM lib back in. This is a check for the model's benefit,
// not the boundary: the
// sandbox's CSP is (docs/PLATFORM.md §7).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { diagnostic, relative } from './diagnostics.mjs';
import { SDK_TYPES } from './sdk.mjs';

const builderRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const modules = path.join(builderRoot, 'node_modules');
export { SDK_TYPES };

const NO_NETWORK = 'the app sandbox has no network; read and write data with useQuery / useDatabase / runAction from @homeai/sdk';
// "Cannot find name" and its variants that suggest adding the DOM or Node typings.
const CANNOT_FIND_NAME = new Set([2304, 2580, 2582, 2584, 2591]);
const NO_DOM = 'apps are React Native code and have no DOM: use react-native components and expo-router';
export const FORBIDDEN_GLOBALS = new Map([
  ['fetch', NO_NETWORK],
  ['fetchBundle', NO_NETWORK],
  ['XMLHttpRequest', NO_NETWORK],
  ['XMLHttpRequestUpload', NO_NETWORK],
  ['originalXMLHttpRequest', NO_NETWORK],
  ['WebSocket', NO_NETWORK],
  ['EventSource', NO_NETWORK],
  ['require', 'use import statements, from the allowed modules or the app\'s own files'],
  ['window', NO_DOM],
  ['self', NO_DOM],
  ['global', 'apps are React Native code, not Node: use the globals directly'],
  ['document', NO_DOM],
  ['navigator', NO_DOM],
  ['location', NO_DOM],
  ['localStorage', 'keep data in the app database (@homeai/sdk)'],
  ['sessionStorage', 'keep data in the app database (@homeai/sdk)'],
]);

export const COMPILER_OPTIONS = {
  noEmit: true,
  strict: true,
  noImplicitAny: false,
  target: ts.ScriptTarget.ES2020,
  lib: ['lib.es2020.d.ts'],
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  jsx: ts.JsxEmit.ReactJSX,
  allowJs: true,
  checkJs: false,
  resolveJsonModule: true,
  esModuleInterop: true,
  skipLibCheck: true,
  types: [],
  typeRoots: [],
  paths: {
    react: [path.join(modules, '@types/react/index.d.ts')],
    'react/jsx-runtime': [path.join(modules, '@types/react/jsx-runtime.d.ts')],
    'react-native': [path.join(modules, 'react-native/types/index.d.ts')],
  },
};

const APP_EXT = new Set(['.ts', '.tsx', '.js', '.jsx']);

function appFiles(dir) {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => !d.name.startsWith('.') && d.name !== 'node_modules')
    .flatMap((d) => {
      const p = path.join(dir, d.name);
      if (d.isDirectory()) return appFiles(p);
      return d.isFile() && APP_EXT.has(path.extname(d.name)) && !d.name.endsWith('.d.ts') ? [p] : [];
    });
}

function position(sf, pos) {
  const { line, character } = sf.getLineAndCharacterOfPosition(pos);
  return { line: line + 1, column: character + 1, source: sf.text.split('\n')[line] };
}

function isGlobalDeclaration(decl) {
  for (let node = decl.parent; node; node = node.parent) {
    if (ts.isSourceFile(node)) return !ts.isExternalModule(node);
    if (ts.isModuleDeclaration(node)) return (node.flags & ts.NodeFlags.GlobalAugmentation) !== 0;
    if (ts.isFunctionLike(node) || ts.isBlock(node) || ts.isClassLike(node)) return false;
  }
  return false;
}

const GLOBAL_THIS_VALUE = 'globalThis can only be used as globalThis.<name> in apps, not as a value; use the global directly';
const GLOBAL_THIS_COMPUTED = 'computed access on globalThis is not allowed in apps; use the global directly';

/** `expr` with the parentheses, `!` and type assertions around it removed. */
function unwrap(expr) {
  while (ts.isParenthesizedExpression(expr) || ts.isNonNullExpression(expr) || ts.isAsExpression(expr) || ts.isTypeAssertionExpression(expr) || ts.isSatisfiesExpression(expr)) expr = expr.expression;
  return expr;
}

/** The expression `node` sits in once the wrappers `unwrap` removes are included. */
function wrapped(node) {
  while (ts.isParenthesizedExpression(node.parent) || ts.isNonNullExpression(node.parent) || ts.isAsExpression(node.parent) || ts.isTypeAssertionExpression(node.parent) || ts.isSatisfiesExpression(node.parent)) node = node.parent;
  return node;
}

// `globalThis.fetch`, `globalThis['fetch']` and `const { fetch } = globalThis`
// reach a forbidden global without naming it as one. Anything typed as the
// global object (`globalThis.globalThis`, an alias) is followed by type.
function globalThisAccess(checker, sf) {
  const globalThisSymbol = checker.resolveName('globalThis', sf, ts.SymbolFlags.Value, false);
  const globalType = globalThisSymbol && checker.getTypeOfSymbol(globalThisSymbol);
  if (!globalType) return () => null;
  const isAccess = (node) => ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node);
  const isGlobalObject = (expr) => checker.getTypeAtLocation(unwrap(expr)) === globalType;
  const member = (node) => {
    if (ts.isPropertyAccessExpression(node)) {
      return FORBIDDEN_GLOBALS.has(node.name.text) ? [node.name, `"${node.name.text}" is not available in apps: ${FORBIDDEN_GLOBALS.get(node.name.text)}`] : null;
    }
    const arg = node.argumentExpression;
    if (ts.isStringLiteralLike(arg)) {
      return FORBIDDEN_GLOBALS.has(arg.text) ? [arg, `"${arg.text}" is not available in apps: ${FORBIDDEN_GLOBALS.get(arg.text)}`] : null;
    }
    return ts.isNumericLiteral(arg) ? null : [arg, GLOBAL_THIS_COMPUTED];
  };
  const isGlobalValue = (node) =>
    ts.isIdentifier(node)
      ? node.text === 'globalThis' && !ts.isTypeQueryNode(node.parent) && !ts.isQualifiedName(node.parent) && !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node) && checker.getSymbolAtLocation(node) === globalThisSymbol
      : isAccess(node) && checker.getTypeAtLocation(node) === globalType;
  return (node) => {
    if (isAccess(node) && isGlobalObject(node.expression)) {
      const found = member(node);
      if (found) return found;
    }
    if (isGlobalValue(node)) {
      const outer = wrapped(node);
      return isAccess(outer.parent) && outer.parent.expression === outer ? null : [node, GLOBAL_THIS_VALUE];
    }
    return null;
  };
}

function forbiddenGlobals(checker, sf, appRoot) {
  const out = [];
  const report = (node, message) => out.push(diagnostic({ step: 'type', file: relative(appRoot, sf.fileName), ...position(sf, node.getStart(sf)), message }));
  const viaGlobalThis = globalThisAccess(checker, sf);
  const visit = (node) => {
    if (ts.isIdentifier(node) && FORBIDDEN_GLOBALS.has(node.text)) {
      const parent = node.parent;
      const isName = (ts.isPropertyAccessExpression(parent) && parent.name === node) || (ts.isPropertyAssignment(parent) && parent.name === node);
      const symbol = isName ? undefined : checker.getSymbolAtLocation(node);
      if (symbol?.declarations?.length && symbol.declarations.every(isGlobalDeclaration)) {
        report(node, `"${node.text}" is not available in apps: ${FORBIDDEN_GLOBALS.get(node.text)}`);
      }
    }
    const found = viaGlobalThis(node);
    if (found) report(...found);
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

function tripleSlash(sf, appRoot) {
  const refs = [...sf.referencedFiles, ...sf.typeReferenceDirectives, ...sf.libReferenceDirectives];
  return refs.map((ref) =>
    diagnostic({
      step: 'type',
      file: relative(appRoot, sf.fileName),
      ...position(sf, ref.pos),
      message: 'triple-slash directives (/// <reference ...>) are not allowed in apps; import what you need from the allowed modules',
    }),
  );
}

/** { diagnostics, ms } for the app at `appRoot`; empty diagnostics means it type-checks. */
export function typecheckApp(appRoot) {
  appRoot = path.resolve(appRoot);
  const t0 = performance.now();
  const files = appFiles(appRoot);
  const program = ts.createProgram({ rootNames: [...files, SDK_TYPES], options: COMPILER_OPTIONS });
  const checker = program.getTypeChecker();
  const inApp = (sf) => sf && path.resolve(sf.fileName).startsWith(appRoot + path.sep);
  const diagnostics = [];
  for (const d of ts.getPreEmitDiagnostics(program)) {
    if (d.file && !inApp(d.file)) continue;
    const at = d.file && d.start !== undefined ? position(d.file, d.start) : { line: null, column: null };
    const name = d.file && d.start !== undefined ? d.file.text.slice(d.start, d.start + d.length) : '';
    const message =
      CANNOT_FIND_NAME.has(d.code) && FORBIDDEN_GLOBALS.has(name)
        ? `"${name}" is not available in apps: ${FORBIDDEN_GLOBALS.get(name)}`
        : `TS${d.code}: ${ts.flattenDiagnosticMessageText(d.messageText, '\n')}`;
    diagnostics.push(diagnostic({ step: 'type', file: d.file ? relative(appRoot, d.file.fileName) : '', ...at, message }));
  }
  for (const sf of program.getSourceFiles().filter(inApp)) {
    diagnostics.push(...tripleSlash(sf, appRoot), ...forbiddenGlobals(checker, sf, appRoot));
  }
  diagnostics.sort((a, b) => a.file.localeCompare(b.file) || (a.line ?? 0) - (b.line ?? 0) || (a.column ?? 0) - (b.column ?? 0));
  return { diagnostics, ms: +(performance.now() - t0).toFixed(1) };
}
