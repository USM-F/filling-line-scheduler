import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filling_scheduler.enums import ErrorCode, ExitCode
from filling_scheduler.errors import ApplicationError


def encode_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_report(path: Path, contents: str, *, force: bool) -> None:
    """Publish a complete report, with race-safe no-overwrite by default."""
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise ApplicationError(ErrorCode.OUTPUT_CONFLICT, "Report exists; use --force", ExitCode.ARTIFACT_ERROR) from exc
    except OSError as exc:
        raise ApplicationError(ErrorCode.OUTPUT_WRITE_ERROR, "Cannot write report", ExitCode.ARTIFACT_ERROR) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_artifacts(artifacts: list[tuple[Path, str]], *, force: bool) -> None:
    """Stage the whole bundle first; caller orders JSON last as the commit marker.

    Each individual file is atomic. A filesystem failure during publication can
    leave a partial bundle, but never a partial JSON file.
    """
    temporary = []
    try:
        for path, contents in artifacts:
            if not force and (path.exists() or path.is_symlink()):
                raise ApplicationError(ErrorCode.OUTPUT_CONFLICT, "Artifact exists; use --force", ExitCode.ARTIFACT_ERROR)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temp = Path(stream.name)
                temporary.append((path, temp))
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        for path, temp in temporary:
            if force:
                os.replace(temp, path)
            else:
                os.link(temp, path)
    except OSError as exc:
        raise ApplicationError(ErrorCode.OUTPUT_WRITE_ERROR, "Cannot publish artifacts", ExitCode.ARTIFACT_ERROR) from exc
    finally:
        for _, temp in temporary:
            temp.unlink(missing_ok=True)
