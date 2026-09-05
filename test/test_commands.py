import json
from pathlib import Path

import pytest

from filling_scheduler.cli import main
from test_milp import example


@pytest.fixture
def command_case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "input with spaces.json"
    source.write_text(json.dumps(example()))
    return ["solve", "--input", str(source), "--output", "output/schedule.json", "--debug"]


def test_solve_validate_render(command_case, capsys):
    assert main(command_case) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OPTIMAL"
    metrics = json.loads(Path(result["metrics"]).read_text())
    assert metrics["independently_validated"]
    assert len(metrics["passes"]) == 4
    assert metrics["model"]["eligible_pairs"] == 2
    assert metrics["objectives"]["split_excess"] == 1
    assert Path(metrics["highs_log"]).is_file()
    assert main(["validate", "--input", command_case[2], "--schedule", result["output"]]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["render", "--schedule", result["output"], "--html-output", "output/other.html"]) == 0
    capsys.readouterr()
    assert Path("output/other.html").read_text() == Path(result["html"]).read_text()
    assert main(["render", "--schedule", result["output"], "--html-output", "output/other.html"]) == 8
    assert main(["render", "--schedule", result["output"], "--html-output", "output/other.html", "--force"]) == 0


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
def test_force_and_renderer_failure_preserve_outputs(command_case, monkeypatch, existing):
    import filling_scheduler.pipeline as pipeline
    Path("output").mkdir()
    path = Path("output") / existing
    path.write_text("previous")
    assert main(command_case) == 8
    def fail(_):
        raise RuntimeError("injected renderer failure")
    monkeypatch.setattr(pipeline, "render_html", fail)
    assert main([*command_case, "--force"]) == 8
    assert path.read_text() == "previous"
    assert list(Path("output").iterdir()) == [path]


@pytest.mark.parametrize("extra", [["--decomposition"], ["--workers", "2"], ["--work-dir", "x"], ["--keep-work-dir"], ["--seed", "-1"]])
def test_unsupported_options(command_case, extra):
    assert main([*command_case, *extra]) == 2
    assert not Path("output/schedule.json").exists()


def test_no_incumbent_and_infeasible(command_case, capsys):
    assert main([*command_case, "--time-limit-seconds", "0.000000001"]) == 4
    assert not Path("output/schedule.json").exists()
    Path(command_case[2]).write_text(json.dumps(example({"A": 201})))
    assert main(command_case) == 3
    assert not capsys.readouterr().out


@pytest.mark.parametrize("extra", [[], ["--objective-mode", "weighted", "--objective-weights", "1", "1", "0.1", "0.01"]])
def test_feasible_incumbent_is_published(command_case, monkeypatch, capsys, extra):
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
    assert main([*command_case, *extra]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "FEASIBLE"
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert len(metrics["passes"]) == 1
    assert not metrics["passes"][0]["proven_optimal"]
    assert metrics["passes"][0]["highs_status"] == "kTimeLimit"


def test_output_path_collisions(command_case):
    assert main([*command_case, "--html-output", "output/schedule.json"]) == 2
    assert main([*command_case, "--html-output", command_case[2], "--force"]) == 2
    assert main([*command_case, "--log-file", "output/schedule.metrics.json"]) == 2
    assert not Path("output/schedule.json").exists()


def test_publication_error(command_case, monkeypatch):
    import filling_scheduler.report as report
    original = report.os.link
    def fail_json(source, target):
        if str(target).endswith("schedule.json"):
            raise OSError("injected publication failure")
        original(source, target)
    monkeypatch.setattr(report.os, "link", fail_json)
    assert main(command_case) == 8
    assert not Path("output/schedule.json").exists()
    assert sorted(p.name for p in Path("output").iterdir()) == ["schedule.html", "schedule.metrics.json"]


def test_thread_count_can_change_between_commands(command_case):
    assert main(command_case) == 0
    assert main([*command_case, "--threads-per-worker", "2", "--force"]) == 0
    assert main([*command_case, "--threads-per-worker", "1", "--force"]) == 0


def test_weighted_cli_and_metrics(command_case, capsys):
    assert main([*command_case, "--objective-mode", "weighted", "--objective-weights", "1", "30", "0.1", "0.01"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["objective_mode"] == "weighted"
    weights = response["objective_weights"]
    assert list(weights.values()) == [1, 30, 0.1, 0.01]
    assert response["weighted_value"] == pytest.approx(sum(weights[k]*v for k,v in response["objectives"].items()))
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert metrics["settings"]["objective_mode"] == "weighted"
    assert metrics["settings"]["objective_weights"] == weights
    assert metrics["weighted_value"] == response["weighted_value"]
    assert metrics["independently_validated"]
    assert len(metrics["passes"]) == 1
    assert Path(response["html"]).exists()
    assert main(["validate", "--input", command_case[2], "--schedule", response["output"]]) == 0


@pytest.mark.parametrize("extra", [
    ["--objective-mode", "invalid"],
    ["--objective-mode", "weighted"],
    ["--objective-weights", "1", "1", "1", "1"],
    ["--objective-mode", "weighted", "--objective-weights", "1", "1", "1"],
    *[["--objective-mode", "weighted", "--objective-weights", value, "0", "0", "0"]
      for value in ("0", "-1", "nan", "inf", "bad")],
])
def test_invalid_objective_options(command_case, extra, capsys):
    assert main([*command_case, *extra]) == 2
    assert not capsys.readouterr().out
    assert not Path("output").exists()
    records = [json.loads(line) for path in Path(".logs").glob("*.jsonl") for line in path.read_text().splitlines()]
    assert records[-1]["error_code"] == "CLI_ERROR"


def test_fractional_weighted_gap_is_not_integer_proof(command_case, monkeypatch, capsys):
    import highspy
    class GapHighs(highspy.Highs):
        def getInfo(self):
            info = super().getInfo()
            info.mip_dual_bound = info.objective_function_value - 0.01
            info.mip_gap = 0.01 / abs(info.objective_function_value)
            return info
    monkeypatch.setattr(highspy, "Highs", GapHighs)
    # HiGHS may return kOptimal when a nonzero requested gap is reached.
    assert main([*command_case, "--objective-mode", "weighted", "--objective-weights", "0", "0", "0.1", "0", "--mip-gap", "0.1"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "FEASIBLE"
    metrics = json.loads(Path(response["metrics"]).read_text())
    assert not metrics["passes"][0]["proven_optimal"]
    assert metrics["passes"][0]["gap"] > 0
