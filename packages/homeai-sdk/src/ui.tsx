// @homeai/ui (M22-03): a small kit of phone-app components over react-native,
// drawn with the theme (theme.ts). The sandbox always renders with
// react-native-web, so a few inputs are the browser's own (date, time,
// select); app code only ever sees these components.
import { createElement, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ActivityIndicator,
  Animated,
  Image,
  PanResponder,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { Ionicons } from './icons';
import { pickImage, useImage } from './sdk';
import { useTheme, type Theme } from './theme';

type Style = StyleProp<ViewStyle>;

export function Icon({ name, size = 22, color, style, testID }: { name: string; size?: number; color?: string; style?: StyleProp<TextStyle>; testID?: string }) {
  const { colors } = useTheme();
  return <Ionicons name={name} size={size} color={color ?? colors.text} style={style} testID={testID} />;
}

// --- layout -----------------------------------------------------------------

export function Screen({ children, scroll = true, padded = true, style, testID }: { children?: ReactNode; scroll?: boolean; padded?: boolean; style?: Style; testID?: string }) {
  const t = useTheme();
  const inner = [{ gap: t.spacing.lg }, padded && { padding: t.spacing.lg }, style];
  if (!scroll) return <View testID={testID} style={[{ flex: 1, backgroundColor: t.colors.background }, inner]}>{children}</View>;
  return (
    <ScrollView testID={testID} style={{ flex: 1, backgroundColor: t.colors.background }} contentContainerStyle={inner} keyboardShouldPersistTaps="handled">
      {children}
    </ScrollView>
  );
}

export function Section({ title, footer, children, testID }: { title?: string; footer?: string; children?: ReactNode; testID?: string }) {
  const t = useTheme();
  return (
    <View testID={testID} style={{ gap: t.spacing.xs + 2 }}>
      {title ? <Text role="heading" style={[s.sectionTitle, { color: t.colors.textMuted, fontSize: t.font.small }]}>{title.toUpperCase()}</Text> : null}
      {children}
      {footer ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.small, paddingHorizontal: t.spacing.xs }}>{footer}</Text> : null}
    </View>
  );
}

const cardStyle = (t: Theme) => ({ backgroundColor: t.colors.surface, borderRadius: t.radius.md, borderWidth: StyleSheet.hairlineWidth, borderColor: t.colors.border });

export function Card({ children, onPress, style, testID }: { children?: ReactNode; onPress?: () => void; style?: Style; testID?: string }) {
  const t = useTheme();
  const body = [cardStyle(t), { padding: t.spacing.lg, gap: t.spacing.sm }, style];
  if (!onPress) return <View testID={testID} style={body}>{children}</View>;
  return (
    <Pressable testID={testID} role="button" onPress={onPress} style={({ pressed }) => [body, pressed && { opacity: 0.7 }]}>
      {children}
    </Pressable>
  );
}

/** Rows in one card with separators: `data` + `renderItem`, or children. `empty` shows when there are none. */
export function List<T>({ data, renderItem, keyExtractor, children, empty, style, testID }: {
  data?: readonly T[] | null;
  renderItem?: (item: T, index: number) => ReactNode;
  keyExtractor?: (item: T, index: number) => string | number;
  children?: ReactNode;
  empty?: ReactNode;
  style?: Style;
  testID?: string;
}) {
  const t = useTheme();
  const items: ReactNode[] = data && renderItem ? data.map((d, i) => <View key={keyExtractor ? keyExtractor(d, i) : i}>{renderItem(d, i)}</View>) : ([] as ReactNode[]).concat(children ?? []).filter((c) => c !== null && c !== false && c !== undefined);
  if (!items.length) return empty ? <>{empty}</> : null;
  return (
    <View testID={testID} role="list" style={[cardStyle(t), { overflow: 'hidden' }, style]}>
      {items.map((item, i) => (
        <View key={i} style={i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderColor: t.colors.border }}>
          {item}
        </View>
      ))}
    </View>
  );
}

export type RowAction = { label: string; icon?: string; color?: string; onPress: () => void };

const ACTION_WIDTH = 76;

