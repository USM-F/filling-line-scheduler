"""Deterministic left shift of fixed production runs; no optimization backend."""

from dataclasses import replace
from typing import TYPE_CHECKING

from filling_scheduler.problem import Problem

if TYPE_CHECKING:
    from filling_scheduler.milp import Run


def left_shift(problem: Problem, runs: list["Run"]) -> list["Run"]:
    """Keep quantities and line order; start every run as early as its calendar allows.

    With no shared resources or release dates, earlier predecessor completion
    cannot delay its successor. Setup takes wall time, production takes work time.
    """
    shifted = []
    for line in problem.lines:
        previous = None
        for run in sorted((r for r in runs if r.line == line), key=lambda r: (r.start, r.sku)):
            ready = previous.end if previous is not None else 0
            setup_start = ready if previous is not None else None
            if previous is not None:
                ready += problem.changeover[previous.sku, run.sku]
            remaining = run.duration
            start = end = None
            for window in problem.windows:
                a = max(ready, window.start)
                if a >= window.end:
                    continue
                if start is None:
                    start = a
                take = min(remaining, window.end-a)
                remaining -= take
                end = a + take
                if remaining == 0:
                    break
            if remaining or start is None or start > run.start or end > run.end:
                raise ValueError("Fixed runs cannot be shifted left within their original bounds")
            previous = replace(run, start=start, end=end, setup_start=setup_start)
            shifted.append(previous)
    return shifted
