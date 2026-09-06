"""Exact rational speeds, whole physical slots, and an independent tiny oracle."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from itertools import permutations, product

import pytest

from filling_scheduler.errors import ApplicationError
from filling_scheduler.milp import SchedulingMilp, run_objectives
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import materialize_schedule, Schedule
from filling_scheduler.timing import left_shift
from filling_scheduler.validation import validate_schedule
from data_generators import example


def data_for(rate, quantity, windows):
    data = example({"A": quantity}, lines=1)
    origin = datetime(2026, 8, 17, 8, tzinfo=timezone.utc)
    timestamp = lambda t: (origin + timedelta(minutes=t)).isoformat()
    data["planningHorizon"]["end"] = timestamp(windows[-1][1])
    data["calendar"]["shifts"] = [
        {"code": f"W{k}", "startTime": timestamp(a)[11:16], "endTime": timestamp(b)[11:16], "breaks": []}
        for k, (a, b) in enumerate(windows)]
    data["lines"][0]["eligibleProducts"][0]["capacityUnitsPerHour"] = rate
    return data


def checked(data):
    problem = prepare_problem(SchedulingInput.model_validate(data))
    result = SchedulingMilp(problem).solve(time_limit=20)
    schedule = materialize_schedule(problem, result, "exact-rate")
    report = validate_schedule(problem, schedule)
    assert report["valid"], report
    assert result.status == "OPTIMAL"
    return problem, result, schedule


@pytest.mark.parametrize("rate,quantity,ticks", [
    (20000, 20000, 60), (20000, 333, 1), (20000, 334, 2),
    (61, 61, 60), (30, 3, 6), (Decimal("0.6"), 1, 100),
    (Decimal("123.45"), 823, 400),
])
def test_exact_rate_and_minimum_duration(rate, quantity, ticks):
    problem, result, schedule = checked(data_for(rate, quantity, [(0, ticks)]))
    assert problem.units_per_tick["A", "L1"] == Fraction(rate) / 60
    assert result.runs[0].duration == ticks
    assert result.objectives["makespan_ticks"] == ticks
    assert schedule.summary.produced_by_product == {"A": quantity}
    production = schedule.lines[0].slots
    assert len(production) == 1 and production[0].quantity == quantity


@pytest.mark.parametrize("rate,quantity,windows", [
    (20000, 20001, [(0, 60)]),
    (90, 3, [(0, 1), (2, 3)]),  # Two 1.5-unit windows hold only two whole units.
    (30, 1, [(0, 1), (2, 3)]),  # Fractional unfinished units do not cross breaks.
])
def test_capacity_overflow_is_infeasible(rate, quantity, windows):
    with pytest.raises(ApplicationError) as exc:
        checked(data_for(rate, quantity, windows))
    assert exc.value.code == "INFEASIBLE"


@pytest.mark.parametrize("rate,quantity,windows,quantities,end", [
    (90, 2, [(0, 1), (2, 3)], [1, 1], 3),
    (90, 3, [(0, 1), (2, 4)], [1, 2], 4),
    (30, 2, [(0, 3), (4, 5), (6, 9)], [1, 1], 8),
])
def test_whole_units_across_breaks(rate, quantity, windows, quantities, end):
    problem, result, schedule = checked(data_for(rate, quantity, windows))
    assert [s.quantity for s in schedule.lines[0].slots] == quantities
    assert result.runs[0].start == 0 and result.runs[0].end == end
    assert validate_schedule(problem, materialize_schedule(problem, result, "again"))["valid"]


def test_left_shift_recomputes_working_duration_at_fractional_rate():
    problem = prepare_problem(SchedulingInput.model_validate(data_for(30, 2, [(0, 3), (4, 9)])))
    model = SchedulingMilp(problem)
    model.row({model.variables["A", "L1"]["start"]: 1}, 4, 4)
    result = model.solve(time_limit=20)
    assert (result.runs[0].start, result.runs[0].end, result.runs[0].duration) == (4, 8, 4)
    result.runs = left_shift(problem, result.runs)
    assert (result.runs[0].start, result.runs[0].end, result.runs[0].duration) == (0, 6, 5)
    assert validate_schedule(problem, materialize_schedule(problem, result, "shifted"))["valid"]


def test_validator_rejects_rounded_up_capacity():
    problem, result, schedule = checked(data_for(90, 2, [(0, 2)]))
    raw = schedule.model_dump(mode="json", by_alias=True)
    raw["lines"][0]["slots"][0]["end"] = problem.timestamp(1)
    report = validate_schedule(problem, Schedule.model_validate(raw))
    assert "CAPACITY" in {e["code"] for e in report["errors"]}


def test_rate_uses_actual_planning_precision():
    data = data_for(61, 61, [(0, 60)])
    data["planningHorizon"]["precisionMinutes"] = 5
    problem, result, schedule = checked(data)
    assert problem.units_per_tick["A", "L1"] == Fraction(61, 12)
    assert result.runs[0].duration == 12


@pytest.mark.parametrize("mode", ["weighted", "heuristic"])
def test_fractional_speed_in_all_solver_modes(mode):
    problem = prepare_problem(SchedulingInput.model_validate(data_for(20000, 667, [(0, 1), (2, 4)])))
    if mode == "weighted":
        result = SchedulingMilp(problem).solve(time_limit=20, objective_mode="weighted", objective_weights=(1, 1, .1, .01))
    else:
        result = SchedulingMilp(problem).solve(time_limit=20, optimize_timing=False)
        result.runs = left_shift(problem, result.runs)
    schedule = materialize_schedule(problem, result, mode)
    assert validate_schedule(problem, schedule)["valid"]
    assert [s.quantity for s in schedule.lines[0].slots] == [333, 334]
    assert result.runs[0].end == 4


def test_decimal_rate_through_public_all_command(tmp_path, monkeypatch, capsys):
    import json
    from filling_scheduler.cli import main
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "input.json"
    output = tmp_path / "schedule.json"
    # JSON parsing must preserve the numeric token as Decimal before converting
    # it to a rational rate. A Python float is only used to write this exact .5.
    source.write_text(json.dumps(data_for(20000.5, 40001, [(0, 120)])))
    assert main(["all", "--input", str(source), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["producedByProduct"] == {"A": 40001}
    assert result["makespan_minutes"] == 120
    assert output.with_suffix(".html").exists()
    assert main(["validate", "--input", str(source), "--schedule", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]


def oracle_run(rate, quantity, ready, windows):
    """Enumerate all endpoints; check capacities without the MILP formulation."""
    candidates = []
    for start in range(ready, windows[-1][1]):
        for end in range(start + 1, windows[-1][1] + 1):
            pieces = [(max(start, a), min(end, b)) for a, b in windows if max(start, a) < min(end, b)]
            if not pieces or pieces[0][0] != start or pieces[-1][1] != end:
                continue
            capacities = [int(rate * (b-a)) for a, b in pieces]
            remaining = quantity - sum(capacities[:-1])
            if (capacities[0] and 0 < remaining <= capacities[-1]
                    and rate * (pieces[-1][1]-pieces[-1][0]-1) < remaining):
                candidates.append((end, start))
    return min(candidates) if candidates else None


@pytest.mark.parametrize("seed", range(12))
def test_fractional_milp_matches_exhaustive_assignment_and_route_oracle(seed):
    import random
    rng = random.Random(seed)
    demand = {s: rng.randint(1, 4) for s in "AB"}
    windows = [(0, 2), (3, 7)]
    rates = {(s, l): rng.choice([Fraction(1, 2), Fraction(3, 2), Fraction(4, 3), Fraction(2)])
             for s in "AB" for l in range(2)}
    best = None
    # Exhaust integer allocations, both line orders, and possible endpoints.
    for amounts in product(*(range(q + 1) for q in demand.values())):
        allocated = {(s, l): q if l == 0 else demand[s]-q
                     for (s, q) in zip(demand, amounts) for l in range(2)}
        orders = [list(permutations(s for s in demand if allocated[s, l])) for l in range(2)]
        for sequences in product(*orders):
            starts, ends, setups = [], [], 0
            for l, sequence in enumerate(sequences):
                ready = 0
                for index, s in enumerate(sequence):
                    transition = 2 if index else 0
                    found = oracle_run(rates[s, l], allocated[s, l], ready + transition, windows)
                    if found is None:
                        break
                    ready, start = found
                    starts.append(start)
                    ends.append(ready)
                    setups += transition
                else:
                    continue
                break
            else:
                candidate = (setups, len(starts)-len(demand), max(ends), sum(starts))
                best = min(best, candidate) if best is not None else candidate
    data = example(demand)
    calendar_data = data_for(60, 1, windows)
    data["planningHorizon"] = calendar_data["planningHorizon"]
    data["calendar"]["shifts"] = calendar_data["calendar"]["shifts"]
    for l, line in enumerate(data["lines"]):
        for p in line["eligibleProducts"]:
            p["capacityUnitsPerHour"] = int(60 * rates[p["product"], l])
    if best is None:
        with pytest.raises(ApplicationError) as exc:
            checked(data)
        assert exc.value.code == "INFEASIBLE"
    else:
        problem, result, schedule = checked(data)
        assert tuple(run_objectives(problem, result.runs).values()) == best
