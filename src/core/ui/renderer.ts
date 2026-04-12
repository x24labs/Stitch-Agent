// Flicker-free terminal renderer
// Uses cursor home + overwrite instead of clear + write

export class Renderer {
  private timer: ReturnType<typeof setInterval> | null = null;
  private renderFn: (() => string) | null = null;
  private started = false;
  private lastLineCount = 0;
  private cols = 80;

  enter(): void {
    this.started = true;
    this.cols = process.stdout.columns || 80;
    // Clear screen once at start, hide cursor
    process.stdout.write("\x1b[2J\x1b[H\x1b[?25l");
  }

  exit(): void {
    if (this.started) {
      this.started = false;
      // Show cursor, clear screen
      process.stdout.write("\x1b[?25h\x1b[2J\x1b[H");
    }
  }

  paint(content: string): void {
    if (!this.started) return;

    // Pad each line to terminal width to overwrite previous content
    const lines = content.split("\n");
    const padded = lines.map((l) => {
      // Strip ANSI codes to get visible length
      const visible = l.replace(/\x1b\[[0-9;]*m/g, "").length;
      const needed = Math.max(0, this.cols - visible);
      return l + " ".repeat(needed);
    });

    // Blank lines to clear any leftover from previous longer frame
    while (padded.length < this.lastLineCount) {
      padded.push(" ".repeat(this.cols));
    }
    this.lastLineCount = lines.length;

    // Cursor home + write everything in one call (no flicker)
    process.stdout.write("\x1b[H" + padded.join("\n"));
  }

  startLoop(renderFn: () => string, intervalMs = 200): void {
    this.renderFn = renderFn;
    this.paint(renderFn());
    this.timer = setInterval(() => {
      this.cols = process.stdout.columns || 80;
      if (this.renderFn) this.paint(this.renderFn());
    }, intervalMs);
  }

  stopLoop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  repaint(): void {
    if (this.renderFn && this.started) {
      this.paint(this.renderFn());
    }
  }
}
