"""Rich-based TUI for stitch run.

Renders a live-updating display with:
- Header panel (repo, agent, job count)
- Job status table with spinners for running jobs
- Live log tail for the active job
- Summary on completion
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from stitch_agent.run.models import CIJob, JobResult, RunReport

_STATUS_ICONS = {
    "passed": "[bold green]\u2705[/]",
    "escalated": "[bold red]\u274c[/]",
    "skipped": "[dim]\u23ed\ufe0f [/]",
    "not_run": "[dim]\u2796[/]",
    "failed": "[bold red]\u274c[/]",
    "running": "[bold yellow]\u25b6[/]",
    "pending": "[dim]\u23f8[/]",
}

_LOG_TAIL_LINES = 12


class RunUI:
    """Manages the live TUI during a stitch run."""

    def __init__(
        self,
        console: Console | None = None,
        agent: str = "",
        repo: str = "",
    ) -> None:
        self.console = console or Console()
        self.agent = agent
        self.repo = repo
        self.jobs: list[_JobState] = []
        self._active_job: str | None = None
        self._active_log: str = ""
        self._active_attempt: int = 0
        self._active_max_attempts: int = 3
        self._start_time: float = 0.0
        self._live: Live | None = None
        self._watch_cycle: int = 0

    def init_jobs(self, jobs: list[CIJob]) -> None:
        self.jobs = [
            _JobState(
                name=j.name,
                stage=j.stage,
                status="skipped" if j.skip_reason else "pending",
                skip_reason=j.skip_reason,
            )
            for j in jobs
        ]

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.stop()
            self._live = None

    def job_started(self, name: str, attempt: int, max_attempts: int) -> None:
        self._active_job = name
        self._active_log = ""
        self._active_attempt = attempt
        self._active_max_attempts = max_attempts
        for j in self.jobs:
            if j.name == name:
                j.status = "running"
                j.start_time = time.monotonic()
                j.attempts = attempt
                break
        self._refresh()

    def job_log_update(self, name: str, log: str) -> None:
        if name == self._active_job:
            self._active_log = log
            self._refresh()

    def job_finished(self, name: str, result: JobResult) -> None:
        for j in self.jobs:
            if j.name == name:
                j.status = result.status
                j.attempts = result.attempts
                j.duration = time.monotonic() - (j.start_time or time.monotonic())
                j.error_log = result.error_log
                break
        if name == self._active_job:
            self._active_job = None
            self._active_log = ""
        self._refresh()

    def watch_cycle(self, cycle: int) -> None:
        self._watch_cycle = cycle
        for j in self.jobs:
            if j.status != "skipped":
                j.status = "pending"
                j.duration = None
                j.attempts = 0
                j.error_log = ""
        self._refresh()

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Panel:
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        header = self._render_header(elapsed)
        table = self._render_table()
        parts: list[object] = [header, Text(), table]

        if self._active_job and self._active_log:
            log_panel = self._render_log_panel()
            parts.append(Text())
            parts.append(log_panel)

        return Panel(
            Group(*parts),
            title=f"[bold]stitch run[/] [cyan]\\[{self.agent}][/]",
            border_style="blue",
            padding=(1, 2),
        )

    def _render_header(self, elapsed: float) -> Text:
        runnable = sum(1 for j in self.jobs if j.status != "skipped")
        skipped = sum(1 for j in self.jobs if j.status == "skipped")
        passed = sum(1 for j in self.jobs if j.status == "passed")
        failed = sum(1 for j in self.jobs if j.status in ("escalated", "failed"))

        line = Text()
        line.append("repo ", style="dim")
        line.append(self.repo, style="bold")
        line.append("  ", style="dim")
        line.append(f"{runnable} jobs", style="cyan")
        if skipped:
            line.append(f", {skipped} skipped", style="dim")
        if passed:
            line.append(f", {passed} passed", style="green")
        if failed:
            line.append(f", {failed} failed", style="red")
        line.append(f"  [{elapsed:.1f}s]", style="dim")
        if self._watch_cycle > 0:
            line.append(f"  cycle #{self._watch_cycle}", style="dim italic")
        return line

    def _render_table(self) -> Table:
        table = Table(
            show_header=False,
            show_edge=False,
            pad_edge=False,
            box=None,
            padding=(0, 1),
        )
        table.add_column("icon", width=3, no_wrap=True)
        table.add_column("name", min_width=20, no_wrap=True)
        table.add_column("info", no_wrap=True)

        for j in self.jobs:
            icon = _STATUS_ICONS.get(j.status, "?")
            name_style = "bold" if j.status == "running" else ""

            info_parts: list[str] = []
            if j.status == "running":
                elapsed = time.monotonic() - (j.start_time or time.monotonic())
                info_parts.append(f"[yellow]running[/] [{elapsed:.1f}s]")
                if self._active_max_attempts > 1:
                    info_parts.append(
                        f"[dim](attempt {self._active_attempt}/{self._active_max_attempts})[/]"
                    )
            elif j.status == "passed":
                dur = f"{j.duration:.1f}s" if j.duration else ""
                info_parts.append(f"[green]{dur}[/]")
                if j.attempts and j.attempts > 1:
                    info_parts.append(f"[dim]({j.attempts} attempts)[/]")
            elif j.status == "escalated":
                dur = f"{j.duration:.1f}s" if j.duration else ""
                info_parts.append(f"[red]failed[/] {dur}")
                if j.attempts:
                    info_parts.append(f"[dim]({j.attempts} attempts)[/]")
            elif j.status == "skipped":
                reason = j.skip_reason or "skipped"
                info_parts.append(f"[dim]{reason}[/]")
            elif j.status == "pending":
                info_parts.append("[dim]pending[/]")
            elif j.status == "not_run":
                info_parts.append("[dim]not run[/]")

            table.add_row(icon, f"[{name_style}]{j.name}[/]", " ".join(info_parts))

        return table

    def _render_log_panel(self) -> Panel:
        lines = self._active_log.strip().splitlines()
        tail = lines[-_LOG_TAIL_LINES:] if len(lines) > _LOG_TAIL_LINES else lines
        truncated = len(lines) > _LOG_TAIL_LINES

        log_text = Text()
        if truncated:
            log_text.append(f"  ... ({len(lines) - _LOG_TAIL_LINES} lines above)\n", style="dim")
        for line in tail:
            if _is_error_line(line):
                log_text.append(f"  {line}\n", style="red")
            elif _is_success_line(line):
                log_text.append(f"  {line}\n", style="green")
            else:
                log_text.append(f"  {line}\n", style="dim")

        return Panel(
            log_text,
            title=f"[bold]{self._active_job}[/]",
            border_style="yellow",
            padding=(0, 1),
        )


class _JobState:
    def __init__(
        self,
        name: str,
        stage: str,
        status: str = "pending",
        skip_reason: str | None = None,
    ) -> None:
        self.name = name
        self.stage = stage
        self.status = status
        self.skip_reason = skip_reason
        self.start_time: float | None = None
        self.duration: float | None = None
        self.attempts: int = 0
        self.error_log: str = ""


def _is_error_line(line: str) -> bool:
    lower = line.lower()
    return any(
        kw in lower
        for kw in ("error", "fail", "assert", "exception", "traceback", "fatal")
    )


def _is_success_line(line: str) -> bool:
    lower = line.lower()
    return any(kw in lower for kw in ("pass", "ok", "success", "✓"))


def print_summary(console: Console, report: RunReport) -> None:
    """Print a final summary after the run completes."""
    passed = sum(1 for j in report.jobs if j.status == "passed")
    failed = sum(1 for j in report.jobs if j.status in ("escalated", "failed"))
    skipped = sum(1 for j in report.jobs if j.status == "skipped")

    if report.overall_status == "passed":
        console.print(
            f"\n[bold green]\u2705 All {passed} jobs passed[/]"
            + (f" [dim]({skipped} skipped)[/]" if skipped else "")
        )
    else:
        console.print(f"\n[bold red]\u274c {failed} failed[/], {passed} passed")
        for j in report.jobs:
            if j.status in ("escalated", "failed") and j.error_log:
                console.print(f"\n[bold red]{j.name}[/]:")
                tail = j.error_log.strip().splitlines()[-6:]
                for line in tail:
                    console.print(f"  [dim]{line}[/]")
