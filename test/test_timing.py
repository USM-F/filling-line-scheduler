from dataclasses import replace
import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from filling_scheduler.milp import SchedulingMilp, run_objectives
from filling_scheduler.schedule import materialize_schedule
from filling_scheduler.timing import left_shift
from filling_scheduler.validation import validate_schedule
from data_generators import independent, makespan_tradeoff, prepared
from data_generators import example, calendar_example


@pytest.mark.parametrize("data", [example(), example({"A": 0}), example({"A": 3}, units=2),
                                   calendar_example(), independent(), makespan_tradeoff()])
def test_left_shift_preserves_business_objectives_and_feasibility(data):
    problem = prepared(data)
    result = SchedulingMilp(problem).solve(optimize_timing=False)
    assert all(p["objective"] in ("changeover_ticks", "split_excess") for p in result.passes)
    original = result.runs
    result.runs = left_shift(problem, original)
    before, after = run_objectives(problem, original), run_objectives(problem, result.runs)
    assert before["changeover_ticks"] == after["changeover_ticks"]
    assert before["split_excess"] == after["split_excess"]
    assert after["makespan_ticks"] <= before["makespan_ticks"]
    assert after["start_sum_ticks"] <= before["start_sum_ticks"]
    assert sorted((r.sku, r.line, r.quantity, r.duration, r.predecessor) for r in original) == sorted(
        (r.sku, r.line, r.quantity, r.duration, r.predecessor) for r in result.runs)
    assert validate_schedule(problem, materialize_schedule(problem, result, "shift"))["valid"]


def test_shift_actually_removes_idle_time_and_rejects_impossible_runs():
    problem = prepared(example({"A": 40}, lines=1))
    model = SchedulingMilp(problem)
    model.row({model.variables["A", "L1"]["start"]: 1}, 30, 30)
    original = model.solve(optimize_timing=False).runs
    shifted = left_shift(problem, original)
    assert original[0].start == 30
    assert shifted[0].start == 0 and shifted[0].end == 40
    with pytest.raises(ValueError):
        left_shift(problem, [replace(original[0], duration=1000)])


@pytest.mark.parametrize("mode", [None, "none", "exact"])
def test_cli_timing_modes(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.chdir(tmp_path)
    source = Path("input.json")
    source.write_text(json.dumps(makespan_tradeoff()))
    args = ["solve", "--input", str(source), "--output", "out.json"]
    if mode:
        args.extend(["--timing-mode", mode])
    assert main(args) == 0
    response = json.loads(capsys.readouterr().out)
    metrics = json.loads(Path("out.metrics.json").read_text())
    assert response["timing_mode"] == (mode or "heuristic")
    assert response["status"] == "OPTIMAL"
    assert len(metrics["proven_objectives"]) == (4 if mode == "exact" else 2)
    if mode == "none":
        assert metrics["timing"]["before"] == metrics["timing"]["after"]
    if mode is None:
        assert metrics["timing"]["after"]["start_sum_ticks"] <= metrics["timing"]["before"]["start_sum_ticks"]


@pytest.mark.parametrize("mode", ["heuristic", "none"])
def test_weighted_cannot_silently_replace_explicit_time_objectives(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    assert main(["solve", "--input", "absent.json", "--output", "out.json",
                 "--objective-mode", "weighted", "--objective-weights", "1", "1", "1", "1",
                 "--timing-mode", mode]) == 2
    assert not Path("out.json").exists()
