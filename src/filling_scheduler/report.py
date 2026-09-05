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
