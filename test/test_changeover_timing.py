from dataclasses import replace

import pytest

from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.milp import SchedulingMilp
from filling_scheduler.schedule import materialize_schedule
from filling_scheduler.validation import validate_schedule, changeover_calendar_usage
from data_generators import example, lunch_example


def checked(problem, result):
    schedule = materialize_schedule(problem, result, "lunch")
    assert validate_schedule(problem, schedule)["valid"]
    return schedule, changeover_calendar_usage(problem, schedule)


@pytest.mark.parametrize("weights,order,makespan,working", [
    ((1, 0, 1, 0), ["A", "B"], 240, 0),
    ((1, 0, 0, 1), ["B", "A"], 270, 30),
])
def test_weighted_already_accounts_for_calendar_but_has_tradeoffs(weights, order, makespan, working):
    problem = lunch_example()
    result = SchedulingMilp(problem).solve(objective_mode="weighted", objective_weights=weights)
    _, usage = checked(problem, result)
    assert [r.sku for r in sorted(result.runs, key=lambda r:r.start)] == order
    assert result.objectives["makespan_ticks"] == makespan
    assert usage["total_changeover_minutes"] == 30
    assert usage["working_changeover_minutes"] == working


def test_wait_for_lunch_reduces_occupied_work_without_delaying_production():
    problem = lunch_example(a=110)
    plain = SchedulingMilp(problem).solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0.01))
    extended_model = SchedulingMilp(problem, working_changeover_weight=1)
    result = extended_model.solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0.01))
    _, before = checked(problem, plain)
    schedule, after = checked(problem, result)
    assert before["working_changeover_minutes"] == 10
    assert after["working_changeover_minutes"] == 0
    assert result.status == "OPTIMAL"
    assert result.objectives["working_changeover_ticks"] == 0
    assert [(r.sku, r.start, r.end) for r in plain.runs] == [(r.sku, r.start, r.end) for r in result.runs]
    setup = next(s for s in schedule.lines[0].slots if s.type == "changeover")
    assert setup.start.hour == 10
    assert setup.end.hour <= 11
    assert result.weighted_value == pytest.approx(30 + 240 + 0.01 * 180)


def test_short_break_does_not_force_waiting_and_delaying_next_run():
    problem = lunch_example(a=110, setup=45, break_end="10:30")
    # A must be first to isolate the waiting decision from reordering.
    model = SchedulingMilp(problem, working_changeover_weight=0.01)
    model.row({model.variables["A", "L1"]["first"]: 1}, 1, 1)
    result = model.solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0.01))
    _, usage = checked(problem, result)
    a, b = sorted(result.runs, key=lambda r:r.start)
    assert a.end == 110  # 09:50, ten minutes before lunch, shorter than setup duration.
    assert b.setup_start == 110
    assert b.start == 155  # 10:35; waiting until 10:00 would delay this to 10:45.
    assert usage["working_changeover_minutes"] == 15
    assert result.objectives["working_changeover_ticks"] == 15


def test_first_run_start_is_already_free_to_shift():
    problem = lunch_example()
    model = SchedulingMilp(problem)
    model.row({model.variables["A", "L1"]["first"]: 1}, 1, 1)
    model.row({model.variables["A", "L1"]["start"]: 1}, 10, 10)
    result = model.solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0))
    checked(problem, result)
    assert next(r for r in result.runs if r.sku == "A").start == 10


def test_moving_only_setup_inside_existing_gap_leaves_four_objectives_unchanged():
    problem = lunch_example(a=110)
    result = SchedulingMilp(problem).solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0.01))
    _, before = checked(problem, result)
    moved = replace(result, runs=[replace(r, setup_start=120) if r.predecessor else r for r in result.runs])
    _, after = checked(problem, moved)
    assert result.objectives == moved.objectives
    assert result.weighted_value == moved.weighted_value
    assert before["working_changeover_minutes"] == 10
    assert after["working_changeover_minutes"] == 0


@pytest.mark.parametrize("setup", [0, 30, 90])
def test_setup_clock_spans_work_and_break_segments(setup):
    problem = lunch_example(a=20, b=20, setup=setup)
    model = SchedulingMilp(problem, working_changeover_weight=1)
    result = model.solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0.01))
    _, usage = checked(problem, result)
    assert result.objectives["working_changeover_ticks"] == usage["working_changeover_minutes"]
    assert result.weighted_value == pytest.approx(
        setup + result.objectives["makespan_ticks"] + 0.01*result.objectives["start_sum_ticks"] + usage["working_changeover_minutes"])


def test_setup_crossing_multiple_breaks():
    data = example({"A": 2, "B": 1}, lines=1, capacity=10)
    data["planningHorizon"]["end"] = "2026-08-17T08:10:00+00:00"
    data["calendar"]["shifts"][0]["breaks"] = [
        {"startTime": "08:03", "endTime": "08:04"}, {"startTime": "08:06", "endTime": "08:08"}]
    data["changeoverMatrixMinutes"]["A"]["B"] = 5
    problem = prepare_problem(SchedulingInput.model_validate(data))
    model = SchedulingMilp(problem, working_changeover_weight=1)
    model.row({model.variables["A", "L1"]["first"]: 1}, 1, 1)
    model.row({model.variables["B", "L1"]["setup_start"]: 1}, 2, 2)
    result = model.solve(objective_mode="weighted", objective_weights=(1, 0, 1, 0))
    _, usage = checked(problem, result)
    assert usage["working_changeover_minutes"] == 3
    assert result.objectives["working_changeover_ticks"] == 3


def test_optional_setup_model_handles_empty_demand():
    problem = prepare_problem(SchedulingInput.model_validate(example({"A": 0})))
    result = SchedulingMilp(problem, working_changeover_weight=1).solve(
        objective_mode="weighted", objective_weights=(1, 0, 1, 0))
    assert result.objectives["working_changeover_ticks"] == 0
    assert result.weighted_value == 0
