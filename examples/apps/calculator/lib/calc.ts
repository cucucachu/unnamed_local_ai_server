/**
 * The calculator's arithmetic: + − × ÷, ^ (power), % (a hundredth),
 * parentheses and a leading minus. A small recursive-descent parser; no
 * `eval`. `evaluate` returns the number, or an error to show.
 */
export type Result = { value: number } | { error: string };

type Token = { kind: 'num'; value: number } | { kind: 'op'; value: string };

const OPS = new Set(['+', '−', '-', '×', '*', '÷', '/', '^', '%', '(', ')']);
const NORMAL: Record<string, string> = { '-': '−', '*': '×', '/': '÷' };

function tokenize(text: string): Token[] | string {
  const out: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === ' ' || c === ',') {
      i++;
    } else if (/[0-9.]/.test(c)) {
      let j = i;
      while (j < text.length && /[0-9.,]/.test(text[j])) j++;
      const raw = text.slice(i, j).replace(/,/g, '');
      if ((raw.match(/\./g) ?? []).length > 1 || raw === '.') return `“${raw}” isn't a number`;
      out.push({ kind: 'num', value: Number(raw) });
      i = j;
    } else if (OPS.has(c)) {
      out.push({ kind: 'op', value: NORMAL[c] ?? c });
      i++;
    } else {
      return `“${c}” isn't something I can work with`;
    }
  }
  return out;
}

export function evaluate(text: string): Result {
  const lexed = tokenize(text);
  if (typeof lexed === 'string') return { error: lexed };
  const tokens: Token[] = lexed;
  if (!tokens.length) return { error: 'Nothing to work out' };
  let at = 0;
  const peek = () => tokens[at];
  const isOp = (v: string) => peek()?.kind === 'op' && peek()!.value === v;

  function expr(): number {
    let n = term();
    while (isOp('+') || isOp('−')) {
      const op = tokens[at++].value;
      const m = term();
      n = op === '+' ? n + m : n - m;
    }
    return n;
  }
  function term(): number {
    let n = unary();
    while (isOp('×') || isOp('÷')) {
      const op = tokens[at++].value;
      const m = unary();
      if (op === '÷' && m === 0) throw new Error("Can't divide by 0");
      n = op === '×' ? n * m : n / m;
    }
    return n;
  }
  function unary(): number {
    if (isOp('−')) {
      at++;
      return -unary();
    }
    if (isOp('+')) {
      at++;
      return unary();
    }
    return power();
  }
  function power(): number {
    const base = postfix();
    if (isOp('^')) {
      at++;
      return base ** unary();
    }
    return base;
  }
  function postfix(): number {
    let n = primary();
    while (isOp('%')) {
      at++;
      n /= 100;
    }
    return n;
  }
  function primary(): number {
    const t = tokens[at++];
    if (!t) throw new Error('It ends too soon');
    if (t.kind === 'num') return t.value;
    if (t.value === '(') {
      const n = expr();
      if (!isOp(')')) throw new Error('A “(” is missing its “)”');
      at++;
      return n;
    }
    throw new Error(`“${t.value}” is in the wrong place`);
  }

  try {
    const value = expr();
    if (at < tokens.length) throw new Error(`“${tokens[at].value}” is in the wrong place`);
    if (!Number.isFinite(value)) throw new Error('Too big to show');
    return { value };
  } catch (e) {
    return { error: (e as Error).message };
  }
}

/** A result for the screen: 12 significant digits, separators, and e-notation when it's huge or tiny. */
export function formatNumber(n: number): string {
  const a = Math.abs(n);
  if (a !== 0 && (a >= 1e15 || a < 1e-9)) return n.toExponential(8).replace(/\.?0+e/, 'e');
  return Number(n.toPrecision(12)).toLocaleString('en-US', { maximumFractionDigits: 10 });
}
