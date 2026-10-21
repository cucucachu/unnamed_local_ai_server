import { Pressable, View } from 'react-native';
import { ROWS, type Board, type Color } from '../lib/connect4';

export const DISC: Record<Color, string> = { red: '#e03131', yellow: '#f5c400' };
const FRAME = '#1c5fd4';

/** The 7×6 board. Tap a column to drop a disc there (when `onDrop` is given). The last move and a winning line are ringed. */
export function BoardView({ board, onDrop, last, line }: { board: Board; onDrop?: (col: number) => void; last?: [number, number] | null; line?: [number, number][] | null }) {
  const ringed = (c: number, r: number) => line?.some(([lc, lr]) => lc === c && lr === r);
  return (
    <View style={{ flexDirection: 'row', backgroundColor: FRAME, borderRadius: 12, padding: 6, gap: 4, width: '100%', maxWidth: 480, alignSelf: 'center' }} testID="board">
      {board.map((column, c) => (
        <Pressable key={c} disabled={!onDrop} onPress={() => onDrop?.(c)} accessibilityLabel={`Column ${c + 1}`} testID={`col-${c}`} style={({ pressed }) => ({ flex: 1, gap: 4, opacity: pressed ? 0.85 : 1 })}>
          {Array.from({ length: ROWS }, (_, i) => {
            const r = ROWS - 1 - i;
            const disc = column[r];
            const isLast = last?.[0] === c && last?.[1] === r;
            return (
              <View
                key={r}
                testID={`cell-${c}-${r}`}
                accessibilityLabel={disc ?? 'empty'}
                style={{
                  aspectRatio: 1,
                  borderRadius: 999,
                  backgroundColor: disc ? DISC[disc] : '#ffffff',
                  borderWidth: ringed(c, r) ? 4 : isLast ? 3 : 0,
                  borderColor: ringed(c, r) ? '#2f9e44' : '#ffffff',
                }}
              />
            );
          })}
        </Pressable>
      ))}
    </View>
  );
}