// Typings app code is checked against for the modules the runtime provides
// besides react / react-native (docs/PLATFORM.md §7 "Allowed imports").
// They describe exactly what src/runtime.tsx registers: anything missing
// here is a type error in the app, which is how unsupported expo-router or
// SDK features are reported.

declare module '@homeai/sdk' {
  export type SQLValue = string | number | boolean | null;
  /** `?` placeholders take a list; `:name` / `$name` / `@name` take an object (the prefix is optional in its keys). */
  export type SQLParams = SQLValue[] | Record<string, SQLValue>;
  export type RunResult = { changes: number; lastInsertRowId: number };
  export type ActionResult = RunResult & { rows: Record<string, unknown>[] };

  /**
   * expo-sqlite-shaped access to this app instance's database. Params go as
   * a list, an object, or one by one: `runAsync(sql, [a, b])`,
   * `runAsync(sql, { $a: a })`, `runAsync(sql, a, b)`. One statement per call;
   * multi-statement writes are actions (`runAction`).
   */
  export interface Database {
    getAllAsync<T = any>(sql: string, params?: SQLParams): Promise<T[]>;
    getAllAsync<T = any>(sql: string, ...params: SQLValue[]): Promise<T[]>;
    getFirstAsync<T = any>(sql: string, params?: SQLParams): Promise<T | null>;
    getFirstAsync<T = any>(sql: string, ...params: SQLValue[]): Promise<T | null>;
    runAsync(sql: string, params?: SQLParams): Promise<RunResult>;
    runAsync(sql: string, ...params: SQLValue[]): Promise<RunResult>;
  }

  /** What SDK calls reject with: `code` is the platform's (`sql_error`, `sql_not_allowed`, …) or the host's (`read_only`, `timeout`, …). */
  export type SDKError = Error & { code: string };

  export type QueryResult<T> = {
    data: T[] | undefined;
    error: Error | null;
    loading: boolean;
    refresh: () => void;
  };

  export type Space = { id: string; slug: string; name: string; role: 'owner' | 'editor' | 'viewer' };
  /** A person. `id` is what the platform stamps in every table's `_created_by` / `_updated_by`. */
  export type User = { id: string; username: string; name: string };
  export type Member = User & { role: 'owner' | 'editor' | 'viewer' };

  export function useDatabase(): Database;
  /** Alias of `useDatabase` (expo-sqlite's hook name). */
  export function useSQLiteContext(): Database;
  /** Runs `sql` and re-runs it whenever the instance's database changes. */
  export function useQuery<T = any>(sql: string, params?: SQLParams): QueryResult<T>;
  export function useQuery<T = any>(sql: string, ...params: SQLValue[]): QueryResult<T>;
  /** Runs the named action from actions/<name>.sql in one transaction; `rows` are the last result set's. */
  export function runAction(name: string, params?: Record<string, SQLValue>): Promise<ActionResult>;
  /** Opens the host's "Ask the agent" panel with this prompt (a new thread pre-seeded with this app's context). */
  export function askAgent(prompt: string): Promise<void>;
  /** The space this instance is installed in; null until the host has said. */
  export function useSpace(): Space | null;
  /** Who is using the app; null until the host has said. */
  export function useUser(): User | null;
  /** The space's members, to show names for `_created_by` / `_updated_by`. */
  export function useMembers(): Member[];
  /** The member with this user id (e.g. `useMember(row._created_by)`), or undefined. */
  export function useMember(id: string | null | undefined): Member | undefined;
}

declare module '@homeai/ui' {
  import type { ReactElement, ReactNode } from 'react';
  import type { StyleProp, TextStyle, ViewStyle } from 'react-native';
  import type { IconName } from '@expo/vector-icons';

