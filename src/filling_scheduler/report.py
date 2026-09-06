import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filling_scheduler.enums import ErrorCode, ExitCode
from filling_scheduler.errors import ApplicationError


def encode_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


@dataclass
class StagedArtifact:
    path: Path
    directory: Path
    identity: tuple[int, int] | None = None

    @property
    def temporary(self) -> Path:
        return self.directory / "new"

    @property
    def backup(self) -> Path:
        return self.directory / "previous"

    def is_published(self) -> bool:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return False
        return (info.st_dev, info.st_ino) == self.identity


def restore_artifacts(staged: list[StagedArtifact]) -> list[dict]:
    """Restore the old JSON marker only after its companion files are restored."""
    errors = []
    if len(staged) > 1:
        marker = staged[-1]
        try:
            if marker.is_published():
                marker.path.unlink()
        except OSError as exc:
            # Do not change companions while a published marker remains visible.
            return [{"path": str(marker.path), "backup": str(marker.backup), "message": str(exc)}]
    for artifact in staged:
        try:
            # Only remove a file published by this invocation.
            if artifact.is_published():
                artifact.path.unlink()
            if errors and artifact is staged[-1]:
                break
            if (artifact.backup.exists() or artifact.backup.is_symlink()) and not (
                    artifact.path.exists() or artifact.path.is_symlink()):
                os.link(artifact.backup, artifact.path, follow_symlinks=False)
        except OSError as exc:
            errors.append({"path": str(artifact.path), "backup": str(artifact.backup), "message": str(exc)})
    return errors


def publish_artifacts(artifacts: list[tuple[Path, str]]) -> None:
    """Publish with rollback; the last artifact is the bundle's JSON marker.

    Stage content and backups before changing any destination. For an overwrite,
    withdraw the old marker before replacing its companions, and publish the new
    marker last. Caught failures restore the previous bundle; if restoration
    itself fails, retain backups and leave the marker absent. This is not an
    atomic snapshot for readers opening several files during a write.
    """
    staged = []
    retain_backups = False
    try:
        for path, contents in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            artifact = StagedArtifact(path, Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent)))
            staged.append(artifact)
            with artifact.temporary.open("w", encoding="utf-8") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            info = artifact.temporary.stat()
            artifact.identity = info.st_dev, info.st_ino
            if path.exists() or path.is_symlink():
                # Preserve the directory entry itself, including dangling symlinks.
                os.link(path, artifact.backup, follow_symlinks=False)
        if len(staged) > 1:
            staged[-1].path.unlink(missing_ok=True)
        for artifact in staged:
            os.replace(artifact.temporary, artifact.path)
    except OSError as exc:
        recovery_errors = restore_artifacts(staged)
        retain_backups = bool(recovery_errors)
        raise ApplicationError(ErrorCode.OUTPUT_WRITE_ERROR, "Cannot publish artifacts", ExitCode.ARTIFACT_ERROR,
                               details=recovery_errors) from exc
    except BaseException:
        retain_backups = bool(restore_artifacts(staged))
        raise
    finally:
        if not retain_backups:
            for artifact in staged:
                artifact.temporary.unlink(missing_ok=True)
                artifact.backup.unlink(missing_ok=True)
                artifact.directory.rmdir()
