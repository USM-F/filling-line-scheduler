"""Command pipelines and artifact publication; no solver import during inspect."""

import logging
import platform
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from filling_scheduler.enums import ErrorCode, EventName, ExitCode, StageName, ObjectiveName, TimingMode, ObjectiveMode, AdditionalObjectiveName
from filling_scheduler.errors import ApplicationError
from filling_scheduler.gantt import render_html
from filling_scheduler.input import load_document, load_input
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.report import encode_report, publish_artifacts
from filling_scheduler.schedule import Schedule, materialize_schedule
from filling_scheduler.stage_timing import timed_stage
from filling_scheduler.validation import validate_schedule, changeover_calendar_usage

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


def check_outputs(outputs: list[Path], inputs: list[Path]) -> None:
    for index, path in enumerate(outputs):
        if any(paths_alias(path, other) for other in inputs + outputs[:index]):
            raise ApplicationError(ErrorCode.CLI_ERROR, "Output paths must be distinct from inputs and each other")
        if path.is_dir():
            raise ApplicationError(ErrorCode.OUTPUT_WRITE_ERROR, "Output is a directory", ExitCode.ARTIFACT_ERROR)


def prepare_input(path: Path):
    with timed_stage(StageName.LOAD_INPUT):
        source = load_input(path)
    with timed_stage(StageName.PREPARE_PROBLEM):
        return prepare_problem(source)


def checked_html(schedule: Schedule, problem=None) -> str:
    with timed_stage(StageName.HTML_RENDER):
        try:
            return render_html(schedule, problem)
        except Exception as exc:
            raise ApplicationError(ErrorCode.RENDER_ERROR, "Cannot render schedule", ExitCode.ARTIFACT_ERROR) from exc


def check_schedule(problem, schedule):
    with timed_stage(StageName.VALIDATE_SCHEDULE):
        report = validate_schedule(problem, schedule)
    logger.info(EventName.SCHEDULE_VALIDATED, extra={"fields": report})
    return report