/** A list row. `actions` are revealed by swiping left, or by a long press. */
export function ListRow({ title, subtitle, value, icon, iconColor, left, right, chevron, onPress, onLongPress, actions, testID }: {
  title: string;
  subtitle?: string;
  value?: string | number;
  icon?: string;
  iconColor?: string;
  left?: ReactNode;
  right?: ReactNode;
  chevron?: boolean;
  onPress?: () => void;
  onLongPress?: () => void;
  actions?: RowAction[];
  testID?: string;
}) {
  const t = useTheme();
  const width = (actions?.length ?? 0) * ACTION_WIDTH;
  const x = useRef(new Animated.Value(0)).current;
  const [open, setOpen] = useState(false);
  const settle = (next: boolean) => {
    setOpen(next);
    Animated.timing(x, { toValue: next ? -width : 0, duration: 150, useNativeDriver: false }).start();
  };
  // The pan handlers are made once, so they read the latest render through `live`.
  const live = useRef({ open, width, settle, dragEnd: 0 });
  Object.assign(live.current, { open, width, settle });
  const pan = useRef(
    PanResponder.create({
      // Capture, so a horizontal drag takes over from the row's Pressable.
      onMoveShouldSetPanResponderCapture: (_e, g) => live.current.width > 0 && Math.abs(g.dx) > 8 && Math.abs(g.dx) > Math.abs(g.dy),
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: (_e, g) => {
        const { open, width } = live.current;
        x.setValue(Math.max(-width, Math.min(0, g.dx + (open ? -width : 0))));
      },
      onPanResponderRelease: (_e, g) => {
        const { open, width, settle } = live.current;
        live.current.dragEnd = Date.now();
        settle(g.dx + (open ? -width : 0) < -width / 2);
      },
    }),
  ).current;
  const row = (
    <Pressable
      testID={testID}
      onPress={() => {
        // A mouse drag ends with a click on the row; that isn't a tap.
        if (Date.now() - live.current.dragEnd < 300) return;
        if (open) settle(false);
        else onPress?.();
      }}
      onLongPress={width ? () => settle(!open) : onLongPress}
      disabled={!onPress && !onLongPress && !width}
      style={({ pressed }) => [s.row, { gap: t.spacing.md, paddingHorizontal: t.spacing.lg, backgroundColor: t.colors.surface }, pressed && (onPress || width) ? { opacity: 0.7 } : null]}
    >
      {left ?? (icon ? <Icon name={icon} color={iconColor ?? t.colors.tint} /> : null)}
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={{ color: t.colors.text, fontSize: t.font.body }} numberOfLines={1}>{title}</Text>
        {subtitle ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.small }} numberOfLines={2}>{subtitle}</Text> : null}
      </View>
      {value !== undefined ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.body }}>{String(value)}</Text> : null}
      {right}
      {(chevron ?? !!onPress) ? <Icon name="chevron-forward-outline" size={18} color={t.colors.textMuted} /> : null}
    </Pressable>
  );
  if (!width) return row;
  return (
    <View style={{ overflow: 'hidden' }}>
      <View style={[StyleSheet.absoluteFill, { flexDirection: 'row', justifyContent: 'flex-end' }]}>
        {actions!.map((a, i) => (
          <Pressable
            key={i}
            testID={testID ? `${testID}-action-${i}` : undefined}
            role="button"
            accessibilityLabel={a.label}
            aria-hidden={!open}
            onPress={() => {
              settle(false);
              a.onPress();
            }}
            style={[s.action, { width: ACTION_WIDTH, backgroundColor: a.color ?? (i === actions!.length - 1 ? t.colors.danger : t.colors.inactive) }]}
          >
            {a.icon ? <Icon name={a.icon} size={20} color="#fff" /> : null}
            <Text style={{ color: '#fff', fontSize: t.font.small, fontWeight: '600' }}>{a.label}</Text>
          </Pressable>
        ))}
      </View>
      <Animated.View style={{ transform: [{ translateX: x }] }} {...pan.panHandlers}>
        {row}
      </Animated.View>
    </View>
  );
}

export function EmptyState({ icon = 'albums-outline', title, message, action, testID }: { icon?: string; title: string; message?: string; action?: { title: string; onPress: () => void }; testID?: string }) {
  const t = useTheme();
  return (
    <View testID={testID} style={{ alignItems: 'center', gap: t.spacing.sm, padding: t.spacing.xl }}>
      <Icon name={icon} size={44} color={t.colors.textMuted} />
      <Text style={{ color: t.colors.text, fontSize: t.font.title, fontWeight: '600', textAlign: 'center' }}>{title}</Text>
      {message ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.body, textAlign: 'center' }}>{message}</Text> : null}
      {action ? <Button title={action.title} onPress={action.onPress} variant="secondary" /> : null}
    </View>
  );
}

