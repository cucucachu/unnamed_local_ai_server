import { hostnameFromUrl } from './webResult';
import type { ChatAssistantItem, ChatItem, ChatUserItem } from './useChat';
import type { ThreadMessage } from './threads';

export type ChatTurnStatus = 'running' | 'completed' | 'cancelled' | 'error' | 'awaiting_approval';

export interface ChatTurnMeta {
  status: ChatTurnStatus;
  durationMs?: number;
}

export interface ChatTurn {
  id: string;
  user: ChatUserItem | null;
  activity: ChatItem[];
  final: ChatAssistantItem | null;
  status: ChatTurnStatus;
  durationMs?: number;
}

/** Synthetic key for a live/history group that has no leading user row
 * (unit tests that emit frames without `sendMessage`, or a stray prefix). */
export const ORPHAN_TURN_KEY = '__orphan__';

export function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.round(ms / 1000));
  if (totalSec < 60) return `${totalSec}s`;
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  return `${minutes}m ${seconds}s`;
}

function fileNameFromArgs(args: Record<string, unknown>): string | null {
  const raw = args.file_path ?? args.path;
  if (typeof raw !== 'string' || !raw) return null;
  const parts = raw.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? raw;
}

function statusForTool(name: string, args: Record<string, unknown>): string {
  const file = fileNameFromArgs(args);
  switch (name) {
    case 'read_file':
    case 'ls':
    case 'glob':
    case 'grep':
      return file ? `Reading \`${file}\`` : 'Reading…';
    case 'edit_file':
    case 'write_file':
      return file ? `Editing \`${file}\`` : 'Editing…';
    case 'delete':
      return file ? `Editing \`${file}\`` : 'Editing…';
    case 'execute_code':
      return 'Running command…';
    case 'web_search':
      return 'Searching the web…';
    case 'web_fetch': {
      const url = typeof args.url === 'string' ? args.url : '';
      const host = url ? hostnameFromUrl(url) : '';
      return host ? `Reading \`${host}\`` : 'Reading…';
    }
    default:
      return 'Thinking…';
  }
}

/** Status line for a running turn, derived from the latest activity item. */
export function runningStatusLine(activity: ChatItem[]): string {
  for (let i = activity.length - 1; i >= 0; i -= 1) {
    const item = activity[i];
    if (item.kind === 'tool') return statusForTool(item.name, item.args);
    if (item.kind === 'assistant' && item.text.length > 0) return 'Writing…';
  }
  return 'Thinking…';
}

function inferStatus(items: ChatItem[]): ChatTurnStatus {
  if (items.some((item) => item.kind === 'error')) return 'error';
  if (items.some((item) => item.kind === 'assistant' && item.stopped)) return 'cancelled';
  if (items.some((item) => item.kind === 'assistant' && item.streaming)) return 'running';
  if (items.some((item) => item.kind === 'tool' && item.status === 'running')) return 'running';
  return 'completed';
}

function splitActivityAndFinal(
  items: ChatItem[],
  status: ChatTurnStatus,
): { activity: ChatItem[]; final: ChatAssistantItem | null } {
  if (status !== 'completed') {
    return { activity: items, final: null };
  }
  let lastIdx = -1;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.kind === 'assistant' && item.text !== '') {
      lastIdx = i;
      break;
    }
  }
  if (lastIdx === -1) return { activity: items, final: null };
  const final = items[lastIdx] as ChatAssistantItem;
  return { activity: items.filter((_, i) => i !== lastIdx), final };
}

/**
 * Group a flat `ChatItem[]` into turns. Each user row starts a turn;
 * following assistant/tool/error items belong to that turn. A leading
 * run of non-user items (no user row yet) becomes an orphan turn.
 *
 * At `completed`, the last non-empty assistant text is `final` and the
 * rest stays in `activity`. Cancelled / error / running / awaiting_approval
 * keep every non-user item in `activity`.
 */
export function groupItemsIntoTurns(
  items: ChatItem[],
  metas: Record<string, ChatTurnMeta> = {},
): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let current: { user: ChatUserItem | null; items: ChatItem[] } | null = null;

  const flush = () => {
    if (!current) return;
    const user = current.user;
    const key = user?.id ?? (turns.length === 0 ? ORPHAN_TURN_KEY : `${ORPHAN_TURN_KEY}-${turns.length}`);
    const meta = metas[key];
    const status = meta?.status ?? inferStatus(current.items);
    const { activity, final } = splitActivityAndFinal(current.items, status);
    turns.push({
      id: key,
      user,
      activity,
      final,
      status,
      durationMs: meta?.durationMs,
    });
    current = null;
  };

  for (const item of items) {
    if (item.kind === 'user') {
      flush();
      current = { user: item, items: [] };
    } else {
      if (!current) current = { user: null, items: [] };
      current.items.push(item);
    }
  }
  flush();
  return turns;
}

/** Pull `{status, durationMs}` off any assistant row that carries `turn`
 * (including empty+tool_calls rows the item mapper skips). Last one in a
 * user-delimited group wins — so a HITL pause + resume keeps the completed
 * duration from the final assistant. */
export function extractTurnMetaFromHistory(messages: ThreadMessage[]): Record<string, ChatTurnMeta> {
  const metas: Record<string, ChatTurnMeta> = {};
  let userId: string | null = null;
  for (const message of messages) {
    if (message.role === 'user') {
      userId = message.id;
      continue;
    }
    if (message.role === 'assistant' && message.turn && userId) {
      metas[userId] = {
        status: message.turn.status,
        durationMs: message.turn.duration_ms,
      };
    }
  }
  return metas;
}

export function finishedHeaderLabel(status: ChatTurnStatus, durationMs?: number): string {
  const dur = durationMs != null ? formatDuration(durationMs) : null;
  if (status === 'cancelled') return dur ? `Stopped after ${dur}` : 'Stopped';
  if (status === 'error') return dur ? `Failed after ${dur}` : 'Failed';
  return dur ? `Worked for ${dur}` : 'Worked';
}
