import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from filling_scheduler.cli import build_parser, main
from filling_scheduler.enums import ExitCode


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_inspect_and_debug(input_path, tmp_path, capsys):
    log = tmp_path / "run.jsonl"
    report = tmp_path / "report.json"
    assert main(["--debug", "inspect", "--input", str(input_path), "--report", str(report), "--log-file", str(log)]) == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert json.loads(captured.out)["totalDemandUnits"] == 1133892
    assert report.read_text() == captured.out
    records = events(log)
    assert len({record["run_id"] for record in records}) == 1
    assert [record["event"] for record in records].count("inspection_completed") == 1
    assert records[0]["event"] == "command_started"
    assert records[-1]["exit_code"] == 0
    assert {record["stage"] for record in records if "stage" in record} == {"load_input", "inspect_input", "json_dump"}
    assert "INFO inspection_completed" in captured.err


def test_info_omits_debug(input_path, tmp_path, capsys):
    log = tmp_path / "run.jsonl"
    assert main(["inspect", "--input", str(input_path), "--log-file", str(log)]) == 0
    assert {record["level"] for record in events(log)} == {"INFO"}
    assert "DEBUG" not in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    [], ["unknown"], ["inspect"], ["inspect", "--input"],
    ["--log-level", "INVALID", "inspect", "--input", "missing"],
    ["solve", "--input", "x", "--output", "y", "--time-limit-seconds", "nan"],
    ["solve", "--input", "x", "--output", "y", "--time-limit-seconds", "bad"],
    ["solve", "--input", "x", "--output", "y", "--mip-gap", "-1"],
])
def test_argument_errors_logged(args, tmp_path, capsys):
    log = tmp_path / "error.jsonl"
    assert main([*args, "--log-file", str(log)]) == ExitCode.INPUT_ERROR
    captured = capsys.readouterr()
    assert not captured.out
    failure = next(record for record in events(log) if record["event"] == "command_failed")
    assert failure["level"] == "ERROR"
    assert failure["error_code"] == "CLI_ERROR"
    assert events(log)[-1]["exit_code"] == 2


@pytest.mark.parametrize("command,args", [
    ("solve", ["--input", "input.json", "--output", "schedule.json"]),
    ("all", ["--input", "input.json", "--output", "schedule.json"]),
    ("validate", ["--input", "input.json", "--schedule", "schedule.json"]),
    ("render", ["--schedule", "schedule.json", "--html-output", "schedule.html"]),
])
def test_missing_command_inputs(command, args, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([command, *args]) == ExitCode.INPUT_ERROR
    assert not capsys.readouterr().out
    assert not (tmp_path / "schedule.json").exists()
    assert not (tmp_path / "schedule.html").exists()
    records = events(next((tmp_path / ".logs").iterdir()))
    assert records[-1]["error_code"] == "INPUT_READ_ERROR"
    assert any(record["level"] == "ERROR" for record in records)


def test_schema_error_logged_with_path(tmp_path, capsys):
    source = tmp_path / "bad.json"
    source.write_text('{"unexpected": 1}')
    log = tmp_path / "log.jsonl"
    assert main(["inspect", "--input", str(source), "--log-file", str(log), "--debug"]) == 2
    assert not capsys.readouterr().out
    failure = next(record for record in events(log) if record["event"] == "command_failed")
    assert failure["error_code"] == "SCHEMA_ERROR"
    assert any(detail["path"] == ["unexpected"] for detail in failure["details"])
    assert any(record["event"] == "stage_failed" for record in events(log))


def test_report_overwrites_by_default(input_path, tmp_path, capsys):
    report = tmp_path / "report.json"
    log = tmp_path / "log.jsonl"
    report.write_text("previous")
    args = ["inspect", "--input", str(input_path), "--report", str(report), "--log-file", str(log)]
    assert main(args) == 0
    assert report.read_text() == capsys.readouterr().out
    assert json.loads(report.read_text())["lineCount"] == 13


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_protect_input_from_logs_and_reports(input_path, tmp_path, monkeypatch, alias):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "input.json"
    source.write_bytes(input_path.read_bytes())
    target = source
    if alias != "same":
        target = tmp_path / "alias"
        if alias == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    before = source.read_bytes()
    assert main(["inspect", "--input", str(source), "--log-file", str(target)]) == 2
    assert source.read_bytes() == before
    assert main(["inspect", "--input", str(source), "--report", str(target)]) == 2
    assert source.read_bytes() == before


def test_log_open_error(input_path, tmp_path, capsys):
    bad_log = tmp_path / "directory"
    bad_log.mkdir()
    assert main(["inspect", "--input", str(input_path), "--log-file", str(bad_log)]) == 8
    assert "LOG_WRITE_ERROR" in capsys.readouterr().err


def test_unexpected_error_has_traceback(input_path, tmp_path, monkeypatch):
    import filling_scheduler.cli as cli
    def fail(_):
        raise RuntimeError("test failure")
    monkeypatch.setattr(cli, "load_input", fail)
    log = tmp_path / "log.jsonl"
    assert main(["inspect", "--input", str(input_path), "--log-file", str(log)]) == 9
    record = next(record for record in events(log) if record["event"] == "command_failed")
    assert "RuntimeError: test failure" in record["traceback"]


@pytest.mark.parametrize("args", [["--help"], ["inspect", "--help"], ["--version"]])
def test_help_and_version(args, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(args) == 0
    assert capsys.readouterr().out


@pytest.mark.parametrize("command", ["solve", "all"])
def test_solve_defaults(command):
    args = build_parser().parse_args([command, "--input", "i", "--output", "o"])
    assert args.command == command
    assert (args.time_limit_seconds, args.mip_gap, args.seed) == (300, 0, 0)


def test_solve_help_exposes_only_implemented_options(capsys):
    from filling_scheduler.cli import ParserExit
    with pytest.raises(ParserExit):
        build_parser().parse_args(["solve", "--help"])
    help_text = capsys.readouterr().out
    for removed in ("--workers", "--decomposition", "--objective-mode", "--timing-mode", "--force"):
        assert removed not in help_text


@pytest.mark.parametrize("entrypoint", [[sys.executable, "-m", "filling_scheduler"], [str(Path(sys.executable).parent / "filling-scheduler")]])
def test_real_entrypoints(entrypoint, input_path, tmp_path):
    result = subprocess.run([*entrypoint, "inspect", "--input", str(input_path), "--log-file", str(tmp_path / "log.jsonl")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["eligiblePairCount"] == 84
