from typing import Any

from filling_scheduler.enums import ErrorCode, ExitCode


class ApplicationError(Exception):
    """Expected failure with a stable public code and structured field details."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        exit_code: ExitCode = ExitCode.INPUT_ERROR,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details or []
