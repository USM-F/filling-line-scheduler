"""Stable CLI codes and structured logging vocabulary."""

from enum import IntEnum, StrEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    NOT_IMPLEMENTED = 1
    INPUT_ERROR = 2
    INFEASIBLE = 3
    NO_INCUMBENT = 4
    WORKER_FAILURE = 5
    MERGE_CONFLICT = 6
    SCHEDULE_INVALID = 7
    ARTIFACT_ERROR = 8
    INTERNAL_ERROR = 9


class ErrorCode(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    CLI_ERROR = "CLI_ERROR"
    INPUT_READ_ERROR = "INPUT_READ_ERROR"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    OUTPUT_CONFLICT = "OUTPUT_CONFLICT"
    OUTPUT_WRITE_ERROR = "OUTPUT_WRITE_ERROR"
    LOG_WRITE_ERROR = "LOG_WRITE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class EventName(StrEnum):
    COMMAND_STARTED = "command_started"
    COMMAND_PARAMETERS = "command_parameters"
    INSPECTION_COMPLETED = "inspection_completed"
    REPORT_WRITTEN = "report_written"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    COMMAND_FAILED = "command_failed"
    COMMAND_COMPLETED = "command_completed"


class StageName(StrEnum):
    LOAD_INPUT = "load_input"
    INSPECT_INPUT = "inspect_input"
    JSON_DUMP = "json_dump"
