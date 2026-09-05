from copy import deepcopy
from dataclasses import replace

import pytest

from filling_scheduler.decomposition import build_components, merge_runs, solve_decomposed
from filling_scheduler.errors import ApplicationError
from filling_scheduler.input import load_input
from filling_scheduler.milp import SchedulingMilp
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import materialize_schedule
from filling_scheduler.validation import validate_schedule
from test_milp import example, calendar_example


def prepared(data):
    return prepare_problem(SchedulingInput.model_validate(data))


def independent(a=30, b=40):
    data = example({"A": a, "B": b})
    for line, sku in zip(data["lines"], "AB"):
        line["eligibleProducts"] = [p for p in line["eligibleProducts"] if p["product"] == sku]
    return data


def makespan_tradeoff():
    # Component A/B prefers A-B for its own makespan (240), but B-A for starts
    # (90 instead of 180). Component C sets global makespan to 300.
    data = example({"A": 120, "B": 60, "C": 240})
    data["planningHorizon"]["end"] = "2026-08-17T13:00:00+00:00"
    data["calendar"]["shifts"] = [{"code": "DAY", "startTime": "08:00", "endTime": "13:00",
                                   "breaks": [{"startTime": "10:00", "endTime": "11:00"}]}]
    for line, skus in zip(data["lines"], ["AB", "C"]):
        line["eligibleProducts"] = [p for p in line["eligibleProducts"] if p["product"] in skus]
    data["changeoverMatrixMinutes"]["A"]["B"] = 30
    data["changeoverMatrixMinutes"]["B"]["A"] = 30
    return data


def test_actual_partition(input_path):
    problem = prepare_problem(load_input(input_path))
    parts = build_components(problem)
    assert [p.component_id for p in parts] == ["C01", "C02", "C03", "C04"]
    assert [list(p.problem.demand) for p in parts] == [list("ABCDFIJKLMPQR"), list("ENOST"), ["G"], ["H"]]
    assert [len(p.problem.units_per_tick) for p in parts] == [72, 10, 1, 1]
    assert [len(p.problem.lines) for p in parts] == [9, 2, 1, 1]
    assert sum(len(SchedulingMilp(p.problem).arcs) for p in parts) == 664
    assert all(p.problem.windows is problem.windows and p.problem.start == problem.start for p in parts)


def test_graph_bridge_zero_demand_idle_line_and_identical_ids():
    data = independent()
    assert len(build_components(prepared(data))) == 2
    data["lines"][1]["eligibleProducts"].append({"product": "A", "capacityUnitsPerHour": 60})
    assert len(build_components(prepared(data))) == 1
    data = example({"A": 30, "B": 40, "Z": 0}, lines=3)
    data["lines"][0]["line"] = "A"
    data["calendar"]["appliesToLines"][0] = "A"
    for line, skus in zip(data["lines"], ["AZ", "BZ", "Z"]):
        line["eligibleProducts"] = [p for p in line["eligibleProducts"] if p["product"] in skus]
    problem = prepared(data)
    parts = build_components(problem)
    assert [p.problem.lines for p in parts] == [("A",), ("L2",)]
    result = solve_decomposed(problem)
    schedule = materialize_schedule(problem, result, "idle")
    assert validate_schedule(problem, schedule)["valid"]
    assert schedule.lines[-1].slots == []
    reordered = deepcopy(data)
    reordered["lines"].reverse()
    reordered["demand"].reverse()
    for line in reordered["lines"]:
        line["eligibleProducts"].reverse()
    assert [(p.component_id, p.problem.demand, p.problem.lines, p.problem.units_per_tick) for p in parts] == [
        (p.component_id, p.problem.demand, p.problem.lines, p.problem.units_per_tick) for p in build_components(prepared(reordered))]


@pytest.mark.parametrize("data", [independent(), example(), example({"A": 0}),
    example({"A": 3, "Z": 0}, units=2), calendar_example(), makespan_tradeoff()])
def test_matches_monolithic_four_objectives(data):
    problem = prepared(data)
    mono = SchedulingMilp(problem).solve()
    decomposed = solve_decomposed(problem)
    assert decomposed.status == mono.status == "OPTIMAL"
    assert decomposed.objectives == mono.objectives
    assert all(p["proven_optimal"] for p in decomposed.passes)
    assert validate_schedule(problem, materialize_schedule(problem, decomposed, "differential"))["valid"]


def test_global_makespan_releases_short_component():
    problem = prepared(makespan_tradeoff())
    local = SchedulingMilp(build_components(problem)[0].problem).solve()
    result = solve_decomposed(problem)
    assert local.objectives["makespan_ticks"] == 240
    assert local.objectives["start_sum_ticks"] == 180
    assert result.objectives == dict(changeover_ticks=30, split_excess=0, makespan_ticks=300, start_sum_ticks=90)
    assert max(r.end for r in result.runs if r.line == "L1") == 270
    assert result.passes[2]["bound"] == 300


def test_split_stays_inside_component():
    data = example({"A": 150, "B": 10}, lines=3)
    for line, skus in zip(data["lines"], ["A", "A", "B"]):
        line["eligibleProducts"] = [p for p in line["eligibleProducts"] if p["product"] in skus]
    problem = prepared(data)
    result = solve_decomposed(problem)
    assert sorted(r.quantity for r in result.runs if r.sku == "A") == [75, 75]
    assert result.objectives == SchedulingMilp(problem).solve().objectives


