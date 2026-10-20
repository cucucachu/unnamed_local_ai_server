import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction } from '@homeai/sdk';
import { Screen } from '@homeai/ui';
import { EventForm, newEvent } from '../components/EventForm';
import { saveEventParams, today, type EventValues } from '../lib/calendar';

export default function NewEventScreen() {
  const router = useRouter();
  const { date, time } = useLocalSearchParams<{ date?: string; time?: string }>();

  async function create(v: EventValues) {
    await runAction('saveEvent', saveEventParams(null, v));
    router.back();
  }

  return (
    <Screen>
      <EventForm initial={newEvent(date || today(), time || undefined)} saveTitle="Add event" onSave={create} />
    </Screen>
  );
}
