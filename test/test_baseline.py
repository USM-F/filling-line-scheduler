import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from filling_scheduler.input import load_document, load_input
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import Schedule
from filling_scheduler.validation import validate_schedule


@pytest.mark.baseline
@pytest.mark.parametrize("decomposition", [False, True], ids=["monolithic", "decomposed"])
def test_supplied_baseline(input_path, capsys, decomposition):
    output = Path("output/baseline-decomposed.json" if decomposition else "output/baseline.json")
    assert main(["solve", "--input", str(input_path), "--output", str(output),
                 "--time-limit-seconds", "300", "--force", "--debug", *(["--decomposition"] if decomposition else [])]) == 0
    response = json.loads(capsys.readouterr().out)
    schedule = load_document(output, Schedule)
    assert validate_schedule(prepare_problem(load_input(input_path)), schedule)["valid"]
    assert sum(schedule.summary.produced_by_product.values()) == 1133892
    assert schedule.summary.total_changeover_minutes == 210
    assert schedule.summary.products_split == 0
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert metrics["settings"]["threads"] == 1
    assert Path(response["html"]).is_file()
    assert metrics["settings"]["decomposition"] == decomposition
    assert metrics["settings"]["timing_mode"] == "heuristic"
    assert metrics["status"] == "OPTIMAL"
    assert metrics["objectives"]["changeover_ticks"] == 210
    assert metrics["objectives"]["split_excess"] == 0
    assert metrics["proven_objectives"] == ["changeover_ticks", "split_excess"]
    assert len(metrics["passes"]) == 2
    for record, (objective, expected) in zip(metrics["passes"], [("changeover_ticks", 210), ("split_excess", 0)]):
        assert record["objective"] == objective
        assert record["proven_optimal"]
        assert record["value"] == expected
        assert record["bound"] == pytest.approx(expected)
    if decomposition:
        assert [c["model"]["eligible_pairs"] for c in metrics["decomposition"]["components"]] == [72, 10, 1, 1]


@pytest.mark.baseline
def test_baseline_comparison():
    """Both reference runs must prove the required two-objective optimum."""
    paths = [Path("output/baseline.metrics.json"), Path("output/baseline-decomposed.metrics.json")]
    if not all(path.exists() for path in paths):
        pytest.skip("Both baseline artifacts are required for comparison")
    mono, decomposed = [json.loads(path.read_text()) for path in paths]
    for metrics in (mono, decomposed):
        assert metrics["status"] == "OPTIMAL"
        assert metrics["proven_objectives"] == ["changeover_ticks", "split_excess"]
        assert len(metrics["passes"]) == 2
        assert [p["value"] for p in metrics["passes"]] == [210, 0]
        assert all(p["proven_optimal"] for p in metrics["passes"])
    comparison = {"structural_changeover_bound_minutes": 210, "runs": [
        {"mode": mode, "status": m["status"], "objectives": m["objectives"], "passes": m["passes"],
         "timing_mode": m["settings"]["timing_mode"], "proven_objectives": m["proven_objectives"],
         "model": m["model"], "solver_elapsed_ms": m["solver_elapsed_ms"],
         "pipeline_elapsed_ms": m["pipeline_elapsed_ms"], "environment": m["environment"]}
        for mode, m in [("monolithic", mono), ("decomposed", decomposed)]]}
    Path("output/baseline-comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
