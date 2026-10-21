export const COLS = 7;
export const ROWS = 6;

export type Color = 'red' | 'yellow';
export type Game = { id: number; red: string; yellow: string; status: 'playing' | 'won' | 'draw' | 'resigned'; winner: string | null; _created_by: string };
export const GAME_COLUMNS = 'id, red, yellow, status, winner, _created_by';
export type Move = { turn: number; col: number; row: number; _created_by: string };
export const MOVE_COLUMNS = 'turn, col, row, _created_by';

/** board[col][row], row 0 at the bottom; null for empty. */
export type Board = (Color | null)[][];

export const colorOfTurn = (turn: number): Color => (turn % 2 === 0 ? 'red' : 'yellow');
export const playerOf = (g: Pick<Game, 'red' | 'yellow'>, color: Color) => (color === 'red' ? g.red : g.yellow);
export const opponentOf = (g: Pick<Game, 'red' | 'yellow'>, id: string) => (g.red === id ? g.yellow : g.red);

export function boardOf(moves: Pick<Move, 'turn' | 'col' | 'row'>[]): Board {
  const board: Board = Array.from({ length: COLS }, () => Array(ROWS).fill(null));
  for (const m of moves) board[m.col][m.row] = colorOfTurn(m.turn);
  return board;
}

/** Where a disc dropped in `col` lands, or null when it's full. */
export function landingRow(board: Board, col: number): number | null {
  const row = board[col].indexOf(null);
  return row < 0 ? null : row;
}

const DIRECTIONS = [
  [1, 0],
  [0, 1],
  [1, 1],
  [1, -1],
];

/** The four (or more) in a row through (col, row), or null. */
export function lineThrough(board: Board, col: number, row: number): [number, number][] | null {
  const color = board[col][row];
  if (!color) return null;
  for (const [dc, dr] of DIRECTIONS) {
    const line: [number, number][] = [[col, row]];
    for (const sign of [1, -1]) {
      for (let c = col + sign * dc, r = row + sign * dr; c >= 0 && c < COLS && r >= 0 && r < ROWS && board[c][r] === color; c += sign * dc, r += sign * dr) {
        line.push([c, r]);
      }
    }
    if (line.length >= 4) return line;
  }
  return null;
}

/** The winning line anywhere on the board, for showing a finished game. */
export function winningLine(board: Board): [number, number][] | null {
  for (let c = 0; c < COLS; c++) for (let r = 0; r < ROWS; r++) {
    const line = lineThrough(board, c, r);
    if (line) return line;
  }
  return null;
}

/** How the game stands if `turn`'s disc lands at (col, row) on `board` (which already has it). */
export function resultAfter(board: Board, col: number, row: number, turn: number): 'playing' | 'won' | 'draw' {
  if (lineThrough(board, col, row)) return 'won';
  return turn + 1 >= COLS * ROWS ? 'draw' : 'playing';
}
