import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from filling_scheduler.input import load_document, load_input
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import Schedule
from filling_scheduler.validation import validate_schedule


@pytest.mark.baseline
@pytest.mark.parametrize("demand_multiplier", [1, 2], ids=["original", "doubled"])
def test_supplied_baseline(input_path, input_data, capsys, demand_multiplier):
    output = Path("output/baseline.json" if demand_multiplier == 1 else "output/baseline_x2.json")
    if demand_multiplier != 1:
        for item in input_data["demand"]:
            item["quantityUnits"] *= demand_multiplier
        input_path = output.with_suffix(".input.json")
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps(input_data, indent=2) + "\n")
    assert main(["all", "--input", str(input_path), "--output", str(output),
                 "--time-limit-seconds", "300", "--debug"]) == 0
    response = json.loads(capsys.readouterr().out)
    schedule = load_document(output, Schedule)
    assert validate_schedule(prepare_problem(load_input(input_path)), schedule)["valid"]
    assert sum(schedule.summary.produced_by_product.values()) == 1133892 * demand_multiplier
    # Independent lower bounds: 20 products on 13 lines require at least
    # 30 * (20 - 13) = 210 changeover minutes; additional assignments cannot be negative.
    assert schedule.summary.total_changeover_minutes == 210
    assert schedule.summary.products_split == 0
    metrics = json.loads(Path(response["metrics"]).read_text())
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
