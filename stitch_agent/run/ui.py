"""Rich-based TUI for stitch run.

Renders a live-updating display with:
- Header panel (repo, agent, job count)
- Job status table with spinners for running jobs
- Live log tail for the active job
- Summary on completion
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from stitch_agent.run.models import CIJob, JobResult, RunReport

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
            refresh_per_second=4,
            transient=False,
            get_renderable=self._render,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            with contextlib.suppress(Exception):
                self._live.update(self._render())
            with contextlib.suppress(Exception):
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

    def driver_started(self, name: str, driver_name: str) -> None:
        self._active_job = name
        self._active_log = ""
        self._driver_name = driver_name
        for j in self.jobs:
            if j.name == name:
                j.status = "fixing"
                break
        self._refresh()

    def driver_log_update(self, name: str, log: str) -> None:
        if name == self._active_job:
            self._active_log = log
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
            with contextlib.suppress(Exception):
                self._live.update(self._render())

    def _render(self) -> Panel:
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        header = self._render_header(elapsed)
        table = self._render_table()
        parts: list[RenderableType] = [header, Text(), table]

        if self._active_job and self._active_log:
            log_panel = self._render_log_panel()
            parts.append(Text())
            parts.append(log_panel)

        title = Text.assemble(
            ("Stitch run ", "bold"),
            (f"[{self.agent}]", "cyan"),
        )
        return Panel(
            Group(*parts),
            title=title,
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
        line.append("  ")
        line.append(f"{runnable} jobs", style="cyan")
        if skipped:
            line.append(f", {skipped} skipped", style="dim")
        if passed:
            line.append(f", {passed} passed", style="green")
        if failed:
            line.append(f", {failed} failed", style="red")
        line.append(f"  {elapsed:.1f}s", style="dim")
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
        table.add_column("icon", width=4, no_wrap=True)
        table.add_column("name", min_width=20, no_wrap=True)
        table.add_column("info", no_wrap=True)

        for j in self.jobs:
            icon_text = _status_icon(j.status)
            name_text = Text(j.name, style="bold" if j.status in ("running", "fixing") else "")
            info_text = self._job_info(j)
            table.add_row(icon_text, name_text, info_text)

        return table

    def _job_info(self, j: _JobState) -> Text:
        info = Text()
        if j.status == "fixing":
            elapsed = time.monotonic() - (j.start_time or time.monotonic())
            driver = getattr(self, "_driver_name", "agent")
            info.append(f"fixing with {driver}", style="magenta bold")
            info.append(f" {elapsed:.1f}s", style="dim")
            if self._active_max_attempts > 1:
                info.append(
                    f" (attempt {self._active_attempt}/{self._active_max_attempts})",
                    style="dim",
                )
        elif j.status == "running":
            elapsed = time.monotonic() - (j.start_time or time.monotonic())
            info.append("running", style="yellow")
            info.append(f" {elapsed:.1f}s", style="dim")
            if self._active_max_attempts > 1:
                info.append(
                    f" (attempt {self._active_attempt}/{self._active_max_attempts})",
                    style="dim",
                )
        elif j.status == "passed":
            dur = f"{j.duration:.1f}s" if j.duration else ""
            info.append(dur, style="green")
            if j.attempts and j.attempts > 1:
                info.append(f" ({j.attempts} attempts)", style="dim")
        elif j.status == "escalated":
            dur = f"{j.duration:.1f}s" if j.duration else ""
            info.append("failed", style="red")
            if dur:
                info.append(f" {dur}", style="dim")
            if j.attempts:
                info.append(f" ({j.attempts} attempts)", style="dim")
        elif j.status == "skipped":
            info.append(j.skip_reason or "skipped", style="dim")
        elif j.status == "pending":
            info.append("pending", style="dim")
        elif j.status == "not_run":
            info.append("not run", style="dim")
        return info

    def _render_log_panel(self) -> Panel:
        lines = self._active_log.strip().splitlines()
        tail = lines[-_LOG_TAIL_LINES:] if len(lines) > _LOG_TAIL_LINES else lines
        truncated = len(lines) > _LOG_TAIL_LINES

        log_text = Text()
        if truncated:
            log_text.append(
                f"  ... ({len(lines) - _LOG_TAIL_LINES} lines above)\n", style="dim"
            )
        for line in tail:
            if _is_error_line(line):
                log_text.append(f"  {line}\n", style="red")
            elif _is_success_line(line):
                log_text.append(f"  {line}\n", style="green")
            else:
                log_text.append(f"  {line}\n", style="dim")

        job_name = self._active_job or ""
        is_fixing = any(j.name == job_name and j.status == "fixing" for j in self.jobs)
        if is_fixing:
            driver = getattr(self, "_driver_name", "agent")
            title = Text.assemble(
                (f"{job_name} ", "bold"),
                (f"fixing with {driver}", "magenta"),
            )
            border = "magenta"
        else:
            title = Text(job_name, style="bold")
            border = "yellow"
        return Panel(
            log_text,
            title=title,
            border_style=border,
            padding=(0, 1),
        )


def _status_icon(status: str) -> Text:
    """Return a Text object for the status icon (avoids markup parsing issues)."""
    icons = {
        "passed": ("\u2705", "bold green"),
        "escalated": ("\u274c", "bold red"),
        "skipped": ("\u23ed\ufe0f ", "dim"),
        "not_run": ("\u2796", "dim"),
        "failed": ("\u274c", "bold red"),
        "running": ("\u25b6", "bold yellow"),
        "fixing": ("\U0001f9e0", "bold magenta"),
        "pending": ("\u23f8", "dim"),
    }
    char, style = icons.get(status, ("?", ""))
    return Text(char, style=style)


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
    return any(kw in lower for kw in ("pass", "ok", "success", "\u2713"))


def print_summary(console: Console, report: RunReport) -> None:
    """Print a final summary after the run completes."""
    passed = sum(1 for j in report.jobs if j.status == "passed")
    failed = sum(1 for j in report.jobs if j.status in ("escalated", "failed"))
    skipped = sum(1 for j in report.jobs if j.status == "skipped")

    if report.overall_status == "passed":
        msg = Text()
        msg.append(f"\n\u2705 All {passed} jobs passed", style="bold green")
        if skipped:
            msg.append(f" ({skipped} skipped)", style="dim")
        console.print(msg)
    else:
        msg = Text()
        msg.append(f"\n\u274c {failed} failed", style="bold red")
        msg.append(f", {passed} passed")
        console.print(msg)
        for j in report.jobs:
            if j.status in ("escalated", "failed") and j.error_log:
                console.print(Text(f"\n{j.name}:", style="bold red"))
                tail = j.error_log.strip().splitlines()[-6:]
                for line in tail:
                    console.print(Text(f"  {line}", style="dim"))
