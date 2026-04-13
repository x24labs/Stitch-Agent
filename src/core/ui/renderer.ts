// Terminal renderer - uses ANSI save/restore cursor position
// Save cursor before frame, restore before next frame, overwrite in place

export class Renderer {
  private timer: ReturnType<typeof setInterval> | null = null;
  private renderFn: (() => string) | null = null;
  private started = false;
  private cols = 80;

  enter(): void {
    this.started = true;
    this.cols = process.stdout.columns || 80;
    // Clear screen, move to top, hide cursor, save position
    process.stdout.write("\x1b[2J\x1b[H\x1b[?25l\x1b7");
  }

  exit(): void {
    if (this.started) {
      this.started = false;
      process.stdout.write("\x1b[?25h");
    }
  }

  paint(content: string): void {
    if (!this.started) return;

    // Restore saved cursor position (top of screen), then save again
    process.stdout.write("\x1b8\x1b7");

    const lines = content.split("\n");
    const out: string[] = [];
    for (const l of lines) {
      const visible = l.replace(/\x1b\[[0-9;]*m/g, "").length;
      // \x1b[2K clears the line, \r moves to column 0, then write content
      out.push(`\x1b[2K${l}${" ".repeat(Math.max(0, this.cols - visible))}`);
    }
    process.stdout.write(out.join("\n"));
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
