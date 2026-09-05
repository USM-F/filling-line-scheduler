import os
from pathlib import Path
import subprocess

import pytest

PROJECT = Path(__file__).parents[2]


def run_guard(path):
    return subprocess.run([str(PROJECT / "scripts/ensure_venv.sh"), "--force"], env={**os.environ, "VENV_DIR": str(path)}, capture_output=True, text=True)


@pytest.mark.parametrize("kind", ["root", "home", "project", "relative", "unmarked", "wrong_owner", "symlink", "ancestor_symlink", "stamp_symlink", "lock_symlink", "wrong_name"])
def test_force_guards(kind, tmp_path):
    venv = tmp_path / ".venv-test"
    venv.mkdir()
    sentinel = venv / "sentinel"
    sentinel.write_text("keep")
    if kind == "root": target = Path("/")
    elif kind == "home": target = Path.home()
    elif kind == "project": target = PROJECT
    elif kind == "relative": target = Path("relative/.venv")
    elif kind == "wrong_name": target = tmp_path
    elif kind == "symlink":
        target = tmp_path / ".venv-link"
        target.symlink_to(venv)
    elif kind == "ancestor_symlink":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        target = link / ".venv-test"
    else:
        target = venv
        if kind == "wrong_owner": (venv / ".fls-owner").write_text("another project")
        if kind in ("stamp_symlink", "lock_symlink"):
            (venv / ".fls-owner").write_text(str(PROJECT))
            link = venv / ".fls-stamp" if kind == "stamp_symlink" else Path(str(venv) + ".lock")
            link.symlink_to(sentinel)
    result = run_guard(target)
    assert result.returncode == 2, result.stderr
    assert "ERROR" in result.stderr
    assert sentinel.read_text() == "keep"
