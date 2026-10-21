import { useState } from 'react';
import { Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { runAction, useDatabase, useMembers, useQuery, useSpace, useUser } from '@homeai/sdk';
import { Button, Card, EmptyState, Screen, useTheme } from '@homeai/ui';
import { BoardView, DISC } from '../../components/BoardView';
import { GAME_COLUMNS, MOVE_COLUMNS, boardOf, colorOfTurn, landingRow, opponentOf, playerOf, resultAfter, winningLine, type Color, type Game, type Move } from '../../lib/connect4';

export default function GameScreen() {
  const t = useTheme();
  const db = useDatabase();
  const router = useRouter();
  const me = useUser();
  const members = useMembers();
  const canPlay = useSpace()?.role !== 'viewer';
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: games } = useQuery<Game>(`SELECT ${GAME_COLUMNS} FROM games WHERE id = ?`, [Number(id)]);
  const { data: moveRows } = useQuery<Move>(`SELECT ${MOVE_COLUMNS} FROM moves WHERE game_id = ? ORDER BY turn`, [Number(id)]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmResign, setConfirmResign] = useState(false);

  if (!games || !moveRows) return null;
  const game = games[0];
  if (!game) return <EmptyState icon="ellipse-outline" title="This game was deleted" testID="game-gone" />;

  const moves = moveRows;
  const board = boardOf(moves);
  const turn = moves.length;
  const color = colorOfTurn(turn);
  const toMove = playerOf(game, color);
  const playing = game.status === 'playing';
  const isPlayer = game.red === me?.id || game.yellow === me?.id;
  const myTurn = playing && canPlay && toMove === me?.id;
  const name = (uid: string | null) => (uid === me?.id ? 'You' : (members.find((m) => m.id === uid)?.name ?? 'Someone'));
  const last = moves.length ? ([moves[moves.length - 1].col, moves[moves.length - 1].row] as [number, number]) : null;
  const line = game.status === 'won' ? winningLine(board) : null;
  // Turns are kept by this screen only: anyone who can edit the space (the agent too) can write a move. Say so when it happened.
  const outOfTurn = moves.filter((m) => m._created_by !== playerOf(game, colorOfTurn(m.turn)));

  async function drop(col: number) {
    if (!myTurn || busy) return;
    const row = landingRow(board, col);
    if (row === null) return setProblem('That column is full.');
    setProblem(null);
    setBusy(true);
    try {
      const after = boardOf([...moves, { turn, col, row }]);
      const result = resultAfter(after, col, row, turn);
      const { rows } = await runAction('dropDisc', { game_id: game.id, turn, col, result, winner: result === 'won' ? me!.id : null });
      if (Number(rows[0]?.moves) !== turn + 1) setProblem('The board changed; try again.');
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resign() {
    await db.runAsync("UPDATE games SET status = 'resigned', winner = ? WHERE id = ? AND status = 'playing'", [opponentOf(game, me!.id), game.id]);
    setConfirmResign(false);
  }

  async function rematch() {
    const { lastInsertRowId } = await db.runAsync('INSERT INTO games (red, yellow) VALUES (?, ?)', [game.yellow, game.red]);
    router.replace({ pathname: '/game/[id]', params: { id: String(lastInsertRowId) } });
  }

  const disc = (c: Color) => <View style={{ width: 16, height: 16, borderRadius: 8, backgroundColor: DISC[c] }} />;
  let status: string;
  if (game.status === 'won') status = game.winner === me?.id ? 'You won!' : `${name(game.winner)} won`;
  else if (game.status === 'resigned') status = game.winner === me?.id ? `${name(opponentOf(game, me!.id))} resigned: you win` : `${name(opponentOf(game, game.winner!))} resigned: ${name(game.winner)} wins`;
  else if (game.status === 'draw') status = 'Draw: the board is full';
  else if (myTurn) status = 'Your turn';
  else status = isPlayer ? `Waiting for ${name(toMove)}` : `${name(toMove)} to move`;

  return (
    <Screen>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }} testID="red-player">
          {disc('red')}
          <Text style={{ color: t.colors.text, fontWeight: toMove === game.red && playing ? '700' : '400' }}>{name(game.red)}</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }} testID="yellow-player">
          <Text style={{ color: t.colors.text, fontWeight: toMove === game.yellow && playing ? '700' : '400' }}>{name(game.yellow)}</Text>
          {disc('yellow')}
        </View>
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 32 }}>
        {playing ? disc(color) : null}
        <Text style={{ fontSize: t.font.title, fontWeight: '700', color: t.colors.text }} testID="status">
          {status}
        </Text>
      </View>
      <BoardView board={board} onDrop={myTurn ? (c) => void drop(c) : undefined} last={last} line={line} />
      {problem ? (
        <Text style={{ color: t.colors.danger, textAlign: 'center' }} testID="problem">
          {problem}
        </Text>
      ) : null}
      {outOfTurn.length ? (
        <Card testID="out-of-turn">
          {outOfTurn.map((m) => (
            <Text key={m.turn} style={{ color: t.colors.warning }}>
              Move {m.turn + 1} was made by {name(m._created_by)}, not {name(playerOf(game, colorOfTurn(m.turn)))}.
            </Text>
          ))}
        </Card>
      ) : null}
      {isPlayer && canPlay ? (
        <View style={{ gap: t.spacing.sm }}>
          {playing ? (
            confirmResign ? (
              <>
                <Button title="Yes, resign" variant="destructive" onPress={() => void resign()} testID="resign-confirm" />
                <Button title="Keep playing" variant="plain" onPress={() => setConfirmResign(false)} testID="resign-cancel" />
              </>
            ) : (
              <Button title="Resign" variant="secondary" icon="flag-outline" onPress={() => setConfirmResign(true)} testID="resign" />
            )
          ) : (
            <Button title="Rematch" icon="refresh-outline" onPress={() => void rematch()} testID="rematch" />
          )}
        </View>
      ) : null}
    </Screen>
  );
}
