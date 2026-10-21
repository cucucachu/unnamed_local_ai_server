import { useRouter } from 'expo-router';
import { useMembers, useQuery, useSpace, useUser } from '@homeai/sdk';
import { EmptyState, List, ListRow, Screen, Section } from '@homeai/ui';
import { GAME_COLUMNS, colorOfTurn, opponentOf, playerOf, type Game } from '../lib/connect4';

type Row = Game & { moves: number };

/** Your games (your turn first), then games between other members, then finished ones. */
export default function GamesScreen() {
  const router = useRouter();
  const me = useUser();
  const members = useMembers();
  const canPlay = useSpace()?.role !== 'viewer';
  const { data } = useQuery<Row>(`SELECT ${GAME_COLUMNS}, (SELECT count(*) FROM moves m WHERE m.game_id = g.id) AS moves FROM games g ORDER BY id DESC`);
  const games = data ?? [];
  const name = (id: string | null) => (id === me?.id ? 'you' : (members.find((m) => m.id === id)?.name ?? 'someone'));
  const toMove = (g: Row) => playerOf(g, colorOfTurn(g.moves));
  const mine = (g: Row) => g.red === me?.id || g.yellow === me?.id;

  const playing = games.filter((g) => g.status === 'playing');
  const yourTurn = playing.filter((g) => toMove(g) === me?.id);
  const theirTurn = playing.filter((g) => mine(g) && toMove(g) !== me?.id);
  const others = playing.filter((g) => !mine(g));
  const finished = games.filter((g) => g.status !== 'playing');

  const result = (g: Row) => {
    if (g.status === 'draw') return 'Draw';
    const by = g.status === 'resigned' ? ' (resigned)' : '';
    return g.winner === me?.id ? `You won${by}` : `${name(g.winner)} won${by}`;
  };
  const row = (g: Row, subtitle: string) => (
    <ListRow
      key={g.id}
      title={mine(g) ? `vs ${name(opponentOf(g, me!.id))}` : `${name(g.red)} vs ${name(g.yellow)}`}
      subtitle={subtitle}
      icon={g.status === 'playing' ? 'ellipse-outline' : 'trophy-outline'}
      onPress={() => router.push({ pathname: '/game/[id]', params: { id: String(g.id) } })}
      testID={`game-${g.id}`}
    />
  );

  if (!games.length) {
    return (
      <EmptyState
        icon="ellipse-outline"
        title="No games yet"
        message={canPlay ? 'Start one against someone in this space.' : 'Games between members will show here.'}
        action={canPlay ? { title: 'New game', onPress: () => router.push('/new') } : undefined}
        testID="games-empty"
      />
    );
  }
  return (
    <Screen>
      {yourTurn.length ? (
        <Section title="Your turn">
          <List testID="your-turn">{yourTurn.map((g) => row(g, `Move ${g.moves + 1}`))}</List>
        </Section>
      ) : null}
      {theirTurn.length ? (
        <Section title="Their turn">
          <List testID="their-turn">{theirTurn.map((g) => row(g, `Waiting for ${name(toMove(g))}`))}</List>
        </Section>
      ) : null}
      {others.length ? (
        <Section title="Other games">
          <List testID="other-games">{others.map((g) => row(g, `${name(toMove(g))} to move`))}</List>
        </Section>
      ) : null}
      {finished.length ? (
        <Section title="Finished">
          <List testID="finished">{finished.map((g) => row(g, result(g)))}</List>
        </Section>
      ) : null}
    </Screen>
  );
}
