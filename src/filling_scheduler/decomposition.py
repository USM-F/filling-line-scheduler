"""Exact eligibility components and sequential lexicographic coordination."""

from collections import Counter
from dataclasses import dataclass, replace
import logging
from pathlib import Path
from time import perf_counter

from filling_scheduler.enums import ErrorCode, EventName, ExitCode, ObjectiveName, SolverStatus, StageName
from filling_scheduler.errors import ApplicationError
from filling_scheduler.milp import Run, SchedulingMilp, SolveResult, run_objectives, proven_integer_optimum
from filling_scheduler.problem import Problem
from filling_scheduler.stage_timing import timed_stage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Component:
    component_id: str
    problem: Problem


def build_components(problem: Problem) -> list[Component]:
    graph = {("sku", sku): set() for sku in problem.demand}
    for sku, line in problem.units_per_tick:
        a, b = ("sku", sku), ("line", line)
        graph[a].add(b)
        graph.setdefault(b, set()).add(a)
    groups, seen = [], set()
    for node in graph:
        if node in seen:
            continue
        stack, nodes = [node], set()
        seen.add(node)
        while stack:
            current = stack.pop()
            nodes.add(current)
            for neighbor in graph[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        groups.append((tuple(sorted(v for t, v in nodes if t == "sku")),
                       tuple(sorted(v for t, v in nodes if t == "line"))))
    # Traversal is linear; sorting only supplies stable public IDs and model order.
    result = []
    for index, (skus, lines) in enumerate(sorted(groups), 1):
        sku_set = set(skus)
        subproblem = replace(problem, demand={sku: problem.demand[sku] for sku in skus}, lines=lines,
            units_per_tick={pair: rate for pair, rate in sorted(problem.units_per_tick.items()) if pair[0] in sku_set},
            changeover={(a, b): problem.changeover[a, b] for a in skus for b in skus})
        result.append(Component(f"C{index:02d}", subproblem))
    return result


def merge_runs(problem: Problem, components: list[Component], results: dict[str, list[Run]]) -> list[Run]:
    def conflict(message):
        raise ApplicationError(ErrorCode.MERGE_CONFLICT, message, ExitCode.MERGE_CONFLICT)
    ids = [c.component_id for c in components]
    if len(set(ids)) != len(ids) or set(results) != set(ids):
        conflict("Missing, duplicate or unexpected component result")
    skus = [sku for c in components for sku in c.problem.demand]
    lines = [line for c in components for line in c.problem.lines]
    if Counter(skus) != Counter({sku: 1 for sku in problem.demand}) or len(lines) != len(set(lines)):
        conflict("Component partition overlaps or omits active demand")
    active_lines = {line for _, line in problem.units_per_tick}
    if set(lines) != active_lines:
        conflict("Component partition does not cover eligible lines")
    merged, pairs = [], set()
    for component in components:
        local = component.problem
        expected_pairs = {pair: rate for pair, rate in problem.units_per_tick.items() if pair[0] in local.demand}
        if (local.demand != {sku: problem.demand[sku] for sku in local.demand}
                or local.units_per_tick != expected_pairs
                or set(local.lines) != {line for _, line in expected_pairs}):
            conflict("Component data disagrees with the original partition")
        quantities = Counter()
        for run in results[component.component_id]:
            pair = run.sku, run.line
            if pair not in component.problem.units_per_tick or pair not in problem.units_per_tick or pair in pairs:
                conflict("Duplicate run or assignment outside its component")
            if run.predecessor is not None and run.predecessor not in component.problem.demand:
                conflict("Predecessor belongs to another component")
            pairs.add(pair)
            quantities[run.sku] += run.quantity
            merged.append(run)
        if dict(quantities) != component.problem.demand:
            conflict("Component result does not cover its demand")
    return sorted(merged, key=lambda run: (run.line, run.start, run.sku))


def objective_progress(objective, models, incumbents, records) -> dict:
    """Global proof; makespan needs a matching max bound, not every local optimum.

    An unattempted local makespan has the valid lower bound zero. Its incumbent
    from the preceding objective still supplies a feasible completion time.
    Additive passes retain the requirement to prove every local optimum.
    """
    values = [m.objective_values(v)[objective] if v is not None else None for m, v in zip(models, incumbents)]
    bounds = [r["bound"] if r else None for r in records]
    makespan = objective == ObjectiveName.MAKESPAN
    aggregate = max if makespan else sum
    value = aggregate(values) if values and all(v is not None for v in values) else (0 if not models else None)
    if makespan:
        bound = max((b for b in bounds if b is not None), default=0)
        proven = value is not None and proven_integer_optimum(value, bound)
    else:
        bound = sum(bounds) if all(b is not None for b in bounds) else None
        proven = all(r is not None and r["proven_optimal"] for r in records)
    return {"objective": objective, "value": value, "bound": bound, "proven_optimal": proven,
            "proof_basis": ("global_bounds" if makespan else "component_optima") if proven else None,
            "gap": max(0, value-bound) / max(1, abs(value)) if value is not None and bound is not None else None}


def solve_decomposed(problem: Problem, *, time_limit=300, mip_gap=0, seed=0, threads=1,
                     log_path: Path | None = None, optimize_timing=True) -> SolveResult:
    with timed_stage(StageName.DECOMPOSE):
        components = build_components(problem)
    diagnostics = {"components": [], "stop_reason": None, "time_limit_seconds": time_limit}
    models = []
    for component in components:
        build_started = perf_counter()
        with timed_stage(StageName.MILP_BUILD):
            model = SchedulingMilp(component.problem)
        models.append(model)
        diagnostics["components"].append({"component_id": component.component_id,
            "skus": list(component.problem.demand), "lines": list(component.problem.lines),
            "model": model.model_size(), "build_elapsed_ms": (perf_counter()-build_started)*1000, "passes": []})
    logger.info(EventName.COMPONENTS_BUILT, extra={"fields": {"components": diagnostics["components"]}})
    started = perf_counter()
    deadline = started + time_limit
    best = [None] * len(models)
    global_passes = []
    reason = "optimal"
    try:
        objectives = list(ObjectiveName) if optimize_timing else [ObjectiveName.CHANGEOVER, ObjectiveName.SPLIT]
        for objective in objectives:
            records = [None] * len(models)
            pending = list(range(len(models)))
            gap_reached = False
            while pending and perf_counter() < deadline:
                for position, index in enumerate(pending):
                    remaining = deadline - perf_counter()
                    if remaining <= 0:
                        break
                    component = components[index]
                    history = diagnostics["components"][index]["passes"]
                    attempt = 1 + sum(r["objective"] == objective for r in history)
                    native_log = (log_path.with_name(f"{log_path.stem}.{component.component_id}.{objective}.{attempt}.log")
                                  if log_path is not None else None)
                    with timed_stage(StageName.MILP_SOLVE):
                        result = models[index].solve_pass(objective,
                            time_limit=remaining / (len(pending) - position), incumbent=best[index],
                            mip_gap=mip_gap, seed=seed, threads=threads, log_path=native_log,
                            context={"component_id": component.component_id, "attempt": attempt})
                    best[index] = result.values
                    record = dict(result.record, highs_log=str(native_log) if native_log else None)
                    history.append(record)
                    # Bounds from earlier attempts remain valid for this same objective and feasible set.
                    previous = records[index]
                    if previous and previous["bound"] is not None:
                        record = dict(record, bound=max(previous["bound"], record["bound"]) if record["bound"] is not None else previous["bound"])
                    records[index] = record
                    gap_reached |= bool(mip_gap > 0 and not record["proven_optimal"] and
                                        record["highs_status"] == "kOptimal" and result.values is not None)
                    if objective == ObjectiveName.MAKESPAN and objective_progress(objective, models, best, records)["proven_optimal"]:
                        break
                if objective == ObjectiveName.MAKESPAN:
                    progress = objective_progress(objective, models, best, records)
                    if progress["proven_optimal"]:
                        break
                    # A feasible completion below the global lower bound cannot
                    # be the bottleneck, even if its local minimum is unknown.
                    pending = [i for i, (model, values) in enumerate(zip(models, best))
                               if values is None or not proven_integer_optimum(
                                   model.objective_values(values)[objective], progress["bound"])]
                else:
                    pending = [i for i, record in enumerate(records) if not record or not record["proven_optimal"]]
                if gap_reached:
                    break
            progress = objective_progress(objective, models, best, records)
            global_passes.append(progress)
            if not progress["proven_optimal"]:
                reason = "gap_reached" if gap_reached else "time_limit"
                break
            for index, model in enumerate(models):
                if objective == ObjectiveName.MAKESPAN:
                    # Earlier passes need not minimize the auxiliary M column.
                    # Keep a skipped component's warm start feasible under the cap.
                    makespan_column = next(iter(model.objectives[objective]))
                    best[index][makespan_column] = model.objective_values(best[index])[objective]
                    model.fix_objective(objective, progress["value"], upper_only=True)
                elif objective != ObjectiveName.STARTS:
                    model.fix_objective(objective, round(records[index]["value"]))
        if any(v is None for v in best):
            reason = "no_incumbent"
            raise ApplicationError(ErrorCode.NO_INCUMBENT, "At least one component has no feasible solution", ExitCode.NO_INCUMBENT)
        with timed_stage(StageName.MERGE):
            runs = merge_runs(problem, components, {c.component_id: m.extract(v) for c, m, v in zip(components, models, best)})
        for entry, model, values in zip(diagnostics["components"], models, best):
            entry["solver_objectives"] = model.objective_values(values)
            entry["solver_elapsed_ms"] = sum(p["elapsed_ms"] for p in entry["passes"])
        sizes = [entry["model"] for entry in diagnostics["components"]]
        size = {key: sum(s[key] for s in sizes) for key in ("columns", "rows", "binaries", "integers", "nonzeros", "eligible_pairs", "route_arcs")}
        size["work_windows"] = len(problem.windows)
        size["component_count"] = len(components)
        return SolveResult(runs, SolverStatus.OPTIMAL if reason == "optimal" else SolverStatus.FEASIBLE,
            run_objectives(problem, runs), global_passes, size, (perf_counter()-started)*1000, diagnostics=diagnostics)
    except ApplicationError as exc:
        reason = exc.code.lower()
        exc.details.append({"decomposition": diagnostics, "passes": global_passes})
        raise
    finally:
        diagnostics.update(stop_reason=reason, elapsed_ms=(perf_counter()-started)*1000)
        logger.info(EventName.DECOMPOSITION_COMPLETED, extra={"fields": {**diagnostics, "passes": global_passes}})
