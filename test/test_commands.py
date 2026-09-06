import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from data_generators import example


@pytest.fixture
def command_case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "input with spaces.json"
    source.write_text(json.dumps(example()))
    return ["solve", "--input", str(source), "--output", "output/schedule.json", "--debug"]


@pytest.mark.parametrize("command", ["solve", "all"])
def test_solve_validate_render(command_case, capsys, command):
    command_case[0] = command
    assert main(command_case) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OPTIMAL"
    metrics = json.loads(Path(result["metrics"]).read_text())
    assert metrics["independently_validated"]
    assert len(metrics["passes"]) == 2
    assert metrics["model"]["eligible_pairs"] == 2
    assert metrics["objectives"]["split_excess"] == 1
    assert Path(metrics["highs_log"]).is_file()
    records = [json.loads(line) for path in Path(".logs").glob("*.jsonl") for line in path.read_text().splitlines()]
    assert records[0]["command"] == command
    inspection = next(r for r in records if r["event"] == "inspection_completed")
    assert inspection["summary"]["totalDemandUnits"] == 150
    stages = [r["stage"] for r in records if r["event"] == "stage_completed"]
    assert stages.index("load_input") < stages.index("prepare_problem") < stages.index("inspect_input")
    assert stages.index("inspect_input") < stages.index("milp_solve") < stages.index("validate_schedule")
    assert stages.index("validate_schedule") < stages.index("html_render") < stages.index("json_dump")
    assert main(["validate", "--input", command_case[2], "--schedule", result["output"]]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["render", "--schedule", result["output"], "--input", command_case[2], "--html-output", "output/other.html"]) == 0
    capsys.readouterr()
    assert Path("output/other.html").read_text() == Path(result["html"]).read_text()
    assert main(["render", "--schedule", result["output"], "--html-output", "output/other.html"]) == 0
    assert json.loads(capsys.readouterr().out)["html"] == "output/other.html"


def test_validation_report_and_format_errors(command_case, capsys):
    assert main(command_case) == 0
    capsys.readouterr()
    path = Path("output/schedule.json")
    raw = json.loads(path.read_text())
    raw["summary"]["slotCount"] = 100
    path.write_text(json.dumps(raw))
    args = ["validate", "--input", command_case[2], "--schedule", str(path)]
    assert main(args) == 7
    assert not json.loads(capsys.readouterr().out)["valid"]
    path.write_text('{"scheduleId": 3}')
    assert main(args) == 2
    assert not capsys.readouterr().out


@pytest.mark.parametrize("existing", ["schedule.json", "schedule.html", "schedule.metrics.json"])
def test_renderer_failure_preserves_outputs(command_case, monkeypatch, existing):
    import filling_scheduler.pipeline as pipeline
    Path("output").mkdir()
    path = Path("output") / existing
    path.write_text("previous")
    def fail(*_):
        raise RuntimeError("injected renderer failure")
    monkeypatch.setattr(pipeline, "render_html", fail)
    assert main(command_case) == 8
    assert path.read_text() == "previous"
    assert list(Path("output").iterdir()) == [path]


@pytest.mark.parametrize("extra", [["--decomposition"], ["--seed", "-1"]])
def test_unsupported_options(command_case, extra):
    assert main([*command_case, *extra]) == 2
    assert not Path("output/schedule.json").exists()


def test_no_incumbent_and_infeasible(command_case, capsys):
    assert main([*command_case, "--time-limit-seconds", "0.000000001"]) == 4
    assert not Path("output/schedule.json").exists()
    Path(command_case[2]).write_text(json.dumps(example({"A": 201})))
    assert main(command_case) == 3
    assert not capsys.readouterr().out


def test_feasible_incumbent_is_published(command_case, monkeypatch, capsys):
    # Simulate a time-limit response around a real native incumbent, without flaky wall-clock races.
    import highspy
    class LimitedHighs(highspy.Highs):
        def getModelStatus(self):
            return highspy.HighsModelStatus.kTimeLimit

        def getInfo(self):
            info = super().getInfo()
            info.mip_dual_bound = -float("inf")
            info.mip_gap = float("inf")
            return info
    monkeypatch.setattr(highspy, "Highs", LimitedHighs)
    assert main(command_case) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "FEASIBLE"
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert len(metrics["passes"]) == 1
    assert not metrics["passes"][0]["proven_optimal"]
    assert metrics["passes"][0]["highs_status"] == "kTimeLimit"


