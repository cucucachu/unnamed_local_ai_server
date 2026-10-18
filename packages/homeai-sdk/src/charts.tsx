// Charts for @homeai/ui (M22-04): bar, line and pie over plain arrays, drawn
// as SVG (the sandbox is always react-native-web, so SVG elements render as
// they would in react-dom). Each chart is one `role="img"` whose label is a
// text summary; the drawing itself is hidden from screen readers.
import { createElement as h, useState, type ReactNode } from 'react';
import { Text, View, type LayoutChangeEvent } from 'react-native';
import { useTheme, type Theme } from './theme';

type Row = object;
const get = (r: object, k: string) => (r as Record<string, unknown>)[k];
type Key<T> = keyof T & string;

export type ChartProps<T extends Row> = {
  data: readonly T[] | null | undefined;
  /** The field for each point's label (a category, a date string, a number). */
  x: Key<T>;
  /** The field to plot, or several for one series each. */
  y: Key<T> | readonly Key<T>[];
  /** Series names for the legend and summary; defaults to the field names. */
  names?: Partial<Record<Key<T>, string>>;
  title?: string;
  height?: number;
  colors?: readonly string[];
  formatX?: (value: unknown) => string;
  formatY?: (value: number) => string;
  testID?: string;
};

export type PieChartProps<T extends Row> = {
  data: readonly T[] | null | undefined;
  label: Key<T>;
  value: Key<T>;
  title?: string;
  size?: number;
  colors?: readonly string[];
  format?: (value: number) => string;
  testID?: string;
};

const FONT = 11;
const CHAR = 6.4; // average glyph width at FONT px, for fitting labels
const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });
const defaultFormat = (n: number) => compact.format(n);

export const chartColors = (t: Theme) =>
  t.scheme === 'dark'
    ? [t.colors.tint, '#3fb950', '#d29922', '#db61a2', '#a371f7', '#39c5cf', '#f0883e', '#8b949e']
    : [t.colors.tint, '#2f9e44', '#e8590c', '#c2255c', '#7048e8', '#1098ad', '#b08800', '#6b7280'];

function useWidth(fallback = 320) {
  const [width, setWidth] = useState(fallback);
  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.round(e.nativeEvent.layout.width);
    if (w > 0 && w !== width) setWidth(w);
  };
  return [width, onLayout] as const;
}

const num = (v: unknown) => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN);
const truncate = (s: string, max: number) => (s.length <= max ? s : `${s.slice(0, Math.max(1, max - 1))}…`);

/** Round tick values covering [min, max]; `zero` stretches the range to include 0. */
export function niceTicks(min: number, max: number, zero: boolean, count = 4): number[] {
  if (zero) {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (min === max) {
    // Including 0 leaves only an all-zero series flat; otherwise pad a little either side.
    const pad = zero ? 1 : Math.abs(min) * 0.05 || 1;
    if (!zero) min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw)!;
  const lo = Math.floor(min / step + 1e-9) * step;
  const hi = Math.ceil(max / step - 1e-9) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function Frame({ label, testID, onLayout, legend, children, empty }: { label: string; testID?: string; onLayout?: (e: LayoutChangeEvent) => void; legend?: ReactNode; children: ReactNode; empty: boolean }) {
  const t = useTheme();
  return (
    <View testID={testID} role="img" aria-label={label} onLayout={onLayout} style={{ gap: t.spacing.sm }}>
      {legend}
      {empty ? (
        <Text testID={testID && `${testID}-empty`} style={{ color: t.colors.textMuted, fontSize: t.font.small, textAlign: 'center', paddingVertical: t.spacing.xl }}>
          No data yet
        </Text>
      ) : (
        children
      )}
    </View>
  );
}

function Legend({ items }: { items: { name: string; color: string }[] }) {
  const t = useTheme();
  if (items.length < 2) return null;
  return (
    <View aria-hidden style={{ flexDirection: 'row', flexWrap: 'wrap', gap: t.spacing.md }}>
      {items.map((it) => (
        <View key={it.name} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <View style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: it.color }} />
          <Text style={{ color: t.colors.textMuted, fontSize: t.font.small }}>{it.name}</Text>
        </View>
      ))}
    </View>
  );
}

type Prepared = {
  labels: string[];
  series: { key: string; name: string; color: string; values: number[] }[];
  min: number;
  max: number;
  any: boolean;
};

function prepare<T extends Row>(p: ChartProps<T>, palette: readonly string[]): Prepared {
  const rows = p.data ?? [];
  const keys = (typeof p.y === 'string' ? [p.y] : [...p.y]) as string[];
  const formatX = p.formatX ?? ((v: unknown) => (v === null || v === undefined ? '' : String(v)));
  const labels = rows.map((r) => formatX(get(r, p.x)));
  const series = keys.map((key, i) => ({
    key,
    name: (p.names as Record<string, string> | undefined)?.[key] ?? key,
    color: palette[i % palette.length],
    values: rows.map((r) => num(get(r, key))),
  }));
  const finite = series.flatMap((s) => s.values.filter(Number.isFinite));
  return { labels, series, min: finite.length ? Math.min(...finite) : 0, max: finite.length ? Math.max(...finite) : 0, any: finite.length > 0 };
}