// --- inputs -----------------------------------------------------------------

function Field({ label, error, hint, children }: { label?: string; error?: string | null; hint?: string; children: ReactNode }) {
  const t = useTheme();
  return (
    <View style={{ gap: t.spacing.xs }}>
      {label ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.small, fontWeight: '600' }}>{label}</Text> : null}
      {children}
      {error ? <Text role="alert" style={{ color: t.colors.danger, fontSize: t.font.small }}>{error}</Text> : hint ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.small }}>{hint}</Text> : null}
    </View>
  );
}

const inputStyle = (t: Theme, error?: unknown) => ({
  borderWidth: 1,
  borderColor: error ? t.colors.danger : t.colors.border,
  borderRadius: t.radius.sm,
  paddingHorizontal: t.spacing.md,
  paddingVertical: t.spacing.sm + 2,
  fontSize: t.font.body,
  color: t.colors.text,
  backgroundColor: t.colors.background,
});

export function TextField({ label, value, onChangeText, placeholder, multiline, error, hint, autoFocus, secureTextEntry, keyboardType, onSubmitEditing, editable = true, testID }: {
  label?: string;
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  multiline?: boolean;
  error?: string | null;
  hint?: string;
  autoFocus?: boolean;
  secureTextEntry?: boolean;
  keyboardType?: 'default' | 'email-address' | 'numeric' | 'decimal-pad' | 'phone-pad' | 'url';
  onSubmitEditing?: () => void;
  editable?: boolean;
  testID?: string;
}) {
  const t = useTheme();
  return (
    <Field label={label} error={error} hint={hint}>
      <TextInput
        testID={testID}
        aria-label={label}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={t.colors.textMuted}
        multiline={multiline}
        numberOfLines={multiline ? 4 : undefined}
        autoFocus={autoFocus}
        secureTextEntry={secureTextEntry}
        keyboardType={keyboardType}
        onSubmitEditing={onSubmitEditing}
        editable={editable}
        style={[inputStyle(t, error), multiline && { minHeight: 96, textAlignVertical: 'top' }, !editable && { opacity: 0.6 }]}
      />
    </Field>
  );
}

const formatNumber = (n: number | null, decimals?: number) => (n === null ? '' : decimals === undefined ? String(n) : n.toFixed(decimals));

/** A number or null (empty). Typing keeps what you typed; the value is clamped to min/max and rounded on blur. */
export function NumberField({ label, value, onChange, min, max, decimals, placeholder, error, hint, testID }: {
  label?: string;
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  decimals?: number;
  placeholder?: string;
  error?: string | null;
  hint?: string;
  testID?: string;
}) {
  const t = useTheme();
  const [text, setText] = useState(formatNumber(value, decimals));
  const last = useRef(value);
  useEffect(() => {
    if (value !== last.current) setText(formatNumber(value, decimals));
    last.current = value;
  }, [value, decimals]);
  const parse = (s: string) => {
    const n = Number(s.replace(',', '.'));
    return s.trim() === '' || !Number.isFinite(n) ? null : n;
  };
  const tidy = (n: number | null) => {
    if (n === null) return null;
    let v = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
    if (decimals !== undefined) v = Number(v.toFixed(decimals));
    return v;
  };
  return (
    <Field label={label} error={error} hint={hint}>
      <TextInput
        testID={testID}
        aria-label={label}
        value={text}
        inputMode={decimals === 0 ? 'numeric' : 'decimal'}
        placeholder={placeholder}
        placeholderTextColor={t.colors.textMuted}
        onChangeText={(s) => {
          setText(s);
          const n = parse(s);
          if (n !== null || s.trim() === '') {
            last.current = n;
            onChange(n);
          }
        }}
        onBlur={() => {
          const v = tidy(parse(text));
          setText(formatNumber(v, decimals));
          last.current = v;
          if (v !== value) onChange(v);
        }}
        style={inputStyle(t, error)}
      />
    </Field>
  );
}

function domInput(t: Theme, error: unknown) {
  const i = inputStyle(t, error);
  return {
    font: 'inherit',
    fontSize: i.fontSize,
    color: i.color,
    background: i.backgroundColor,
    border: `1px solid ${i.borderColor}`,
    borderRadius: i.borderRadius,
    padding: `${i.paddingVertical}px ${i.paddingHorizontal}px`,
    colorScheme: t.scheme,
    minHeight: 40,
    boxSizing: 'border-box' as const,
    width: '100%',
  };
}

