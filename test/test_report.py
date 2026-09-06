import pytest

from filling_scheduler.enums import ErrorCode
from filling_scheduler.errors import ApplicationError
from filling_scheduler.report import write_report, publish_artifacts


def test_publish_overwrites_by_default(tmp_path):
    path = tmp_path / "nested" / "report.json"
    write_report(path, "first\n")
    write_report(path, "second\n")
    assert path.read_text() == "second\n"
    assert list(path.parent.iterdir()) == [path]


def test_write_error(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    with pytest.raises(ApplicationError) as caught:
        write_report(path / "report", "{}")
    assert caught.value.code == ErrorCode.OUTPUT_WRITE_ERROR


def test_overwrite_replaces_symlink_without_modifying_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("original")
    path = tmp_path / "report"
    path.symlink_to(target)
    write_report(path, "report")
    assert target.read_text() == "original"
    assert not path.is_symlink()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_bundle_failure_restores_complete_previous_state(tmp_path, monkeypatch, existing, fail_at):
    import filling_scheduler.report as report
    paths = [tmp_path / name for name in ("metrics.json", "schedule.html", "schedule.json")]
    if existing:
        for path in paths:
            path.write_text("old-" + path.name)
    operation = report.os.replace

    def fail_publication(source, target, **options):
        if source.name == "new":
            # Old JSON must be withdrawn before replacing ANY companion.
            assert not paths[-1].exists()
            if target == paths[fail_at]:
                raise OSError("injected publication failure")
        return operation(source, target, **options)

    monkeypatch.setattr(report.os, "replace", fail_publication)
    with pytest.raises(ApplicationError) as exc:
        publish_artifacts([(p, "new-" + p.name) for p in paths])
    assert exc.value.code == ErrorCode.OUTPUT_WRITE_ERROR
    assert not exc.value.details
    if existing:
        assert [p.read_text() for p in paths] == ["old-" + p.name for p in paths]
        assert set(tmp_path.iterdir()) == set(paths)
    else:
        assert list(tmp_path.iterdir()) == []


def test_bundle_overwrite_success_and_recovery_of_symlinks(tmp_path, monkeypatch):
    import filling_scheduler.report as report
    original = tmp_path / "original"
    original.write_text("preserve")
    paths = [tmp_path / "view.html", tmp_path / "schedule.json"]
    paths[0].symlink_to(original)
    paths[1].symlink_to(tmp_path / "missing")
    replace = report.os.replace

    def fail_json(source, target):
        if target == paths[1]:
            raise OSError("injected")
        return replace(source, target)

    monkeypatch.setattr(report.os, "replace", fail_json)
    with pytest.raises(ApplicationError):
        publish_artifacts([(p, "new") for p in paths])
    assert all(p.is_symlink() for p in paths)
    assert original.read_text() == "preserve"
    assert paths[1].readlink() == tmp_path / "missing"
    monkeypatch.setattr(report.os, "replace", replace)
    publish_artifacts([(p, "new") for p in paths])
    assert all(not p.is_symlink() and p.read_text() == "new" for p in paths)
    assert original.read_text() == "preserve"
    assert len(list(tmp_path.iterdir())) == 3


def test_failed_rollback_keeps_backups_and_withholds_json(tmp_path, monkeypatch):
    import filling_scheduler.report as report
    paths = [tmp_path / "view.html", tmp_path / "schedule.json"]
    for path in paths:
        path.write_text("old")
    replace, link = report.os.replace, report.os.link

    def fail_publish(source, target):
        if target == paths[1]:
            raise OSError("cannot publish JSON")
        return replace(source, target)

    def fail_restore(source, target, **options):
        if source.name == "previous" and target == paths[0]:
            raise OSError("cannot restore HTML")
        return link(source, target, **options)

    monkeypatch.setattr(report.os, "replace", fail_publish)
    monkeypatch.setattr(report.os, "link", fail_restore)
    with pytest.raises(ApplicationError) as exc:
        publish_artifacts([(p, "new") for p in paths])
    assert exc.value.details[0]["path"] == str(paths[0])
    assert not paths[1].exists()
    backups = list(tmp_path.glob(".*/previous"))
    assert len(backups) == 2
    assert all(p.read_text() == "old" for p in backups)


def test_interrupt_restores_bundle(tmp_path, monkeypatch):
    import filling_scheduler.report as report
    paths = [tmp_path / "view.html", tmp_path / "schedule.json"]
    for path in paths:
        path.write_text("old")
    replace = report.os.replace

    def interrupt_after_replace(source, target):
        replace(source, target)
        if target == paths[-1]:
            raise KeyboardInterrupt

    monkeypatch.setattr(report.os, "replace", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt):
        publish_artifacts([(p, "new") for p in paths])
    assert all(p.read_text() == "old" for p in paths)
    assert set(tmp_path.iterdir()) == set(paths)