function summarize(kind: string, title: string | undefined, d: Prepared, fmt: (n: number) => string) {
  const head = `${title ?? kind}: ${kind.toLowerCase()} of ${d.labels.length} point${d.labels.length === 1 ? '' : 's'}`;
  if (!d.any) return `${title ?? kind}: no data`;
  const parts = d.series.map((s) => {
    const pts = s.values.map((v, i) => [v, i] as const).filter(([v]) => Number.isFinite(v));
    if (!pts.length) return `${s.name}: no values`;
    const hi = pts.reduce((a, b) => (b[0] > a[0] ? b : a));
    const lo = pts.reduce((a, b) => (b[0] < a[0] ? b : a));
    const first = pts[0];
    const last = pts[pts.length - 1];
    if (pts.length === 1) return `${s.name}: ${fmt(first[0])} (${d.labels[first[1]]})`;
    return `${s.name}: from ${fmt(first[0])} (${d.labels[first[1]]}) to ${fmt(last[0])} (${d.labels[last[1]]}), highest ${fmt(hi[0])} (${d.labels[hi[1]]}), lowest ${fmt(lo[0])} (${d.labels[lo[1]]})`;
  });
  return `${head}. ${parts.join('. ')}.`;
}

/** The axes both cartesian charts share: y ticks with grid lines, and as many x labels as fit. */
function axesFor(width: number, height: number, d: Prepared, zero: boolean, fmt: (n: number) => string) {
  const ticks = niceTicks(d.min, d.max, zero);
  const tickText = ticks.map(fmt);
  const left = Math.ceil(Math.max(...tickText.map((s) => s.length)) * CHAR) + 8;
  const box = { left, right: width - 8, top: 8, bottom: height - 22 };
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const yAt = (v: number) => box.bottom - ((v - lo) / (hi - lo)) * (box.bottom - box.top);
  const n = d.labels.length;
  const plotW = box.right - box.left;
  const maxChars = 10;
  const labelW = Math.min(maxChars, Math.max(1, ...d.labels.map((l) => l.length))) * CHAR + 10;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / labelW))));
  return { box, ticks, tickText, yAt, every, maxChars, plotW };
}

function Axes({ axes, t, xs, labels }: { axes: ReturnType<typeof axesFor>; t: Theme; xs: number[]; labels: string[] }) {
  const { box, ticks, tickText, yAt, every, maxChars } = axes;
  const text = { fill: t.colors.textMuted, fontSize: FONT, fontFamily: 'system-ui, sans-serif' };
  return [
    ...ticks.map((v, i) =>
      h(
        'g',
        { key: `y${i}` },
        h('line', { x1: box.left, x2: box.right, y1: yAt(v), y2: yAt(v), stroke: t.colors.border, strokeWidth: v === 0 ? 1.5 : 1, strokeDasharray: v === 0 ? undefined : '2 3' }),
        h('text', { ...text, x: box.left - 6, y: yAt(v) + 4, textAnchor: 'end', 'data-axis': 'y' }, tickText[i]),
      ),
    ),
    ...labels.map((l, i) =>
      i % every === 0 || (i === labels.length - 1 && every === 1)
        ? h('text', { key: `x${i}`, ...text, x: xs[i], y: box.bottom + 15, textAnchor: 'middle', 'data-axis': 'x' }, truncate(l, maxChars))
        : null,
    ),
  ];
}

export function BarChart<T extends Row>(props: ChartProps<T>) {
  const t = useTheme();
  const [width, onLayout] = useWidth();
  const height = props.height ?? 200;
  const fmt = props.formatY ?? defaultFormat;
  const d = prepare(props, props.colors ?? chartColors(t));
  const label = summarize('Bar chart', props.title, d, fmt);
  const legend = <Legend items={d.series} />;
  if (!d.any) return <Frame label={label} testID={props.testID} onLayout={onLayout} legend={legend} empty>{null}</Frame>;
  const axes = axesFor(width, height, d, true, fmt);
  const n = d.labels.length;
  const band = axes.plotW / n;
  const groupW = band * 0.75;
  const barW = groupW / d.series.length;
  const xs = d.labels.map((_, i) => axes.box.left + band * (i + 0.5));
  const y0 = axes.yAt(0);
  const bars = d.series.flatMap((s, si) =>
    s.values.map((v, i) => {
      if (!Number.isFinite(v)) return null;
      const y = axes.yAt(v);
      return h('rect', {
        key: `${si}-${i}`,
        'data-bar': `${s.key}:${i}`,
        x: xs[i] - groupW / 2 + si * barW + 0.5,
        width: Math.max(1, barW - 1),
        y: Math.min(y, y0),
        height: Math.max(v === 0 ? 0 : 1, Math.abs(y0 - y)),
        rx: Math.min(3, barW / 4),
        fill: s.color,
      });
    }),
  );
  return (
    <Frame label={label} testID={props.testID} onLayout={onLayout} legend={legend} empty={false}>
      {h('svg', { width, height, 'aria-hidden': true, style: { display: 'block', overflow: 'visible' } }, ...Axes({ axes, t, xs, labels: d.labels }), ...bars)}
    </Frame>
  );
}