def test_output_path_collisions(command_case):
    assert main([*command_case, "--html-output", "output/schedule.json"]) == 2
    assert main([*command_case, "--html-output", command_case[2]]) == 2
    assert main([*command_case, "--log-file", "output/schedule.metrics.json"]) == 2
    assert not Path("output/schedule.json").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_publication_error(command_case, monkeypatch, existing):
    import filling_scheduler.report as report
    paths = [Path("output") / name for name in ("schedule.json", "schedule.html", "schedule.metrics.json")]
    if existing:
        paths[0].parent.mkdir()
        for path in paths:
            path.write_text("previous-" + path.name)
    original = report.os.replace
    def fail_json(source, target):
        if str(target).endswith("schedule.json"):
            raise OSError("injected publication failure")
        original(source, target)
    monkeypatch.setattr(report.os, "replace", fail_json)
    assert main(command_case) == 8
    if existing:
        assert [p.read_text() for p in paths] == ["previous-" + p.name for p in paths]
        assert set(Path("output").iterdir()) == set(paths)
    else:
        assert list(Path("output").iterdir()) == []


@pytest.mark.parametrize("command", ["solve", "all"])
def test_repeat_solve_replaces_entire_bundle_with_changed_input(command_case, capsys, command):
    command_case[0] = command
    assert main(command_case) == 0
    first = json.loads(capsys.readouterr().out)
    old_id = json.loads(Path(first["output"]).read_text())["scheduleId"]
    Path(command_case[2]).write_text(json.dumps(example({"A": 80})))
    assert main(command_case) == 0
    second = json.loads(capsys.readouterr().out)
    schedule = json.loads(Path(second["output"]).read_text())
    metrics = json.loads(Path(second["metrics"]).read_text())
    html = Path(second["html"]).read_text()
    assert schedule["summary"]["producedByProduct"] == {"A": 80}
    assert schedule["scheduleId"] == metrics["scheduleId"] != old_id
    assert schedule["scheduleId"] in html and old_id not in html
    assert main(["validate", "--input", command_case[2], "--schedule", second["output"]]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    before = {Path(second[k]): Path(second[k]).read_bytes() for k in ("output", "html", "metrics")}
    Path(command_case[2]).write_text(json.dumps(example({"A": 201})))
    assert main(command_case) == 3
    assert not capsys.readouterr().out
    assert all(p.read_bytes() == contents for p, contents in before.items())


@pytest.mark.parametrize("failure,exit_code", [("input", 2), ("schedule", 7)])
def test_all_failure_keeps_previous_bundle(command_case, monkeypatch, capsys, failure, exit_code):
    import filling_scheduler.pipeline as pipeline
    command_case[0] = "all"
    paths = [Path("output") / name for name in ("schedule.json", "schedule.html", "schedule.metrics.json")]
    paths[0].parent.mkdir()
    for path in paths:
        path.write_text("previous-" + path.name)
    if failure == "input":
        Path(command_case[2]).write_text("{}")
    else:
        materialize = pipeline.materialize_schedule

        def corrupt_summary(*args):
            schedule = materialize(*args)
            summary = schedule.summary.model_copy(update={"slot_count": schedule.summary.slot_count + 1})
            return schedule.model_copy(update={"summary": summary})

        monkeypatch.setattr(pipeline, "materialize_schedule", corrupt_summary)
    assert main(command_case) == exit_code
    assert not capsys.readouterr().out
    assert [p.read_text() for p in paths] == ["previous-" + p.name for p in paths]
    assert set(Path("output").iterdir()) == set(paths)
    records = [json.loads(line) for path in Path(".logs").glob("*.jsonl") for line in path.read_text().splitlines()]
    assert not any(r.get("stage") == "html_render" for r in records)
    if failure == "input":
        assert not any(r["event"] == "solve_pass_completed" for r in records)
    else:
        assert next(r for r in records if r["event"] == "schedule_validated")["valid"] is False


def test_thread_count_can_change_between_commands(command_case):
    assert main(command_case) == 0
    assert main([*command_case, "--threads", "2"]) == 0
    assert main([*command_case, "--threads", "1"]) == 0
