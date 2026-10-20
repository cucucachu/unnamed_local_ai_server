import { useQuery } from '@homeai/sdk';
import { EVENT_SELECT, type CalendarEvent } from './calendar';

/** One event (the whole series, if it repeats); undefined while loading, null once it's gone. */
export function useEvent(id: number): CalendarEvent | null | undefined {
  const { data } = useQuery<CalendarEvent>(`${EVENT_SELECT} WHERE e.id = ?`, [id]);
  return data === undefined ? undefined : (data[0] ?? null);
}