def test_infeasible_component():
    with pytest.raises(ApplicationError) as exc:
        solve_decomposed(prepared(independent(b=101)))
    assert exc.value.exit_code == 3
    assert exc.value.details[-1]["decomposition"]["stop_reason"] == "infeasible"


@pytest.mark.parametrize("missing", [False, True])
def test_shared_deadline_with_and_without_all_incumbents(monkeypatch, missing):
    import filling_scheduler.decomposition as module
    clock = [0.0]
    monkeypatch.setattr(module, "perf_counter", lambda: clock[0])
    original = SchedulingMilp.solve_pass
    allocations = []
    def limited(self, objective, **options):
        allocations.append(options["time_limit"])
        result = original(self, objective, **{**options, "time_limit": 10})
        clock[0] += options["time_limit"]
        result.record.update(proven_optimal=False, highs_status="kTimeLimit")
        if missing and len(allocations) == 2:
            result.values = None
        return result
    monkeypatch.setattr(SchedulingMilp, "solve_pass", limited)
    problem = prepared(independent())
    if missing:
        with pytest.raises(ApplicationError) as exc:
            solve_decomposed(problem, time_limit=10)
        assert exc.value.exit_code == 4
    else:
        result = solve_decomposed(problem, time_limit=10)
        assert result.status == "FEASIBLE"
        assert len(result.passes) == 1
        assert validate_schedule(problem, materialize_schedule(problem, result, "limited"))["valid"]
    assert allocations == [5, 5]


def test_retry_remaining_budget_and_context(monkeypatch, tmp_path):
    original = SchedulingMilp.solve_pass
    calls = []
    def interrupted_once(self, objective, **options):
        calls.append(options)
        result = original(self, objective, **options)
        if len(calls) == 1:
            result.record.update(proven_optimal=False, highs_status="kTimeLimit", bound=-1)
        return result
    monkeypatch.setattr(SchedulingMilp, "solve_pass", interrupted_once)
    result = solve_decomposed(prepared(independent()), log_path=tmp_path / "run.highs.log")
    assert result.status == "OPTIMAL"
    assert calls[2]["incumbent"] is not None
    assert calls[2]["context"] == {"component_id": "C01", "attempt": 2}
    assert len({c["log_path"] for c in calls}) == len(calls)
    assert all(c["log_path"].exists() for c in calls)


def test_requested_gap_stops_before_next_objective(monkeypatch):
    original = SchedulingMilp.solve_pass
    def gap(self, objective, **options):
        result = original(self, objective, **options)
        result.record.update(proven_optimal=False, highs_status="kOptimal", gap=0.1)
        return result
    monkeypatch.setattr(SchedulingMilp, "solve_pass", gap)
    result = solve_decomposed(prepared(independent()), mip_gap=0.1)
    assert result.status == "FEASIBLE"
    assert result.diagnostics["stop_reason"] == "gap_reached"
    assert len(result.passes) == 1


def test_later_pass_limit_keeps_earlier_solutions(monkeypatch):
    import filling_scheduler.decomposition as module
    clock = [0.0]
    monkeypatch.setattr(module, "perf_counter", lambda: clock[0])
    original = SchedulingMilp.solve_pass
    def limited(self, objective, **options):
        if objective == "split_excess":
            clock[0] = 100
            return original(self, objective, **{**options, "time_limit": 0})
        return original(self, objective, **options)
    monkeypatch.setattr(SchedulingMilp, "solve_pass", limited)
    problem = prepared(independent())
    result = solve_decomposed(problem, time_limit=10)
    assert result.status == "FEASIBLE"
    assert len(result.passes) == 2
    assert result.passes[0]["proven_optimal"]
    assert not result.passes[1]["proven_optimal"]
    assert validate_schedule(problem, materialize_schedule(problem, result, "saved"))["valid"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "foreign", "quantity", "predecessor", "partition", "lines", "demand", "rates"])
def test_merge_rejects_corrupt_results(mutation):
    problem = prepared(independent())
    parts = build_components(problem)
    results = {c.component_id: SchedulingMilp(c.problem).solve().runs for c in parts}
    if mutation == "missing":
        del results["C02"]
    elif mutation == "extra":
        results["unknown"] = []
    elif mutation == "duplicate":
        results["C01"] *= 2
    elif mutation == "foreign":
        results["C01"] = results["C02"]
    elif mutation == "quantity":
        results["C01"] = [replace(results["C01"][0], quantity=1)]
    elif mutation == "predecessor":
        results["C01"] = [replace(results["C01"][0], predecessor="B")]
    elif mutation == "partition":
        parts[1] = replace(parts[1], problem=parts[0].problem)
    elif mutation == "demand":
        parts[0] = replace(parts[0], problem=replace(parts[0].problem, demand={"A": 1}))
    elif mutation == "rates":
        parts[0] = replace(parts[0], problem=replace(parts[0].problem, units_per_tick={("A", "L1"): 2}))
    else:
        parts[0] = replace(parts[0], problem=replace(parts[0].problem, lines=("unknown",)))
    with pytest.raises(ApplicationError) as exc:
        merge_runs(problem, parts, results)
    assert exc.value.exit_code == 6
