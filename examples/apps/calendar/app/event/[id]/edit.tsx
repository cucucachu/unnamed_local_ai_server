import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction } from '@homeai/sdk';
import { EmptyState, Screen } from '@homeai/ui';
import { EventForm } from '../../../components/EventForm';
import { saveEventParams, valuesOf, type EventValues } from '../../../lib/calendar';
import { useEvent } from '../../../lib/useEvent';

/** Edit a whole event (scope "all"), or one occurrence of a repeating one ("one"), which becomes its own event. */
export default function EditEventScreen() {
  const router = useRouter();
  const { id, date, scope } = useLocalSearchParams<{ id: string; date?: string; scope?: string }>();
  const event = useEvent(Number(id));

  if (event === undefined) return null;
  if (event === null) return <EmptyState icon="calendar-outline" title="This event was deleted" testID="event-gone" />;

  const one = scope === 'one' && event.repeat !== 'none' && !!date;

  async function save(v: EventValues) {
    if (one) {
      // The series' screen behind this one shows the occurrence that was just replaced.
      await runAction('saveEvent', saveEventParams(null, v, { eventId: event!.id, date: date! }));
      router.dismissAll();
      return;
    }
    await runAction('saveEvent', saveEventParams(event!.id, v));
    router.back();
  }

  return (
    <Screen>
      <EventForm initial={valuesOf(event, one ? date : undefined)} saveTitle="Save" onSave={save} oneOccurrence={one} />
    </Screen>
  );
}
