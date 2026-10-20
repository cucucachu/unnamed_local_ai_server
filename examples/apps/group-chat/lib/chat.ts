// Times in the database are UTC ISO strings (`_created_at`); shown in local time.

export type Message = {
  id: number;
  body: string;
  edited: number;
  deleted: number;
  _created_by: string;
  _created_at: string;
  _updated_by: string;
};

export const MESSAGE_COLUMNS = 'id, body, edited, deleted, _created_by, _created_at, _updated_by';
/** How many more messages scrolling up loads. */
export const PAGE = 50;
/** Messages from one sender this close together share one name header. */
export const GROUP_MINUTES = 5;
export const MAX_CHARS = 4000;

function localDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function dayOf(iso: string): string {
  return localDay(new Date(iso));
}

/** "Today", "Yesterday", "Monday" within a week, else "Mon 3 Aug" (with the year if not this one). */
export function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const day = localDay(d);
  if (day === localDay(now)) return 'Today';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (day === localDay(yesterday)) return 'Yesterday';
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  });
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/**
 * For `messages` newest first (as the inverted list shows them): whether each starts a new day
 * (a separator above it) and whether it shows its sender's name (a new sender, a new day, or a
 * gap of more than `GROUP_MINUTES`).
 */
export function layout(messages: Pick<Message, '_created_by' | '_created_at'>[]) {
  return messages.map((m, i) => {
    const older = messages[i + 1];
    const newDay = !older || dayOf(older._created_at) !== dayOf(m._created_at);
    const gap = older ? new Date(m._created_at).getTime() - new Date(older._created_at).getTime() : Infinity;
    return { newDay, showSender: newDay || older._created_by !== m._created_by || gap > GROUP_MINUTES * 60_000 };
  });
}
