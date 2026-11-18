/* The canvas cannot read CSS variables per frame; these mirror styles/tokens.css. */
export const TONE0 = [255, 185, 106] as const;
export const TONE1 = [163, 184, 255] as const;
export const GOOD = [140, 239, 182] as const;
export const STATIC = [207, 203, 224] as const;
export const INK = [255, 255, 255] as const;
export const rgba = (c: readonly [number, number, number], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
