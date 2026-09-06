"""Exercise wrapper path resolution without requiring Docker inside pytest."""
import os
from pathlib import Path
import subprocess


def prepare(work_dir, *args):
    utils = Path(__file__).parents[1] / "scripts/utils.sh"
    result = subprocess.run([
        "bash", "-c", 'set -euo pipefail; source "$1"; shift; prepare_input_mounts "$@"; '
        'printf "%s\\0" "${#FLS_CLI_ARGS[@]}" "${FLS_CLI_ARGS[@]}" "${FLS_INPUT_MOUNTS[@]}"',
        "test", str(utils), *args,
    ], env={**os.environ, "FLS_WORK_DIR": str(work_dir)}, capture_output=True, check=True)
    fields = result.stdout.decode().split("\0")[:-1]
    count = int(fields[0])
    return fields[1:count+1], [v for v in fields[count+1:] if v]


def test_external_inputs_spaced_paths_equals_syntax_and_symlinks(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    external = tmp_path / "external input.json"
    external.write_text("{}")
    link = work / "linked.json"
    link.symlink_to(external)
    args, mounts = prepare(work, "validate", "--input", str(external), "--schedule=linked.json")
    assert args == ["validate", "--input", str(external), f"--schedule={external}"]
    assert mounts == ["-v", f"{external}:{external}:ro"]


def test_relative_parent_input_uses_work_directory_and_preserves_output(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "input.json"
    source.write_text("{}")
    args, mounts = prepare(work, "solve", "--input=../input.json", "--output", "output/result.json", "--debug")
    assert args == ["solve", f"--input={source}", "--output", "output/result.json", "--debug"]
    assert mounts == ["-v", f"{source}:{source}:ro"]


def test_missing_inputs_and_parser_errors_are_left_to_cli(tmp_path):
    for original in (["solve", "--input"], ["solve", "--input", "missing"], ["solve", "--input=missing"], ["--help"]):
        args, mounts = prepare(tmp_path, *original)
        assert args == original
        assert mounts == []
