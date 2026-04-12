// Braille spinner animation

const FRAMES = ["\u280B", "\u2819", "\u2839", "\u2838", "\u283C", "\u2834", "\u2826", "\u2827", "\u2807", "\u280F"];

export class Spinner {
  private frameIndex = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private onTick: (() => void) | null = null;

  get frame(): string {
    return FRAMES[this.frameIndex % FRAMES.length]!;
  }

  start(onTick?: () => void): void {
    this.onTick = onTick ?? null;
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.frameIndex = (this.frameIndex + 1) % FRAMES.length;
      this.onTick?.();
    }, 80);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