function NativeInput({ type, label, value, onChange, min, max, error, hint, testID }: {
  type: 'date' | 'time';
  label?: string;
  value: string | null;
  onChange: (v: string | null) => void;
  min?: string;
  max?: string;
  error?: string | null;
  hint?: string;
  testID?: string;
}) {
  const t = useTheme();
  return (
    <Field label={label} error={error} hint={hint}>
      {createElement('input', {
        type,
        'data-testid': testID,
        'aria-label': label,
        value: value ?? '',
        min,
        max,
        onChange: (e: { target: { value: string } }) => onChange(e.target.value || null),
        style: domInput(t, error),
      })}
    </Field>
  );
}

/** A date as `YYYY-MM-DD` (or null), with the browser's date picker. */
export function DateField(props: { label?: string; value: string | null; onChange: (date: string | null) => void; min?: string; max?: string; error?: string | null; hint?: string; testID?: string }) {
  return <NativeInput type="date" {...props} />;
}

/** A time as `HH:MM` (or null), with the browser's time picker. */
export function TimeField(props: { label?: string; value: string | null; onChange: (time: string | null) => void; error?: string | null; hint?: string; testID?: string }) {
  return <NativeInput type="time" {...props} />;
}

export function Select<V extends string | number>({ label, value, options, onChange, placeholder, error, hint, testID }: {
  label?: string;
  value: V | null;
  options: readonly { label: string; value: V }[];
  onChange: (value: V) => void;
  placeholder?: string;
  error?: string | null;
  hint?: string;
  testID?: string;
}) {
  const t = useTheme();
  const index = options.findIndex((o) => o.value === value);
  return (
    <Field label={label} error={error} hint={hint}>
      {createElement(
        'select',
        {
          'data-testid': testID,
          'aria-label': label,
          value: index < 0 ? '' : String(index),
          onChange: (e: { target: { value: string } }) => {
            const o = options[Number(e.target.value)];
            if (o) onChange(o.value);
          },
          style: domInput(t, error),
        },
        index < 0 ? createElement('option', { value: '', disabled: true }, placeholder ?? 'Choose…') : null,
        ...options.map((o, i) => createElement('option', { key: i, value: String(i) }, o.label)),
      )}
    </Field>
  );
}

export function Toggle({ label, description, value, onValueChange, disabled, testID }: { label: string; description?: string; value: boolean; onValueChange: (value: boolean) => void; disabled?: boolean; testID?: string }) {
  const t = useTheme();
  return (
    <View style={[s.row, { gap: t.spacing.md }]}>
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={{ color: t.colors.text, fontSize: t.font.body }}>{label}</Text>
        {description ? <Text style={{ color: t.colors.textMuted, fontSize: t.font.small }}>{description}</Text> : null}
      </View>
      <Switch testID={testID} aria-label={label} value={value} onValueChange={onValueChange} disabled={disabled} trackColor={{ false: t.colors.border, true: t.colors.tint }} />
    </View>
  );
}

export function Stepper({ label, value, onChange, min = -Infinity, max = Infinity, step = 1, testID }: { label?: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; testID?: string }) {
  const t = useTheme();
  const set = (v: number) => onChange(Math.min(max, Math.max(min, Number(v.toFixed(6)))));
  return (
    <View style={[s.row, { gap: t.spacing.md }]}>
      {label ? <Text style={{ flex: 1, color: t.colors.text, fontSize: t.font.body }}>{label}</Text> : null}
      <IconButton icon="remove-outline" accessibilityLabel={`Decrease${label ? ` ${label}` : ''}`} onPress={() => set(value - step)} disabled={value - step < min} testID={testID && `${testID}-minus`} />
      <Text testID={testID} style={{ color: t.colors.text, fontSize: t.font.body, minWidth: 32, textAlign: 'center', fontVariant: ['tabular-nums'] }}>{value}</Text>
      <IconButton icon="add-outline" accessibilityLabel={`Increase${label ? ` ${label}` : ''}`} onPress={() => set(value + step)} disabled={value + step > max} testID={testID && `${testID}-plus`} />
    </View>
  );
}

