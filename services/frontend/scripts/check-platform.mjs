#!/usr/bin/env node
// check-platform.mjs — M6-02 static parity sweep.
//
// Flags web-only globals (window., document., localStorage, navigator.) used
// outside of `.web.ts(x)` files, since those APIs don't exist on native and
// would crash at runtime instead of failing a build. Two things are exempt,
// because both are the app's own established, deliberate patterns (see
// lib/files.ts, lib/api.ts, src/app/(tabs)/files.tsx) rather than mistakes:
//
//   1. Code inside an `if (Platform.OS === 'web') { ... }` block.
//   2. Code inside a function whose name ends in `Web` (e.g.
//      `pickAndUploadWeb`) — the "split into two same-file functions, one
//      per platform" alternative to a `.web.ts` file, used when the
//      non-web branch is a one-liner and a whole extra file would be
//      overkill.
//
// This is a lightweight brace-counting heuristic, not a real parser — it's
// intentionally simple (per the ticket) and tuned to this codebase's actual
// style. It does not handle every conceivable edge case (braces inside
// strings/template literals/comments are not specially handled), so treat a
// clean run as "no *obvious* platform leaks", not a formal guarantee.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const SCAN_DIRS = ['src', 'lib', 'components'];
const SKIP_DIR_NAMES = new Set(['node_modules', '__tests__', '.expo', 'dist']);
const FILE_EXTENSIONS = ['.ts', '.tsx'];
const WEB_ONLY_FILE = /\.web\.tsx?$/;
const WEB_ONLY_FUNCTION_NAME = /Web$/;

// Matches a `function`/arrow/const function declaration and captures its name,
// e.g. `function pickAndUploadWeb(`, `const pickAndUploadWeb = (`, `async function foo(`.
const FUNCTION_DECL = /(?:function\s+|const\s+|let\s+)([A-Za-z0-9_]+)\s*(?::[^=]+)?=?\s*(?:async\s*)?\(/;
const FUNCTION_DECL_KEYWORD = /^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\(/;

const FLAGGED_APIS = [
  { name: 'window', pattern: /\bwindow\./ },
  { name: 'document', pattern: /\bdocument\./ },
  { name: 'localStorage', pattern: /\blocalStorage\b/ },
  { name: 'navigator', pattern: /\bnavigator\./ },
];

function listFiles(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    if (SKIP_DIR_NAMES.has(entry)) continue;
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      out.push(...listFiles(full));
    } else if (FILE_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full);
    }
  }
  return out;
}

function braceDelta(line) {
  let delta = 0;
  for (const ch of line) {
    if (ch === '{') delta += 1;
    else if (ch === '}') delta -= 1;
  }
  return delta;
}

function scanFile(path) {
  const violations = [];
  const lines = readFileSync(path, 'utf8').split('\n');

  let depth = 0;
  // Stack of { depth } marking the brace-depth at which an exemption (web
  // guard or *Web-named function) became active; active while depth >= that.
  const exemptionStack = [];
  let inBlockComment = false;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();

    // Skip `//` line comments and `/** ... */` block comments (including
    // the JSDoc-style " * ..." continuation lines this codebase uses
    // heavily) — mentioning `window.foo` in prose shouldn't be flagged as
    // using it. Not a real tokenizer: doesn't handle a `/* */` comment that
    // shares a line with real code, which this codebase doesn't do anyway.
    let isCommentLine = false;
    if (inBlockComment) {
      isCommentLine = true;
      if (trimmed.includes('*/')) inBlockComment = false;
    } else if (trimmed.startsWith('//')) {
      isCommentLine = true;
    } else if (trimmed.startsWith('/*')) {
      isCommentLine = true;
      inBlockComment = !trimmed.includes('*/');
    }

    if (!isCommentLine) {
      const isGuardOpen = /Platform\.OS\s*===\s*['"]web['"]/.test(line);
      const fnMatch = FUNCTION_DECL_KEYWORD.exec(line) || FUNCTION_DECL.exec(line);
      const isWebFunction = fnMatch !== null && WEB_ONLY_FUNCTION_NAME.test(fnMatch[1]);

      const delta = braceDelta(line);
      const opensBlock = delta > 0 && /\{/.test(line);

      if (exemptionStack.length === 0) {
        for (const api of FLAGGED_APIS) {
          if (api.pattern.test(line)) {
            violations.push({ line: i + 1, api: api.name, text: trimmed });
          }
        }
      }

      if (opensBlock && (isGuardOpen || isWebFunction)) {
        exemptionStack.push(depth + 1);
      }

      depth += delta;

      while (exemptionStack.length > 0 && depth < exemptionStack[exemptionStack.length - 1]) {
        exemptionStack.pop();
      }
    }
  }

  return violations;
}

function main() {
  const files = SCAN_DIRS.flatMap((d) => listFiles(join(ROOT, d))).filter(
    (f) => !WEB_ONLY_FILE.test(f)
  );

  let violationCount = 0;
  for (const file of files) {
    const violations = scanFile(file);
    for (const v of violations) {
      violationCount += 1;
      console.error(`${relative(ROOT, file)}:${v.line}: uses '${v.api}' outside a .web file / Platform.OS === 'web' guard / *Web()-named function\n    ${v.text}`);
    }
  }

  if (violationCount > 0) {
    console.error(`\ncheck-platform: ${violationCount} potential native-parity issue(s) found.`);
    process.exit(1);
  }

  console.log(`check-platform: OK (${files.length} files scanned, no unguarded web-only globals found).`);
}

main();
