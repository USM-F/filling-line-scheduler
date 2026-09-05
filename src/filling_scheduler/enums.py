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
    PROBLEM_INVALID = "PROBLEM_INVALID"
    UNSUPPORTED_PRECISION = "UNSUPPORTED_PRECISION"
    INFEASIBLE = "INFEASIBLE"
    NO_INCUMBENT = "NO_INCUMBENT"
    SOLVER_ERROR = "SOLVER_ERROR"
    SCHEDULE_INVALID = "SCHEDULE_INVALID"
    RENDER_ERROR = "RENDER_ERROR"
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
    SOLVE_PASS_COMPLETED = "solve_pass_completed"
    SCHEDULE_VALIDATED = "schedule_validated"
    COMMAND_STARTED = "command_started"
    COMMAND_PARAMETERS = "command_parameters"
    INSPECTION_COMPLETED = "inspection_completed"
    REPORT_WRITTEN = "report_written"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    COMMAND_FAILED = "command_failed"
    COMMAND_COMPLETED = "command_completed"


class StageName(StrEnum):
    PREPARE_PROBLEM = "prepare_problem"
    MILP_BUILD = "milp_build"
    MILP_SOLVE = "milp_solve"
    MATERIALIZE = "materialize"
    VALIDATE_SCHEDULE = "validate_schedule"
    HTML_RENDER = "html_render"
    LOAD_INPUT = "load_input"
    INSPECT_INPUT = "inspect_input"
    JSON_DUMP = "json_dump"


class SolverStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"


class ObjectiveMode(StrEnum):
    LEXICOGRAPHIC = "lexicographic"
    WEIGHTED = "weighted"


class ObjectiveName(StrEnum):
    CHANGEOVER = "changeover_ticks"
    SPLIT = "split_excess"
    MAKESPAN = "makespan_ticks"
    STARTS = "start_sum_ticks"