export function SearchField({ value, onChangeText, placeholder = 'Search', testID }: { value: string; onChangeText: (text: string) => void; placeholder?: string; testID?: string }) {
  const t = useTheme();
  return (
    <View style={[s.row, { gap: t.spacing.sm, paddingHorizontal: t.spacing.md, borderRadius: t.radius.md, backgroundColor: t.colors.surface }]}>
      <Icon name="search-outline" size={18} color={t.colors.textMuted} />
      <TextInput
        testID={testID}
        role="searchbox"
        aria-label={placeholder}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={t.colors.textMuted}
        style={{ flex: 1, paddingVertical: t.spacing.sm + 2, fontSize: t.font.body, color: t.colors.text }}
      />
      {value ? <IconButton icon="close-circle-outline" accessibilityLabel="Clear search" size={18} color={t.colors.textMuted} onPress={() => onChangeText('')} testID={testID && `${testID}-clear`} /> : null}
    </View>
  );
}

// --- photos (#468) ----------------------------------------------------------

/** A stored photo (`pickImage`'s id), cropped to a square; `icon` stands in without one. */
export function Photo({ id, size = 64, round, icon = 'image-outline', style, testID }: { id: string | null | undefined; size?: number; round?: boolean; icon?: string; style?: Style; testID?: string }) {
  const t = useTheme();
  const { uri, loading } = useImage(id);
  return (
    <View
      testID={testID}
      style={[{ width: size, height: size, borderRadius: round ? size / 2 : t.radius.md, backgroundColor: t.colors.surface, overflow: 'hidden', alignItems: 'center', justifyContent: 'center' }, style]}
    >
      {uri ? (
        <Image testID={testID && `${testID}-image`} source={{ uri }} resizeMode="cover" style={{ width: size, height: size }} />
      ) : loading ? (
        <ActivityIndicator size="small" color={t.colors.textMuted} />
      ) : (
        <Icon name={icon} size={Math.round(size * 0.45)} color={t.colors.textMuted} />
      )}
    </View>
  );
}

/**
 * A photo input: take or choose one, or remove it. `value` is an image id. Removing only
 * clears the field; `deleteImage` the ids you drop when you save.
 */
export function PhotoField({ label, value, onChange, round, size = 88, icon, hint, disabled, testID }: {
  label?: string;
  value: string | null;
  onChange: (id: string | null) => void;
  round?: boolean;
  size?: number;
  icon?: string;
  hint?: string;
  disabled?: boolean;
  testID?: string;
}) {
  const t = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pick = async (source: 'camera' | 'library') => {
    setBusy(true);
    setError(null);
    try {
      const picked = await pickImage(source);
      if (picked) onChange(picked.id);
    } catch (err: any) {
      setError(String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };
  const id = testID ?? 'photo';
  return (
    <Field label={label} error={error} hint={hint}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: t.spacing.md }}>
        <Photo id={value} size={size} round={round} icon={icon} testID={`${id}-preview`} />
        <View style={{ flex: 1, gap: t.spacing.xs, alignItems: 'flex-start' }}>
          <Button variant="plain" icon="camera-outline" title="Take photo" disabled={disabled || busy} onPress={() => void pick('camera')} testID={`${id}-camera`} />
          <Button variant="plain" icon="images-outline" title={value ? 'Choose another' : 'Choose photo'} disabled={disabled || busy} onPress={() => void pick('library')} testID={`${id}-library`} />
          {value ? <Button variant="plain" icon="trash-outline" title="Remove" disabled={disabled || busy} onPress={() => onChange(null)} testID={`${id}-remove`} /> : null}
        </View>
      </View>
    </Field>
  );
}

export function Button({ title, onPress, variant = 'primary', icon, disabled, loading, style, testID }: {
  title: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'destructive' | 'plain';
  icon?: string;
  disabled?: boolean;
  loading?: boolean;
  style?: Style;
  testID?: string;
}) {
  const t = useTheme();
  const solid = variant === 'primary' || variant === 'destructive';
  const bg = variant === 'primary' ? t.colors.tint : variant === 'destructive' ? t.colors.danger : variant === 'secondary' ? t.colors.surface : 'transparent';
  const fg = solid ? t.colors.onTint : t.colors.tint;
  return (
    <Pressable
      testID={testID}
      role="button"
      aria-disabled={disabled || loading}
      disabled={disabled || loading}
      onPress={onPress}
      style={({ pressed }) => [
        s.button,
        { gap: t.spacing.sm, backgroundColor: bg, borderRadius: t.radius.sm, paddingHorizontal: t.spacing.lg },
        variant === 'secondary' && { borderWidth: 1, borderColor: t.colors.border },
        (pressed || disabled) && { opacity: disabled ? 0.45 : 0.75 },
        style,
      ]}
    >
      {loading ? <ActivityIndicator size="small" color={fg} /> : icon ? <Icon name={icon} size={18} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: t.font.body, fontWeight: '600' }}>{title}</Text>
    </Pressable>
  );
}

