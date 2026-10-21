// The game without the graphics: where the sliding block is, and what a drop
// does to it. Units are three.js world units; the base block is SIZE x SIZE.

export const SIZE = 3;
export const HEIGHT = 0.5;
/** The sliding block swings between -RANGE and +RANGE from the centre of the block below. */
export const RANGE = 4.5;
/** A drop this close to the block below snaps onto it and keeps the full size. */
export const PERFECT = 0.12;

export type Axis = 'x' | 'z';
/** A block's footprint: its centre and its size along x (w) and z (d). */
export type Block = { x: number; z: number; w: number; d: number };

export const BASE: Block = { x: 0, z: 0, w: SIZE, d: SIZE };

/** Blocks come in along x and z by turns; `score` is how many are on the tower already. */
export function axisFor(score: number): Axis {
  return score % 2 === 0 ? 'x' : 'z';
}

/** Seconds for a block to cross from one side to the other; a little faster as the tower grows. */
export function crossingSeconds(score: number) {
  return Math.max(0.6, 1.5 - score * 0.04);
}

/** The block's offset from the centre of the one below, `t` seconds after it appeared at -RANGE:
 * over to +RANGE and back, again and again. It is over the centre at crossingSeconds / 2. */
export function slide(score: number, t: number) {
  const u = (t / crossingSeconds(score)) % 2;
  return -RANGE + 2 * RANGE * (u <= 1 ? u : 2 - u);
}

export type Drop =
  | { kind: 'perfect'; placed: Block }
  | { kind: 'cut'; placed: Block; falling: Block }
  | { kind: 'miss'; falling: Block };

/** Drops a block `offset` along `axis` from the centre of `below`: what stays on the tower is the
 * overlap, and the overhang falls. */
export function drop(below: Block, axis: Axis, offset: number): Drop {
  if (Math.abs(offset) <= PERFECT) return { kind: 'perfect', placed: { ...below } };
  const c = below[axis];
  const size = axis === 'x' ? below.w : below.d;
  const along = (centre: number, length: number): Block =>
    axis === 'x' ? { ...below, x: centre, w: length } : { ...below, z: centre, d: length };
  const overlap = size - Math.abs(offset);
  if (overlap <= 0) return { kind: 'miss', falling: along(c + offset, size) };
  return {
    kind: 'cut',
    placed: along(c + offset / 2, overlap),
    falling: along(c + (Math.sign(offset) * size) / 2 + offset / 2, Math.abs(offset)),
  };
}

/** The block's color at height `level`: the hue drifts slowly up the tower. */
export function hueAt(level: number) {
  return ((200 + level * 9) % 360) / 360;
}
