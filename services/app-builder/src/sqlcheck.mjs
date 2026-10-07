// SQL lint for the smoke phase: SQL that doesn't run while the screens render
// (actions, and queries in event handlers) is compiled against schema.sql
// here, so a typo fails the build instead of the first tap on a phone.
//   - every statement of every actions/<name>.sql is prepared;
//   - every string literal passed to useQuery / getAllAsync / getFirstAsync /
//     runAsync in the app's source is prepared;
//   - runAction('<name>', {...}) needs an actions/<name>.sql, and an object
//     literal of params must supply each :param the action uses.
// Preparing compiles without running: syntax errors, unknown tables and
// unknown columns, nothing that depends on the data.
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { diagnostic, relative, sourceLine } from './diagnostics.mjs';

const QUERY_CALLS = new Set(['useQuery', 'getAllAsync', 'getFirstAsync', 'runAsync']);
const ACTION_RE = /^[a-z][a-zA-Z0-9_]*\.sql$/;

/** `sql` cut into statements at each `;` outside strings, quoted names and comments, with
 * the 1-based line each starts on; statements with no code (only comments) are dropped. */
export function splitStatements(sql) {
  const out = [];
  let start = 0;
  let code = false;
  let i = 0;
  const lineAt = (at) => sql.slice(0, at).split('\n').length;
  const push = (end) => {
    if (code) {
      const text = sql.slice(start, end);
      const lead = text.length - text.trimStart().length;
      out.push({ sql: text.trim(), line: lineAt(start + lead) });
    }
    start = end;
    code = false;
  };
  while (i < sql.length) {
    const ch = sql[i];
    const next = sql[i + 1];
    if (ch === '-' && next === '-') {
      const end = sql.indexOf('\n', i);
      i = end < 0 ? sql.length : end + 1;
      if (!code) start = i;
    } else if (ch === '/' && next === '*') {
      const end = sql.indexOf('*/', i + 2);
      i = end < 0 ? sql.length : end + 2;
      if (!code) start = i;
    } else if (ch === "'" || ch === '"' || ch === '`' || ch === '[') {
      const close = ch === '[' ? ']' : ch;
      let j = i + 1;
      while (j < sql.length) {
        if (sql[j] === close) {
          if (close !== ']' && sql[j + 1] === close) j += 2;
          else break;
        } else j++;
      }
      code = true;
      i = j + 1;
    } else if (ch === ';') {
      i++;
      push(i);
    } else {
      if (!/\s/.test(ch)) code = true;
      else if (!code) start = i + 1;
      i++;
    }
  }
  push(sql.length);
  return out.map((s) => ({ ...s, sql: s.sql.replace(/;\s*$/, '') }));
}

/** The named parameters (`:x`, `@x`, `$x`) used in `sql`, outside strings and comments. */
export function namedParams(sql) {
  const names = new Set();
  const stripped = sql
    .replace(/--[^\n]*/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/'(?:[^']|'')*'/g, "''")
    .replace(/"(?:[^"]|"")*"/g, '""');
  for (const m of stripped.matchAll(/(?<![\w:@$])[:@$]([A-Za-z_][A-Za-z0-9_]*)/g)) names.add(m[1]);
  return names;
}

function appSources(appRoot) {
  const out = [];
  const walk = (dir) => {
    for (const d of fs.readdirSync(dir, { withFileTypes: true })) {
      if (d.name.startsWith('.') || d.name === 'node_modules') continue;
      const p = path.join(dir, d.name);
      if (d.isDirectory()) walk(p);
      else if (/\.(tsx?|jsx?)$/.test(d.name)) out.push(p);
    }
  };
  walk(appRoot);
  return out;
}

const calleeName = (expr) =>
  ts.isIdentifier(expr) ? expr.text : ts.isPropertyAccessExpression(expr) ? expr.name.text : null;
const literalText = (node) =>
  node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : null;