  /** The app's theme: light unless app.json says `"userInterfaceStyle": "automatic"` (follow the host) or `"dark"`. */
  export type Theme = {
    scheme: 'light' | 'dark';
    colors: {
      background: string; surface: string; text: string; textMuted: string; border: string;
      tint: string; onTint: string; danger: string; success: string; warning: string; inactive: string;
    };
    spacing: { xs: number; sm: number; md: number; lg: number; xl: number };
    radius: { sm: number; md: number; lg: number };
    font: { small: number; body: number; title: number; large: number };
  };
  export function useTheme(): Theme;

  type El = ReactElement | null;
  type Style = StyleProp<ViewStyle>;

  export function Icon(props: { name: IconName; size?: number; color?: string; style?: StyleProp<TextStyle>; testID?: string }): El;

  /** A screen's body: themed background, padding and spacing between children; scrolls unless scroll={false}. */
  export function Screen(props: { children?: ReactNode; scroll?: boolean; padded?: boolean; style?: Style; testID?: string }): El;
  /** A titled group: put a List, Card or fields inside. */
  export function Section(props: { title?: string; footer?: string; children?: ReactNode; testID?: string }): El;
  export function Card(props: { children?: ReactNode; onPress?: () => void; style?: Style; testID?: string }): El;
  /** Rows in one card with separators: data + renderItem, or ListRow children. `empty` shows when there are none. */
  export function List<T>(props: {
    data?: readonly T[] | null;
    renderItem?: (item: T, index: number) => ReactNode;
    keyExtractor?: (item: T, index: number) => string | number;
    children?: ReactNode;
    empty?: ReactNode;
    style?: Style;
    testID?: string;
  }): El;
  export type RowAction = { label: string; icon?: IconName; color?: string; onPress: () => void };
  /** A row: title, subtitle, value on the right, icon on the left, chevron when pressable. `actions` show on swipe left or long press. */
  export function ListRow(props: {
    title: string;
    subtitle?: string;
    value?: string | number;
    icon?: IconName;
    iconColor?: string;
    left?: ReactNode;
    right?: ReactNode;
    chevron?: boolean;
    onPress?: () => void;
    onLongPress?: () => void;
    actions?: RowAction[];
    testID?: string;
  }): El;
  export function EmptyState(props: { icon?: IconName; title: string; message?: string; action?: { title: string; onPress: () => void }; testID?: string }): El;

  export function TextField(props: {
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
  }): El;
  /** A number, or null when empty. Clamped to min/max and rounded to `decimals` on blur. */
  export function NumberField(props: { label?: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number; decimals?: number; placeholder?: string; error?: string | null; hint?: string; testID?: string }): El;
  /** A date as 'YYYY-MM-DD', or null. */
  export function DateField(props: { label?: string; value: string | null; onChange: (date: string | null) => void; min?: string; max?: string; error?: string | null; hint?: string; testID?: string }): El;
  /** A time as 'HH:MM', or null. */
  export function TimeField(props: { label?: string; value: string | null; onChange: (time: string | null) => void; error?: string | null; hint?: string; testID?: string }): El;
  export function Select<V extends string | number>(props: { label?: string; value: V | null; options: readonly { label: string; value: V }[]; onChange: (value: V) => void; placeholder?: string; error?: string | null; hint?: string; testID?: string }): El;
  export function Toggle(props: { label: string; description?: string; value: boolean; onValueChange: (value: boolean) => void; disabled?: boolean; testID?: string }): El;
  export function Stepper(props: { label?: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; testID?: string }): El;
  export function SearchField(props: { value: string; onChangeText: (text: string) => void; placeholder?: string; testID?: string }): El;
  export function Button(props: { title: string; onPress: () => void; variant?: 'primary' | 'secondary' | 'destructive' | 'plain'; icon?: IconName; disabled?: boolean; loading?: boolean; style?: Style; testID?: string }): El;
  /** An icon-only button; accessibilityLabel says what it does. */
  export function IconButton(props: { icon: IconName; onPress: () => void; accessibilityLabel: string; color?: string; size?: number; disabled?: boolean; testID?: string }): El;
  export function Chip(props: { label: string; selected?: boolean; onPress?: () => void; icon?: IconName; testID?: string }): El;

