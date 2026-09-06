import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from filling_scheduler.input import load_document, load_input
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import Schedule
from filling_scheduler.validation import validate_schedule


@pytest.mark.baseline
@pytest.mark.parametrize("decomposition", [False, True])
def test_supplied_baseline(input_path, capsys, decomposition):
    output = Path("output/baseline-decomposed.json" if decomposition else "output/baseline.json")
    assert main(["all", "--input", str(input_path), "--output", str(output),
                 "--time-limit-seconds", "300", "--debug", *(["--decomposition"] if decomposition else [])]) == 0
    response = json.loads(capsys.readouterr().out)
    schedule = load_document(output, Schedule)
    assert validate_schedule(prepare_problem(load_input(input_path)), schedule)["valid"]
    assert sum(schedule.summary.produced_by_product.values()) == 1133892
    assert schedule.summary.total_changeover_minutes == 210
    assert schedule.summary.products_split == 0
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert metrics["settings"]["threads"] == 1
    assert Path(response["html"]).is_file()
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