def solve_command(args, run_id: str, log_file: Path) -> dict:
    from filling_scheduler.milp import SchedulingMilp, run_objectives
    from filling_scheduler.timing import left_shift
    from filling_scheduler.milp import validate_objective_options
    mode, weights = validate_objective_options(args.objective_mode, args.objective_weights, args.working_changeover_weight)
    weight_summary = dict(zip(ObjectiveName, weights)) if weights is not None else None
    timing_mode = args.timing_mode or (TimingMode.HEURISTIC if mode == ObjectiveMode.LEXICOGRAPHIC else TimingMode.EXACT)
    if mode == ObjectiveMode.WEIGHTED and timing_mode != TimingMode.EXACT:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Weighted mode requires exact timing")
    if not 0 <= args.seed <= 2147483647:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Seed must be between 0 and 2147483647")
    html_path = args.html_output or args.output.with_suffix(".html")
    metrics_path = args.output.with_suffix(".metrics.json")
    native_log = Path(f".logs/{run_id}.highs.log")
    check_outputs([args.output, html_path, metrics_path, native_log], [args.input, log_file])
    started = perf_counter()
    problem = prepare_input(args.input)
    with timed_stage(StageName.INSPECT_INPUT):
        inspection = inspect_input(problem.source)
    logger.info(EventName.INSPECTION_COMPLETED, extra={"fields": {"summary": inspection}})
    with timed_stage(StageName.MILP_BUILD):
        model = SchedulingMilp(problem, working_changeover_weight=args.working_changeover_weight)
    with timed_stage(StageName.MILP_SOLVE):
        result = model.solve(time_limit=args.time_limit_seconds, mip_gap=args.mip_gap,
                             seed=args.seed, threads=args.threads, log_path=native_log,
                             objective_mode=mode, objective_weights=weights,
                             optimize_timing=timing_mode == TimingMode.EXACT)
    timing = {"mode": timing_mode, "before": dict(result.objectives), "elapsed_ms": 0}
    if timing_mode == TimingMode.HEURISTIC:
        shift_started = perf_counter()
        with timed_stage(StageName.LEFT_SHIFT):
            result.runs = left_shift(problem, result.runs)
        result.objectives = run_objectives(problem, result.runs)
        timing["elapsed_ms"] = (perf_counter()-shift_started)*1000
    timing["after"] = dict(result.objectives)
    with timed_stage(StageName.MATERIALIZE):
        schedule = materialize_schedule(problem, result, f"{problem.source.id}-{run_id}")
    report = check_schedule(problem, schedule)
    if not report["valid"]:
        raise ApplicationError(ErrorCode.SCHEDULE_INVALID, "Generated schedule failed independent validation",
                               ExitCode.SCHEDULE_INVALID, details=report["errors"])
    changeovers = changeover_calendar_usage(problem, schedule)
    if args.working_changeover_weight and changeovers["working_changeover_minutes"] != result.objectives[AdditionalObjectiveName.WORKING_CHANGEOVER] * problem.precision:
        raise ApplicationError(ErrorCode.SOLVER_ERROR, "Working changeover objective disagrees with physical slots", ExitCode.INTERNAL_ERROR)
    html = checked_html(schedule, problem)
    metrics = {"scheduleId": schedule.schedule_id, "status": result.status, "independently_validated": True,
               "objectives": result.objectives, "passes": result.passes, "model": result.model,
               "solver_elapsed_ms": result.elapsed_ms, "pipeline_elapsed_ms": (perf_counter()-started)*1000,
               "settings": {"time_limit_seconds": args.time_limit_seconds, "mip_gap": args.mip_gap,
                            "threads": args.threads, "seed": args.seed},
               "environment": {"python": platform.python_version(), "highspy": version("highspy"),
                               "architecture": platform.machine(), "platform": platform.platform()},
               "highs_log": str(native_log) if native_log.exists() else None, "summary": report["summary"]}
    metrics["timing"] = timing
    metrics["settings"]["timing_mode"] = timing_mode
    metrics["makespan_minutes"] = max((r.end for r in result.runs), default=0) * problem.precision
    metrics["proven_objectives"] = [p["objective"] for p in result.passes if p["proven_optimal"]]
    metrics["settings"].update(objective_mode=mode, objective_weights=weight_summary,
                               working_changeover_weight=args.working_changeover_weight)
    metrics.update(weighted_value=result.weighted_value, changeover_analysis=changeovers)
    with timed_stage(StageName.JSON_DUMP):
        publish_artifacts([(metrics_path, encode_report(metrics)), (html_path, html),
                           (args.output, schedule.model_dump_json(by_alias=True, indent=2) + "\n")])
    response = {"status": result.status, "timing_mode": timing_mode, "proven_objectives": metrics["proven_objectives"],
                "output": str(args.output), "html": str(html_path), "metrics": str(metrics_path),
                "objectives": result.objectives, "makespan_minutes": metrics["makespan_minutes"],
                "summary": report["summary"]}
    response.update(objective_mode=mode, objective_weights=weight_summary, weighted_value=result.weighted_value,
                    working_changeover_weight=args.working_changeover_weight)
    logger.info(EventName.REPORT_WRITTEN, extra={"fields": response})
    return response


def validate_command(args) -> dict:
    problem = prepare_input(args.input)
    schedule = load_document(args.schedule, Schedule)
    return check_schedule(problem, schedule)


def render_command(args, log_file: Path) -> dict:
    inputs = [args.schedule, log_file] + ([args.input] if args.input else [])
    check_outputs([args.html_output], inputs)
    schedule = load_document(args.schedule, Schedule)
    problem = prepare_input(args.input) if args.input else None
    html = checked_html(schedule, problem)
    publish_artifacts([(args.html_output, html)])
    return {"html": str(args.html_output), "scheduleId": schedule.schedule_id}