  /** value from 0 to 1. */
  export function ProgressBar(props: { value: number; color?: string; testID?: string }): El;
  export function Badge(props: { value: string | number; color?: string; testID?: string }): El;
  /** An amount in major units (12.5 = $12.50); `colored` makes negatives red and positives green. */
  export function Money(props: { amount: number; currency?: string; signed?: boolean; colored?: boolean; style?: StyleProp<TextStyle>; testID?: string }): El;
  export function formatMoney(amount: number, currency?: string, options?: { signed?: boolean }): string;
  /** 45 → "45s", 750 → "12m 30s", 3900 → "1h 05m". */
  export function Duration(props: { seconds: number; style?: StyleProp<TextStyle>; testID?: string }): El;
  export function formatDuration(seconds: number): string;
}

declare module 'expo-sqlite' {
  import type { ReactNode } from 'react';
  import type { Database } from '@homeai/sdk';

  export type SQLiteDatabase = Database;
  export function useSQLiteContext(): SQLiteDatabase;
  export function openDatabaseAsync(name?: string): Promise<SQLiteDatabase>;
  export function SQLiteProvider(props: { databaseName?: string; children?: ReactNode }): ReactNode;
}

declare module '@expo/vector-icons' {
  import type { ReactElement } from 'react';
  import type { StyleProp, TextStyle } from 'react-native';