/** Plain `{ a, b: 1, 'c': x }` keys, or null when the object has spreads or computed keys. */
function objectKeys(node) {
  if (!node || !ts.isObjectLiteralExpression(node)) return null;
  const keys = [];
  for (const p of node.properties) {
    if (ts.isShorthandPropertyAssignment(p)) keys.push(p.name.text);
    else if (ts.isPropertyAssignment(p) && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name))) keys.push(p.name.text);
    else return null;
  }
  return keys.map((k) => k.replace(/^[:@$]/, ''));
}

/** The SQL literals and runAction calls in the app's source, with where they are. */
export function sqlCalls(appRoot) {
  const queries = [];
  const actions = [];
  for (const abs of appSources(appRoot)) {
    const text = fs.readFileSync(abs, 'utf8');
    const file = relative(appRoot, abs);
    const kind = abs.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
    const visit = (node) => {
      if (ts.isCallExpression(node)) {
        const name = calleeName(node.expression);
        const first = node.arguments[0];
        const literal = literalText(first);
        if (literal !== null && (QUERY_CALLS.has(name) || name === 'runAction')) {
          const pos = sf.getLineAndCharacterOfPosition(first.getStart(sf) + 1);
          const at = { file, line: pos.line + 1, column: pos.character + 1 };
          if (name === 'runAction') actions.push({ ...at, name: literal, keys: node.arguments.length < 2 ? [] : objectKeys(node.arguments[1]) });
          else queries.push({ ...at, sql: literal.trim() });
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return { queries, actions };
}

function readActions(appRoot) {
  const dir = path.join(appRoot, 'actions');
  let names = [];
  try {
    names = fs.readdirSync(dir).filter((n) => ACTION_RE.test(n));
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
  }
  return new Map(names.map((n) => [n.slice(0, -4), fs.readFileSync(path.join(dir, n), 'utf8')]));
}

const prepareError = (db, sql) => {
  try {
    db.prepare(sql);
    return null;
  } catch (err) {
    return err.message;
  }
};

/** { diagnostics, bad } where `bad` holds the source SQL already reported, so the
 * render doesn't report it again. `db` has schema.sql (and any reads stand-ins) applied. */
export function checkSql(appRoot, db) {
  const diagnostics = [];
  const bad = new Set();
  const actions = readActions(appRoot);
  const actionParams = new Map();
  for (const [name, text] of actions) {
    const file = `actions/${name}.sql`;
    const statements = splitStatements(text);
    if (statements.length === 0) {
      diagnostics.push(diagnostic({ step: 'sql', file, message: `${file} has no statements` }));
      continue;
    }
    const params = new Set();
    for (const st of statements) {
      for (const p of namedParams(st.sql)) params.add(p);
      const error = prepareError(db, st.sql);
      if (error) {
        diagnostics.push(
          diagnostic({
            step: 'sql',
            file,
            line: st.line,
            source: sourceLine(appRoot, file, st.line),
            message: `${error} in action ${name}: ${st.sql}`,
          }),
        );
      }
    }
    actionParams.set(name, params);
  }
  const { queries, actions: calls } = sqlCalls(appRoot);
  for (const q of queries) {
    const error = prepareError(db, q.sql);
    if (!error) continue;
    bad.add(q.sql);
    diagnostics.push(
      diagnostic({ step: 'sql', file: q.file, line: q.line, column: q.column, source: sourceLine(appRoot, q.file, q.line), message: `${error} in SQL: ${q.sql}` }),
    );
  }
  for (const call of calls) {
    const at = { step: 'sql', file: call.file, line: call.line, column: call.column, source: sourceLine(appRoot, call.file, call.line) };
    if (!actions.has(call.name)) {
      const known = [...actions.keys()];
      diagnostics.push(
        diagnostic({ ...at, message: `runAction('${call.name}') has no actions/${call.name}.sql${known.length ? ` (the app's actions: ${known.join(', ')})` : ''}` }),
      );
      continue;
    }
    if (call.keys === null) continue;
    const missing = [...actionParams.get(call.name)].filter((p) => !call.keys.includes(p));
    if (missing.length) {
      diagnostics.push(
        diagnostic({
          ...at,
          message: `runAction('${call.name}') doesn't pass ${missing.map((p) => `:${p}`).join(', ')}, which actions/${call.name}.sql uses`,
        }),
      );
    }
  }
  return { diagnostics, bad };
}