export function LineChart<T extends Row>(props: ChartProps<T>) {
  const t = useTheme();
  const [width, onLayout] = useWidth();
  const height = props.height ?? 200;
  const fmt = props.formatY ?? defaultFormat;
  const d = prepare(props, props.colors ?? chartColors(t));
  const label = summarize('Line chart', props.title, d, fmt);
  const legend = <Legend items={d.series} />;
  if (!d.any) return <Frame label={label} testID={props.testID} onLayout={onLayout} legend={legend} empty>{null}</Frame>;
  const axes = axesFor(width, height, d, false, fmt);
  const n = d.labels.length;
  const inset = Math.min(12, axes.plotW / 4);
  const xs = d.labels.map((_, i) => (n === 1 ? axes.box.left + axes.plotW / 2 : axes.box.left + inset + (i * (axes.plotW - 2 * inset)) / (n - 1)));
  const dots = n <= 31;
  const lines = d.series.flatMap((s, si) => {
    // A missing value breaks the line rather than joining across it.
    let path = '';
    let pen = false;
    s.values.forEach((v, i) => {
      if (!Number.isFinite(v)) return void (pen = false);
      path += `${pen ? 'L' : 'M'}${xs[i].toFixed(1)},${axes.yAt(v).toFixed(1)}`;
      pen = true;
    });
    return [
      h('path', { key: `l${si}`, 'data-line': s.key, d: path, fill: 'none', stroke: s.color, strokeWidth: 2, strokeLinejoin: 'round', strokeLinecap: 'round' }),
      ...(dots || n === 1
        ? s.values.map((v, i) => (Number.isFinite(v) ? h('circle', { key: `d${si}-${i}`, 'data-point': `${s.key}:${i}`, cx: xs[i], cy: axes.yAt(v), r: 3, fill: s.color }) : null))
        : []),
    ];
  });
  return (
    <Frame label={label} testID={props.testID} onLayout={onLayout} legend={legend} empty={false}>
      {h('svg', { width, height, 'aria-hidden': true, style: { display: 'block', overflow: 'visible' } }, ...Axes({ axes, t, xs, labels: d.labels }), ...lines)}
    </Frame>
  );
}

export function PieChart<T extends Row>({ data, label, value, title, size = 160, colors, format = defaultFormat, testID }: PieChartProps<T>) {
  const t = useTheme();
  const palette = colors ?? chartColors(t);
  const slices = (data ?? [])
    .map((r) => ({ name: String(get(r, label) ?? ''), v: num(get(r, value)) }))
    .filter((s) => Number.isFinite(s.v) && s.v > 0)
    .map((s, i) => ({ ...s, color: palette[i % palette.length] }));
  const total = slices.reduce((a, s) => a + s.v, 0);
  const pct = (v: number) => `${Math.round((v / total) * 100)}%`;
  const summary = slices.length
    ? `${title ?? 'Pie chart'}: ${slices.map((s) => `${s.name} ${pct(s.v)} (${format(s.v)})`).join(', ')}.`
    : `${title ?? 'Pie chart'}: no data`;
  if (!slices.length) return <Frame label={summary} testID={testID} empty>{null}</Frame>;
  const r = size / 2;
  const inner = r * 0.58;
  let angle = -Math.PI / 2;
  const at = (a: number, rad: number) => `${(r + rad * Math.cos(a)).toFixed(2)},${(r + rad * Math.sin(a)).toFixed(2)}`;
  const paths = slices.map((s, i) => {
    if (slices.length === 1) {
      return h('circle', { key: i, 'data-slice': s.name, cx: r, cy: r, r: (r + inner) / 2, fill: 'none', stroke: s.color, strokeWidth: r - inner });
    }
    const a0 = angle;
    const a1 = (angle += (s.v / total) * Math.PI * 2);
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const d = `M${at(a0, r)}A${r},${r} 0 ${large} 1 ${at(a1, r)}L${at(a1, inner)}A${inner},${inner} 0 ${large} 0 ${at(a0, inner)}Z`;
    return h('path', { key: i, 'data-slice': s.name, d, fill: s.color, stroke: t.colors.background, strokeWidth: 1 });
  });
  return (
    <Frame label={summary} testID={testID} empty={false}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.lg, flexWrap: 'wrap' }}>
        {h('svg', { width: size, height: size, 'aria-hidden': true, style: { display: 'block' } }, ...paths)}
        <View aria-hidden style={{ flex: 1, minWidth: 120, gap: t.spacing.xs }}>
          {slices.map((s, i) => (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: s.color }} />
              <Text style={{ flex: 1, color: t.colors.text, fontSize: t.font.small }} numberOfLines={1}>
                {s.name}
              </Text>
              <Text style={{ color: t.colors.textMuted, fontSize: t.font.small, fontVariant: ['tabular-nums'] }}>{pct(s.v)}</Text>
            </View>
          ))}
        </View>
      </View>
    </Frame>
  );
}
