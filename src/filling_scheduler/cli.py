"""CLI boundary: all command outcomes share logging and enum-based exit codes."""

import argparse
import logging
import math
import sys
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

from filling_scheduler import __version__, config
from filling_scheduler.enums import ErrorCode, EventName, ExitCode, StageName, ObjectiveMode
from filling_scheduler.errors import ApplicationError
from filling_scheduler.input import load_input
from filling_scheduler.report import encode_report, write_report
from filling_scheduler.logging_config import close_logging, configure_logging
from filling_scheduler.pipeline import inspect_input, paths_alias, solve_command, validate_command, render_command
from filling_scheduler.stage_timing import timed_stage


class ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ApplicationError(ErrorCode.CLI_ERROR, message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        raise ParserExit(status)


def positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def finite_float(value: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a finite number") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise argparse.ArgumentTypeError("expected a finite positive number" if positive else "expected a finite nonnegative number")
    return result


def common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-file", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Filling-line scheduler", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=__version__)
    common_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=ArgumentParser)
    inspect = commands.add_parser("inspect", help="Inspect the input schema and counts", allow_abbrev=False)
    inspect.add_argument("--input", required=True, type=Path)
    inspect.add_argument("--report", type=Path)
    inspect.add_argument("--force", action="store_true")
    solve = commands.add_parser("solve", help="Schedule production using MILP", allow_abbrev=False)
    solve.add_argument("--input", required=True, type=Path)
    solve.add_argument("--output", required=True, type=Path)
    solve.add_argument("--decomposition", action="store_true")
    solve.add_argument("--workers", type=positive_int, default=config.DEFAULT_WORKERS)
    solve.add_argument("--threads-per-worker", type=positive_int, default=config.DEFAULT_THREADS_PER_WORKER)
    solve.add_argument("--time-limit-seconds", type=lambda value: finite_float(value, positive=True), default=config.DEFAULT_TIME_LIMIT_SECONDS)
    solve.add_argument("--mip-gap", type=finite_float, default=config.DEFAULT_MIP_GAP)
    solve.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    solve.add_argument("--objective-mode", choices=list(ObjectiveMode), default=ObjectiveMode.LEXICOGRAPHIC,
                       help="Objective policy (default: lexicographic)")
    solve.add_argument("--objective-weights", nargs=4, type=finite_float, metavar=("F1", "F2", "F3", "F4"),
                       help="Required in weighted mode: changeover ticks, extra assignments, makespan ticks, start sum ticks")
    solve.add_argument("--html-output", type=Path)
    solve.add_argument("--work-dir", type=Path)
    solve.add_argument("--keep-work-dir", action="store_true")
    solve.add_argument("--force", action="store_true")
    validate = commands.add_parser("validate", help="Independently validate a schedule", allow_abbrev=False)
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--schedule", required=True, type=Path)
    render = commands.add_parser("render", help="Render an offline HTML Gantt", allow_abbrev=False)
    render.add_argument("--schedule", required=True, type=Path)
    render.add_argument("--html-output", required=True, type=Path)
    render.add_argument("--force", action="store_true")
    for command in (inspect, solve, validate, render):
        common_arguments(command)
    return parser


