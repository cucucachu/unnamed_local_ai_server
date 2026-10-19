import { useMembers, useQuery } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen } from '@homeai/ui';

type Done = { id: number; name: string; done_on: string; _created_by: string };

export default function HistoryScreen() {
  const members = useMembers();
  const { data } = useQuery<Done>(
    `SELECT d.id, c.name, d.done_on, d._created_by
     FROM completions d JOIN chores c ON c.id = d.chore_id
     ORDER BY d.done_on DESC, d.id DESC LIMIT 200`,
  );
  const who = (id: string) => members.find((m) => m.id === id)?.name ?? 'Someone';

  return (
    <Screen>
      <List
        data={data}
        keyExtractor={(d) => d.id}
        empty={<EmptyState icon="time-outline" title="Nothing done yet" message="Each time someone taps Done, it's listed here." />}
        renderItem={(d) => <ListRow title={d.name} subtitle={`${who(d._created_by)} · ${d.done_on}`} testID={`done-row-${d.id}`} />}
        testID="history"
      />
    </Screen>
  );
}