  // icon names: generated by scripts/icons.py
  export type IconName =
  | 'add-circle-outline'
  | 'add-outline'
  | 'airplane-outline'
  | 'alarm-outline'
  | 'albums-outline'
  | 'alert-circle-outline'
  | 'analytics-outline'
  | 'apps-outline'
  | 'archive-outline'
  | 'arrow-back-outline'
  | 'arrow-down-outline'
  | 'arrow-forward-outline'
  | 'arrow-redo-outline'
  | 'arrow-undo-outline'
  | 'arrow-up-outline'
  | 'at-outline'
  | 'attach-outline'
  | 'bag-outline'
  | 'bandage-outline'
  | 'bar-chart-outline'
  | 'barbell-outline'
  | 'basket-outline'
  | 'battery-half-outline'
  | 'bed-outline'
  | 'beer-outline'
  | 'bicycle-outline'
  | 'bonfire-outline'
  | 'book-outline'
  | 'bookmark-outline'
  | 'bookmarks-outline'
  | 'briefcase-outline'
  | 'brush-outline'
  | 'bug-outline'
  | 'build-outline'
  | 'bulb-outline'
  | 'bus-outline'
  | 'business-outline'
  | 'cafe-outline'
  | 'calculator-outline'
  | 'calendar-outline'
  | 'call-outline'
  | 'camera-outline'
  | 'car-outline'
  | 'card-outline'
  | 'cart-outline'
  | 'cash-outline'
  | 'chatbox-outline'
  | 'chatbubble-outline'
  | 'chatbubbles-outline'
  | 'checkbox-outline'
  | 'checkmark-circle-outline'
  | 'checkmark-done-outline'
  | 'checkmark-outline'
  | 'chevron-back-outline'
  | 'chevron-down-outline'
  | 'chevron-forward-outline'
  | 'chevron-up-outline'
  | 'clipboard-outline'
  | 'close-circle-outline'
  | 'close-outline'
  | 'cloud-download-outline'
  | 'cloud-outline'
  | 'cloud-upload-outline'
  | 'code-outline'
  | 'color-palette-outline'
  | 'compass-outline'
  | 'construct-outline'
  | 'copy-outline'
  | 'create-outline'
  | 'cube-outline'
  | 'cut-outline'
  | 'desktop-outline'
  | 'document-outline'
  | 'document-text-outline'
  | 'documents-outline'
  | 'download-outline'
  | 'ellipse-outline'
  | 'ellipsis-horizontal-outline'
  | 'ellipsis-vertical-outline'
  | 'exit-outline'
  | 'eye-off-outline'
  | 'eye-outline'
  | 'fast-food-outline'
  | 'female-outline'
  | 'film-outline'
  | 'filter-outline'
  | 'fish-outline'
  | 'fitness-outline'
  | 'flag-outline'
  | 'flame-outline'
  | 'flash-outline'
  | 'flask-outline'
  | 'flower-outline'
  | 'folder-open-outline'
  | 'folder-outline'
  | 'footsteps-outline'
  | 'funnel-outline'
  | 'game-controller-outline'
  | 'gift-outline'
  | 'git-branch-outline'
  | 'glasses-outline'
  | 'globe-outline'
  | 'grid-outline'
  | 'hammer-outline'
  | 'hand-left-outline'
  | 'happy-outline'
  | 'headset-outline'
  | 'heart-dislike-outline'
  | 'heart-outline'
  | 'help-circle-outline'
  | 'help-outline'
  | 'home-outline'
  | 'hourglass-outline'
  | 'ice-cream-outline'
  | 'image-outline'
  | 'images-outline'
  | 'infinite-outline'
  | 'information-circle-outline'
  | 'key-outline'
  | 'keypad-outline'
  | 'laptop-outline'
  | 'layers-outline'
  | 'leaf-outline'
  | 'library-outline'
  | 'link-outline'
  | 'list-outline'
  | 'location-outline'
  | 'lock-closed-outline'
  | 'lock-open-outline'
  | 'log-in-outline'
  | 'log-out-outline'
  | 'magnet-outline'
  | 'mail-outline'
  | 'male-outline'
  | 'man-outline'
  | 'map-outline'
  | 'medal-outline'
  | 'medical-outline'
  | 'medkit-outline'
  | 'megaphone-outline'
  | 'menu-outline'
  | 'mic-off-outline'
  | 'mic-outline'
  | 'moon-outline'
  | 'musical-note-outline'
  | 'musical-notes-outline'
  | 'navigate-outline'
  | 'newspaper-outline'
  | 'notifications-outline'
  | 'nutrition-outline'
  | 'options-outline'
  | 'paper-plane-outline'
  | 'partly-sunny-outline'
  | 'pause-outline'
  | 'paw-outline'
  | 'pencil-outline'
  | 'people-outline'
  | 'person-add-outline'
  | 'person-circle-outline'
  | 'person-outline'
  | 'pie-chart-outline'
  | 'pin-outline'
  | 'pint-outline'
  | 'pizza-outline'
  | 'planet-outline'
  | 'play-circle-outline'
  | 'play-outline'
  | 'podium-outline'
  | 'pricetag-outline'
  | 'pricetags-outline'
  | 'print-outline'
  | 'pulse-outline'
  | 'push-outline'
  | 'qr-code-outline'
  | 'radio-outline'
  | 'rainy-outline'
  | 'reader-outline'
  | 'receipt-outline'
  | 'refresh-outline'
  | 'reload-outline'
  | 'remove-circle-outline'
  | 'remove-outline'
  | 'repeat-outline'
  | 'restaurant-outline'
  | 'ribbon-outline'
  | 'rocket-outline'
  | 'rose-outline'
  | 'sad-outline'
  | 'save-outline'
  | 'school-outline'
  | 'search-outline'
  | 'send-outline'
  | 'server-outline'
  | 'settings-outline'
  | 'share-outline'
  | 'shield-checkmark-outline'
  | 'shield-outline'
  | 'shirt-outline'
  | 'shuffle-outline'
  | 'skull-outline'
  | 'snow-outline'
  | 'sparkles-outline'
  | 'speedometer-outline'
  | 'square-outline'
  | 'star-half-outline'
  | 'star-outline'
  | 'stats-chart-outline'
  | 'stop-outline'
  | 'stopwatch-outline'
  | 'storefront-outline'
  | 'sunny-outline'
  | 'swap-horizontal-outline'
  | 'swap-vertical-outline'
  | 'sync-outline'
  | 'tennisball-outline'
  | 'terminal-outline'
  | 'text-outline'
  | 'thermometer-outline'
  | 'thumbs-down-outline'
  | 'thumbs-up-outline'
  | 'ticket-outline'
  | 'time-outline'
  | 'timer-outline'
  | 'today-outline'
  | 'toggle-outline'
  | 'trail-sign-outline'
  | 'train-outline'
  | 'trash-outline'
  | 'trending-down-outline'
  | 'trending-up-outline'
  | 'trophy-outline'
  | 'tv-outline'
  | 'umbrella-outline'
  | 'videocam-outline'
  | 'volume-high-outline'
  | 'volume-mute-outline'
  | 'walk-outline'
  | 'wallet-outline'
  | 'warning-outline'
  | 'watch-outline'
  | 'water-outline'
  | 'wifi-outline'
  | 'wine-outline'
  | 'woman-outline';
  // end icon names

