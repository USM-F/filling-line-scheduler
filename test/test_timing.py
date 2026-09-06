from dataclasses import replace

import pytest

from filling_scheduler.milp import SchedulingMilp, run_objectives
from filling_scheduler.schedule import materialize_schedule
from filling_scheduler.timing import left_shift
from filling_scheduler.validation import validate_schedule
from data_generators import example, calendar_example, prepared


@pytest.mark.parametrize("data", [example(), example({"A": 0}), example({"A": 3}, units=2), calendar_example()])
def test_left_shift_preserves_objectives_and_feasibility(data):
    problem = prepared(data)
    result = SchedulingMilp(problem).solve()
    original = result.runs
    result.runs = left_shift(problem, original)
    assert run_objectives(problem, original) == run_objectives(problem, result.runs)
    assert max((r.end for r in result.runs), default=0) <= max((r.end for r in original), default=0)
    assert sum(r.start for r in result.runs) <= sum(r.start for r in original)
    assert sorted((r.sku, r.line, r.quantity, r.predecessor) for r in original) == sorted(
        (r.sku, r.line, r.quantity, r.predecessor) for r in result.runs)
    assert validate_schedule(problem, materialize_schedule(problem, result, "shift"))["valid"]


def test_shift_removes_idle_time_and_rejects_impossible_runs():
    problem = prepared(example({"A": 40}, lines=1))
    model = SchedulingMilp(problem)
    model.row({model.variables["A", "L1"]["start"]: 1}, 30, 30)
    original = model.solve().runs
    shifted = left_shift(problem, original)
    assert original[0].start == 30
    assert shifted[0].start == 0 and shifted[0].end == 40
    with pytest.raises(ValueError):
        left_shift(problem, [replace(original[0], duration=1000)])
