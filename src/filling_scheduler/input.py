import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from filling_scheduler.enums import ErrorCode
from filling_scheduler.models import SchedulingInput
from filling_scheduler.errors import ApplicationError


class ObjectPairs(list):
    """Distinguish objects from arrays until duplicate-key paths are checked."""


def materialize(value: Any, path: str = "$") -> Any:
    if isinstance(value, ObjectPairs):
        result = {}
        for key, child in value:
            child_path = f"{path}[{json.dumps(key)}]"
            if key in result:
                raise ApplicationError(
                    ErrorCode.DUPLICATE_JSON_KEY,
                    "Duplicate JSON object key",
                    details=[{"path": child_path, "message": "duplicate key"}],
                )
            result[key] = materialize(child, child_path)
        return result
    if isinstance(value, list):
        return [materialize(child, f"{path}[{index}]") for index, child in enumerate(value)]
    return value


def reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def load_input(path: Path) -> SchedulingInput:
    try:
        contents = path.read_bytes()
    except OSError as exc:
        raise ApplicationError(ErrorCode.INPUT_READ_ERROR, "Cannot read input file") from exc
    try:
        raw = materialize(json.loads(
            contents.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=ObjectPairs,
        ))
    except (ValueError, UnicodeError, RecursionError) as exc:
        details = [{"path": "$", "message": "invalid JSON document"}]
        if isinstance(exc, json.JSONDecodeError):
            details[0].update(line=exc.lineno, column=exc.colno)
        raise ApplicationError(ErrorCode.INVALID_JSON, "Invalid UTF-8 JSON", details=details) from exc
    try:
        return SchedulingInput.model_validate(raw)
    except ValidationError as exc:
        details = [
            {"path": list(error["loc"]), "type": error["type"], "message": error["msg"]}
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        ]
        raise ApplicationError(ErrorCode.SCHEMA_ERROR, "Input does not match schema", details=details) from exc
