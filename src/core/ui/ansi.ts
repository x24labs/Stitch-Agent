// ANSI escape code helpers for terminal rendering

const ESC = "\x1b";
const CSI = `${ESC}[`;

export const RESET = `${CSI}0m`;
export const BOLD = `${CSI}1m`;
export const DIM = `${CSI}2m`;
export const CURSOR_HOME = `${CSI}H`;
export const CLEAR_SCREEN = `${CSI}2J`;
export const CURSOR_HIDE = `${CSI}?25l`;
export const CURSOR_SHOW = `${CSI}?25h`;
export const ALT_SCREEN_ON = `${CSI}?1049h`;
export const ALT_SCREEN_OFF = `${CSI}?1049l`;
export const CLEAR_LINE = `${CSI}2K`;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.startsWith("#") ? hex.slice(1) : hex;
  const n = Number.parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Apply 24-bit foreground color from hex */
export function fg(hex: string, text: string): string {
  const [r, g, b] = hexToRgb(hex);
  return `${CSI}38;2;${r};${g};${b}m${text}${RESET}`;
}

/** Bold text */
export function bold(text: string): string {
  return `${BOLD}${text}${RESET}`;
}

/** Bold + colored text */
export function boldFg(hex: string, text: string): string {
  const [r, g, b] = hexToRgb(hex);
  return `${BOLD}${CSI}38;2;${r};${g};${b}m${text}${RESET}`;
}

/** Dim text */
export function dimText(text: string): string {
  return `${DIM}${text}${RESET}`;
}

/** Pad string to fixed width */
export function pad(s: string, n: number): string {
  return s.length >= n ? s.slice(0, n) : s + " ".repeat(n - s.length);
}

/** Format elapsed milliseconds */
export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const t = Math.floor((ms % 1000) / 100);
  return m > 0 ? `${m}:${(s % 60).toString().padStart(2, "0")}.${t}` : `${s}.${t}s`;
}

/** Build a progress bar string */
export function progressBar(pct: number, width: number, hex: string): string {
  const filled = Math.round((pct / 100) * width);
  const bar = "\u2588".repeat(filled) + "\u2591".repeat(width - filled);
  return fg(hex, bar);
}

/** Horizontal line */
export function line(width: number, hex?: string): string {
  const l = "\u2500".repeat(width);
  return hex ? fg(hex, l) : dimText(l);
}
