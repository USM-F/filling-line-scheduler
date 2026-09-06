"""The supplied six-day calendar has a hard production limit, including Sunday tail."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from filling_scheduler.input import load_document, load_input
from filling_scheduler.problem import prepare_problem
from filling_scheduler.schedule import Schedule
from filling_scheduler.validation import validate_schedule


@pytest.mark.parametrize("excess,exit_code", [(0, 0), (1, 3)])
def test_weekly_capacity_boundary(input_data, tmp_path, monkeypatch, capsys, excess, exit_code):
    monkeypatch.chdir(tmp_path)
    # Keep real lines, speeds and the entire weekly calendar; isolate mandatory G@L5.
    for demand in input_data["demand"]:
        demand["quantityUnits"] = 492480 + excess if demand["product"] == "G" else 0
    source, output = Path("input.json"), Path("schedule.json")
    source.write_text(json.dumps(input_data))
    assert main(["solve", "--input", str(source), "--output", str(output)]) == exit_code
    response = capsys.readouterr().out
    if excess:
        assert not response
        assert all(not p.exists() for p in (output, output.with_suffix(".html"), output.with_suffix(".metrics.json")))
        return
    assert json.loads(response)["status"] == "OPTIMAL"
    problem = prepare_problem(load_input(source))
    schedule = load_document(output, Schedule)
    assert validate_schedule(problem, schedule)["valid"]
    assert problem.available_ticks == 6840
    production = [s for line in schedule.lines for s in line.slots if s.type == "production"]
    assert len(production) == 24
    assert sum(s.quantity for s in production) == 492480
    assert max(s.end for s in production).isoformat() == "2026-08-23T07:00:00+07:00"
    assert all(s.end <= problem.source.planning_horizon.end for s in production)
    # The public validator independently rejects a completion beyond the week.
    raw = schedule.model_dump(mode="json", by_alias=True)
    last = next(line for line in raw["lines"] if line["line"] == "L5")["slots"][-1]
    last["end"] = (datetime.fromisoformat(input_data["planningHorizon"]["end"]) + timedelta(minutes=1)).isoformat()
    assert "HORIZON" in {e["code"] for e in validate_schedule(problem, Schedule.model_validate(raw))["errors"]}
