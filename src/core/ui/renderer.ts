// Flicker-free terminal renderer
// First paint: console.clear(). Subsequent paints: cursor home + overwrite + pad.

export class Renderer {
  private timer: ReturnType<typeof setInterval> | null = null;
  private renderFn: (() => string) | null = null;
  private started = false;
  private firstPaint = true;
  private lastLineCount = 0;
  private cols = 80;

  enter(): void {
    this.started = true;
    this.firstPaint = true;
    this.cols = process.stdout.columns || 80;
    process.stderr.write("\x1b[?25l"); // hide cursor
  }

  exit(): void {
    if (this.started) {
      this.started = false;
      process.stderr.write("\x1b[?25h"); // show cursor
      console.clear();
    }
  }

  paint(content: string): void {
    if (!this.started) return;

    if (this.firstPaint) {
      console.clear();
      this.firstPaint = false;
    }

    // Pad each line to terminal width to overwrite previous content
    const lines = content.split("\n");
    const padded = lines.map((l) => {
      const visible = l.replace(/\x1b\[[0-9;]*m/g, "").length;
      const needed = Math.max(0, this.cols - visible);
      return l + " ".repeat(needed);
    });

    // Blank lines to clear leftover from previous longer frame
    while (padded.length < this.lastLineCount) {
      padded.push(" ".repeat(this.cols));
    }
    this.lastLineCount = lines.length;

    // Cursor home + write in one call
    process.stdout.write(`\x1b[H${padded.join("\n")}`);
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