  /** Ionicons, outline style: the names in IconName (a subset of Ionicons; filled and sharp variants aren't included). */
  export function Ionicons(props: { name: IconName; size?: number; color?: string; style?: StyleProp<TextStyle>; testID?: string }): ReactElement | null;
}

declare module 'expo-router' {
  import type { ReactElement, ReactNode } from 'react';
  import type { IconName } from '@expo/vector-icons';

  export type Href = string | { pathname: string; params?: Record<string, unknown> };
  type HeaderSlot = ReactNode | ((props: { tintColor: string }) => ReactNode);
  export type ScreenOptions = {
    title?: string;
    headerShown?: boolean;
    /** 'modal' slides the screen up over the one beneath, with a Close button. Set it on the layout's Stack.Screen. */
    presentation?: 'card' | 'modal';
    /** Replaces the back button. */
    headerLeft?: HeaderSlot;
    headerRight?: HeaderSlot;
  };
  export type TabsScreenOptions = {
    title?: string;
    headerShown?: boolean;
    /** The tab's icon (shorthand for tabBarIcon with Ionicons). */
    icon?: IconName;
    tabBarIcon?: (props: { color: string; size: number; focused: boolean }) => ReactNode;
    tabBarBadge?: string | number;
    /** null hides the route from the tab bar. */
    href?: null;
    headerLeft?: HeaderSlot;
    headerRight?: HeaderSlot;
  };
  type Params = Record<string, string | string[]>;

  export interface Router {
    push(href: Href): void;
    navigate(href: Href): void;
    replace(href: Href): void;
    back(): void;
    canGoBack(): boolean;
    dismissAll(): void;
    setParams(params: Params): void;
  }

  export const router: Router;
  export function useRouter(): Router;
  export function useLocalSearchParams<T extends Params = Params>(): T;
  export function useGlobalSearchParams<T extends Params = Params>(): T;
  export function usePathname(): string;

  export function Stack(props: { children?: ReactNode; screenOptions?: ScreenOptions }): ReactElement;
  export namespace Stack {
    function Screen(props: { name?: string; options?: ScreenOptions }): ReactElement | null;
  }
  /** A bottom tab bar over the routes in this layout's folder; each tab keeps its own stack. */
  export function Tabs(props: { children?: ReactNode; screenOptions?: TabsScreenOptions }): ReactElement;
  export namespace Tabs {
    function Screen(props: { name: string; options?: TabsScreenOptions }): ReactElement | null;
  }
  export function Slot(): ReactElement | null;
  export function Link(props: {
    href: Href;
    replace?: boolean;
    asChild?: boolean;
    children?: ReactNode;
    style?: unknown;
    testID?: string;
  }): ReactElement;
}
