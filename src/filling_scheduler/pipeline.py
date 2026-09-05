"""Command pipelines and artifact publication; no solver import during inspect."""

import logging
import platform
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from filling_scheduler.enums import ErrorCode, EventName, ExitCode, StageName
from filling_scheduler.errors import ApplicationError
from filling_scheduler.gantt import render_html
from filling_scheduler.input import load_document, load_input
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.report import encode_report, publish_artifacts
from filling_scheduler.schedule import Schedule, materialize_schedule
from filling_scheduler.stage_timing import timed_stage
from filling_scheduler.validation import validate_schedule

logger = logging.getLogger(__name__)


def inspect_input(problem: SchedulingInput) -> dict[str, Any]:
    return {
        "id": problem.id,
        "planningHorizon": problem.planning_horizon.model_dump(mode="json", by_alias=True),
        "productCount": len(problem.demand),
        "activeProductCount": sum(item.demand_units > 0 for item in problem.demand),
        "lineCount": len(problem.lines),
        "eligiblePairCount": sum(len(line.eligible_products) for line in problem.lines),
        "totalDemandUnits": sum(item.demand_units for item in problem.demand),
    }


def paths_alias(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def check_outputs(outputs: list[Path], inputs: list[Path], *, force: bool) -> None:
    for index, path in enumerate(outputs):
        if any(paths_alias(path, other) for other in inputs + outputs[:index]):
            raise ApplicationError(ErrorCode.CLI_ERROR, "Output paths must be distinct from inputs and each other")
        if not force and (path.exists() or path.is_symlink()):
            raise ApplicationError(ErrorCode.OUTPUT_CONFLICT, "Artifact exists; use --force", ExitCode.ARTIFACT_ERROR)
        if path.is_dir():
            raise ApplicationError(ErrorCode.OUTPUT_WRITE_ERROR, "Output is a directory", ExitCode.ARTIFACT_ERROR)


def prepare_input(path: Path):
    with timed_stage(StageName.LOAD_INPUT):
        source = load_input(path)
    with timed_stage(StageName.PREPARE_PROBLEM):
        return prepare_problem(source)


def checked_html(schedule: Schedule) -> str:
    with timed_stage(StageName.HTML_RENDER):
        try:
            return render_html(schedule)
        except Exception as exc:
            raise ApplicationError(ErrorCode.RENDER_ERROR, "Cannot render schedule", ExitCode.ARTIFACT_ERROR) from exc


def check_schedule(problem, schedule):
    with timed_stage(StageName.VALIDATE_SCHEDULE):
        report = validate_schedule(problem, schedule)
    logger.info(EventName.SCHEDULE_VALIDATED, extra={"fields": report})
    return report


def solve_command(args, run_id: str, log_file: Path) -> dict:
    if args.decomposition or args.workers != 1 or args.work_dir is not None or args.keep_work_dir:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Decomposition and workers are not supported by the monolithic model")
    if not 0 <= args.seed <= 2147483647:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Seed must be between 0 and 2147483647")
    html_path = args.html_output or args.output.with_suffix(".html")
    metrics_path = args.output.with_suffix(".metrics.json")
    native_log = Path(f".logs/{run_id}.highs.log")
    check_outputs([args.output, html_path, metrics_path, native_log], [args.input, log_file], force=args.force)
    started = perf_counter()
    problem = prepare_input(args.input)
    from filling_scheduler.milp import SchedulingMilp
    with timed_stage(StageName.MILP_BUILD):
        model = SchedulingMilp(problem)
    with timed_stage(StageName.MILP_SOLVE):
        result = model.solve(time_limit=args.time_limit_seconds, mip_gap=args.mip_gap,
                             seed=args.seed, threads=args.threads_per_worker, log_path=native_log)
    with timed_stage(StageName.MATERIALIZE):
        schedule = materialize_schedule(problem, result, f"{problem.source.id}-{run_id}")
    report = check_schedule(problem, schedule)
    if not report["valid"]:
        raise ApplicationError(ErrorCode.SCHEDULE_INVALID, "Generated schedule failed independent validation",
                               ExitCode.SCHEDULE_INVALID, details=report["errors"])
    html = checked_html(schedule)
    metrics = {"scheduleId": schedule.schedule_id, "status": result.status, "independently_validated": True,
               "objectives": result.objectives, "passes": result.passes, "model": result.model,
               "solver_elapsed_ms": result.elapsed_ms, "pipeline_elapsed_ms": (perf_counter()-started)*1000,
               "settings": {"time_limit_seconds": args.time_limit_seconds, "mip_gap": args.mip_gap,
                            "threads": args.threads_per_worker, "seed": args.seed},
               "environment": {"python": platform.python_version(), "highspy": version("highspy"),
                               "architecture": platform.machine(), "platform": platform.platform()},
               "highs_log": str(native_log) if native_log.exists() else None, "summary": report["summary"]}
    with timed_stage(StageName.JSON_DUMP):
        publish_artifacts([(metrics_path, encode_report(metrics)), (html_path, html),
                           (args.output, schedule.model_dump_json(by_alias=True, indent=2) + "\n")], force=args.force)
    response = {"status": result.status, "output": str(args.output), "html": str(html_path),
                "metrics": str(metrics_path), "objectives": result.objectives, "summary": report["summary"]}
    logger.info(EventName.REPORT_WRITTEN, extra={"fields": response})
    return response


def validate_command(args) -> dict:
    problem = prepare_input(args.input)
    schedule = load_document(args.schedule, Schedule)
    return check_schedule(problem, schedule)


def render_command(args, log_file: Path) -> dict:
    check_outputs([args.html_output], [args.schedule, log_file], force=args.force)
    schedule = load_document(args.schedule, Schedule)
    html = checked_html(schedule)
    publish_artifacts([(args.html_output, html)], force=args.force)
    return {"html": str(args.html_output), "scheduleId": schedule.schedule_id}
