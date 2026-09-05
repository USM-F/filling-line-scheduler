import pytest

from filling_scheduler.enums import ErrorCode, ExitCode
from filling_scheduler.errors import ApplicationError
from filling_scheduler.report import write_report


def test_publish_no_overwrite_and_force(tmp_path):
    path = tmp_path / "nested" / "report.json"
    write_report(path, "first\n", force=False)
    with pytest.raises(ApplicationError) as caught:
        write_report(path, "second\n", force=False)
    assert caught.value.code == ErrorCode.OUTPUT_CONFLICT
    assert caught.value.exit_code == ExitCode.ARTIFACT_ERROR
    assert path.read_text() == "first\n"
    write_report(path, "second\n", force=True)
    assert path.read_text() == "second\n"
    assert list(path.parent.iterdir()) == [path]


def test_write_error(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    with pytest.raises(ApplicationError) as caught:
        write_report(path / "report", "{}", force=False)
    assert caught.value.code == ErrorCode.OUTPUT_WRITE_ERROR


def test_force_replaces_symlink_without_modifying_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("original")
    path = tmp_path / "report"
    path.symlink_to(target)
    write_report(path, "report", force=True)
    assert target.read_text() == "original"
    assert not path.is_symlink()
