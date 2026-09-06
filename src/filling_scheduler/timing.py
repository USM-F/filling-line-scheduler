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
    Fractional rates require recomputing duration: whole-unit capacity lost at
    breaks depends on the selected windows. Integral rates keep the old duration.
    """
    shifted = []
    for line in problem.lines:
        previous = None
        for run in sorted((r for r in runs if r.line == line), key=lambda r: (r.start, r.sku)):
            ready = previous.end if previous is not None else 0
            setup_start = ready if previous is not None else None
            if previous is not None:
                ready += problem.changeover[previous.sku, run.sku]
            units = problem.units_per_tick[run.sku, line]
            fractional = units.denominator != 1
            remaining = run.quantity if fractional else run.duration
            duration = 0
            start = end = None
            for window in problem.windows:
                a = max(ready, window.start)
                if a >= window.end:
                    continue
                capacity = units.numerator * (window.end-a) // units.denominator if fractional else window.end-a
                if start is None and not capacity:
                    continue
                if start is None:
                    start = a
                amount = min(remaining, capacity)
                take = ((amount * units.denominator + units.numerator - 1) // units.numerator
                        if fractional and remaining <= capacity else window.end-a)
                if not fractional:
                    take = amount
                remaining -= amount
                duration += take
                end = a + take
                if remaining == 0:
                    break
            if remaining or start is None or start > run.start or end > run.end:
                raise ValueError("Fixed runs cannot be shifted left within their original bounds")
            previous = replace(run, start=start, end=end, duration=duration, setup_start=setup_start)
            shifted.append(previous)
    return shifted