def raw_option(argv: list[str], option: str) -> str | None:
    """Recover logging settings even when the full CLI fails to parse."""
    found = None
    for index, value in enumerate(argv):
        if value.startswith(option + "="):
            found = value.split("=", 1)[1]
        elif value == option and index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            found = argv[index + 1]
    return found


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    run_id = uuid4().hex
    started = perf_counter_ns()
    failure: ApplicationError | None = None
    args: argparse.Namespace | None = None
    parser_status: int | None = None
    try:
        args = build_parser().parse_args(argv)
    except ApplicationError as exc:
        failure = exc
    except ParserExit as exc:
        parser_status = exc.status

    level = raw_option(argv, "--log-level") or config.DEFAULT_LOG_LEVEL
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = config.DEFAULT_LOG_LEVEL
    if "--debug" in argv:
        level = "DEBUG"
    log_file = Path(raw_option(argv, "--log-file") or f".logs/{run_id}.jsonl")
    # Do not let diagnostics append to an input or to a requested report/schedule.
    for option in ("--input", "--schedule", "--report", "--output", "--html-output"):
        value = raw_option(argv, option)
        if value and paths_alias(log_file, Path(value)):
            failure = ApplicationError(ErrorCode.CLI_ERROR, "Log file must be separate from data files")
            log_file = Path(f".logs/{run_id}.jsonl")
            break
    output = raw_option(argv, "--output")
    if output and any(paths_alias(log_file, Path(output).with_suffix(suffix)) for suffix in (".html", ".metrics.json")):
        failure = ApplicationError(ErrorCode.CLI_ERROR, "Log file must be separate from generated artifacts")
        log_file = Path(f".logs/{run_id}.jsonl")
    logger = logging.getLogger("filling_scheduler")
    try:
        try:
            logger = configure_logging(run_id, level, log_file)
        except OSError as exc:
            logger = configure_logging(run_id, level, None)
            failure = ApplicationError(ErrorCode.LOG_WRITE_ERROR, "Cannot open log file", ExitCode.ARTIFACT_ERROR)
            failure.__cause__ = exc
        command = args.command if args is not None else None
        logger.info(EventName.COMMAND_STARTED, extra={"fields": {"command": command}})
        logger.debug(EventName.COMMAND_PARAMETERS, extra={"fields": {"argv": argv}})
        exit_code = ExitCode.SUCCESS
        error_code = None
        try:
            if failure is not None:
                raise failure
            if parser_status is not None:
                exit_code = ExitCode(parser_status)
            elif args is not None and args.command == "inspect":
                if args.report and paths_alias(args.input, args.report):
                    raise ApplicationError(ErrorCode.CLI_ERROR, "Report must be separate from input")
                with timed_stage(StageName.LOAD_INPUT):
                    problem = load_input(args.input)
                with timed_stage(StageName.INSPECT_INPUT):
                    report = inspect_input(problem)
                with timed_stage(StageName.JSON_DUMP):
                    contents = encode_report(report)
                    if args.report:
                        write_report(args.report, contents, force=args.force)
                        logger.info(EventName.REPORT_WRITTEN)
                logger.info(EventName.INSPECTION_COMPLETED, extra={"fields": {"summary": report}})
                sys.stdout.write(contents)
            elif args is not None:
                if args.command == "solve":
                    report = solve_command(args, run_id, log_file)
                elif args.command == "validate":
                    report = validate_command(args)
                else:
                    report = render_command(args, log_file)
                sys.stdout.write(encode_report(report))
                if args.command == "validate" and not report["valid"]:
                    raise ApplicationError(ErrorCode.SCHEDULE_INVALID, "Schedule failed validation", ExitCode.SCHEDULE_INVALID,
                                           details=report["errors"])
        except ApplicationError as exc:
            exit_code, error_code = exc.exit_code, exc.code
            logger.error(EventName.COMMAND_FAILED, exc_info=exc.code == ErrorCode.RENDER_ERROR, extra={"fields": {
                "error_code": exc.code, "exit_code": exc.exit_code,
                "message": str(exc), "details": exc.details,
            }})
        except Exception:
            exit_code, error_code = ExitCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR
            logger.exception(EventName.COMMAND_FAILED, extra={"fields": {
                "error_code": error_code, "exit_code": exit_code,
                "message": "Unexpected internal error",
            }})
        logger.info(EventName.COMMAND_COMPLETED, extra={"fields": {
            "command": command, "exit_code": exit_code, "error_code": error_code,
            "elapsed_ms": (perf_counter_ns() - started) / 1_000_000,
        }})
        return int(exit_code)
    finally:
        close_logging(logger)