export function IconButton({ icon, onPress, accessibilityLabel, color, size = 22, disabled, testID }: { icon: string; onPress: () => void; accessibilityLabel: string; color?: string; size?: number; disabled?: boolean; testID?: string }) {
  const t = useTheme();
  return (
    <Pressable
      testID={testID}
      role="button"
      accessibilityLabel={accessibilityLabel}
      aria-disabled={disabled}
      disabled={disabled}
      onPress={onPress}
      hitSlop={8}
      style={({ pressed }) => [{ padding: 4, opacity: disabled ? 0.35 : pressed ? 0.6 : 1 }]}
    >
      <Icon name={icon} size={size} color={color ?? t.colors.tint} />
    </Pressable>
  );
}

export function Chip({ label, selected, onPress, icon, testID }: { label: string; selected?: boolean; onPress?: () => void; icon?: string; testID?: string }) {
  const t = useTheme();
  const fg = selected ? t.colors.onTint : t.colors.text;
  return (
    <Pressable
      testID={testID}
      role={onPress ? 'button' : undefined}
      aria-pressed={onPress ? !!selected : undefined}
      disabled={!onPress}
      onPress={onPress}
      style={[s.chip, { gap: t.spacing.xs, borderColor: selected ? t.colors.tint : t.colors.border, backgroundColor: selected ? t.colors.tint : t.colors.surface }]}
    >
      {icon ? <Icon name={icon} size={14} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: t.font.small, fontWeight: '500' }}>{label}</Text>
    </Pressable>
  );
}

// --- display ----------------------------------------------------------------

export function ProgressBar({ value, color, testID }: { value: number; color?: string; testID?: string }) {
  const t = useTheme();
  const v = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return (
    <View testID={testID} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)} style={{ height: 6, borderRadius: 3, backgroundColor: t.colors.border, overflow: 'hidden' }}>
      <View style={{ width: `${v * 100}%`, height: '100%', backgroundColor: color ?? t.colors.tint }} />
    </View>
  );
}

export function Badge({ value, color, testID }: { value: string | number; color?: string; testID?: string }) {
  const t = useTheme();
  return (
    <View testID={testID} style={[s.badge, { backgroundColor: color ?? t.colors.danger }]}>
      <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{String(value)}</Text>
    </View>
  );
}

export function formatMoney(amount: number, currency = 'USD', { signed = false }: { signed?: boolean } = {}) {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency, signDisplay: signed ? 'exceptZero' : 'auto' }).format(amount);
}

export function Money({ amount, currency, signed, colored, style, testID }: { amount: number; currency?: string; signed?: boolean; colored?: boolean; style?: StyleProp<TextStyle>; testID?: string }) {
  const t = useTheme();
  const color = colored && amount !== 0 ? (amount < 0 ? t.colors.danger : t.colors.success) : t.colors.text;
  return <Text testID={testID} style={[{ color, fontVariant: ['tabular-nums'] }, style]}>{formatMoney(amount, currency, { signed })}</Text>;
}

/** 45 → "45s", 750 → "12m 30s", 3900 → "1h 05m". */
export function formatDuration(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return sec ? `${m}m ${String(sec).padStart(2, '0')}s` : `${m}m`;
  return `${sec}s`;
}

export function Duration({ seconds, style, testID }: { seconds: number; style?: StyleProp<TextStyle>; testID?: string }) {
  const t = useTheme();
  return <Text testID={testID} style={[{ color: t.colors.text, fontVariant: ['tabular-nums'] }, style]}>{formatDuration(seconds)}</Text>;
}

const s = StyleSheet.create({
  sectionTitle: { fontWeight: '600', letterSpacing: 0.5, paddingHorizontal: 4 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 48, paddingVertical: 8 },
  action: { alignItems: 'center', justifyContent: 'center', gap: 2 },
  button: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', minHeight: 44 },
  chip: { flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5 },
  badge: { minWidth: 20, height: 20, paddingHorizontal: 6, borderRadius: 10, alignItems: 'center', justifyContent: 'center', alignSelf: 'flex-start' },
});
