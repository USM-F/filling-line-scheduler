"""One integrated split/route/event-calendar MILP; native HiGHS is loaded lazily."""

from dataclasses import dataclass
import logging
import math
from pathlib import Path
from time import perf_counter

from filling_scheduler.enums import ErrorCode, EventName, ExitCode, ObjectiveName, ObjectiveMode, SolverStatus, AdditionalObjectiveName
from filling_scheduler.errors import ApplicationError
from filling_scheduler.problem import Problem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Run:
    sku: str
    line: str
    quantity: int
    duration: int
    start: int
    end: int
    predecessor: str | None
    setup_start: int | None = None


@dataclass
class SolveResult:
    runs: list[Run]
    status: SolverStatus
    objectives: dict[str, int]
    passes: list[dict]
    model: dict[str, int]
    elapsed_ms: float
    weighted_value: float | None = None


@dataclass
class PassResult:
    values: list[float] | None
    record: dict


def validate_objective_options(mode, weights, working_changeover_weight=0):
    try:
        mode = ObjectiveMode(mode)
    except ValueError as exc:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Unknown objective mode") from exc
    if not math.isfinite(working_changeover_weight) or working_changeover_weight < 0:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Working changeover weight must be finite and nonnegative")
    if mode == ObjectiveMode.LEXICOGRAPHIC:
        if working_changeover_weight:
            raise ApplicationError(ErrorCode.CLI_ERROR, "Working changeover penalty requires weighted mode")
        if weights is not None:
            raise ApplicationError(ErrorCode.CLI_ERROR, "Objective weights require weighted mode")
        return mode, None
    if weights is None or len(weights) != 4:
        raise ApplicationError(ErrorCode.CLI_ERROR, "Weighted mode requires four explicit objective weights")
    if any(not math.isfinite(w) or w < 0 for w in weights) or not any(weights):
        raise ApplicationError(ErrorCode.CLI_ERROR, "Weights must be finite, nonnegative, and not all zero")
    return mode, tuple(weights)


def proven_integer_optimum(value: float, bound: float) -> bool:
    return math.isfinite(bound) and math.ceil(bound - 1e-6) >= round(value)


class SchedulingMilp:
    def __init__(self, problem: Problem, *, working_changeover_weight: float = 0) -> None:
        self.problem = problem
        if not math.isfinite(working_changeover_weight) or working_changeover_weight < 0:
            raise ApplicationError(ErrorCode.CLI_ERROR, "Working changeover weight must be finite and nonnegative")
        self.working_changeover_weight = working_changeover_weight
        self.working_changeover_objective: dict[int, float] = {}
        self.names: list[str] = []
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.integer: list[int] = []
        self.rows: list[dict[int, float]] = []
        self.row_lower: list[float] = []
        self.row_upper: list[float] = []
        self.variables: dict[tuple[str, str], dict[str, int]] = {}
        self.arcs: dict[tuple[str, str, str], int] = {}
        self.objectives: dict[ObjectiveName, dict[int, float]] = {name: {} for name in ObjectiveName}
        self.build()
        if working_changeover_weight:
            self.build_setup_times()

    def variable(self, name: str, upper: float, *, integer: bool = True) -> int:
        index = len(self.names)
        self.names.append(name)
        self.lower.append(0)
        self.upper.append(upper)
        self.integer.append(int(integer))
        return index

    def row(self, coefficients: dict[int, float], lower: float = -math.inf, upper: float = math.inf) -> None:
        self.rows.append({index: value for index, value in coefficients.items() if value})
        self.row_lower.append(lower)
        self.row_upper.append(upper)

    def build(self) -> None:
        problem = self.problem
        T = problem.horizon
        makespan = self.variable("makespan", T)
        self.objectives[ObjectiveName.MAKESPAN][makespan] = 1
        for (sku, line), units in problem.units_per_tick.items():
            demand = problem.demand[sku]
            n, d = units.numerator, units.denominator
            # Each pause can leave less than one whole unit of unused capacity.
            loss = max(0, len(problem.windows) - 1) * (d - 1)
            pmax = min((d * demand + loss + n - 1) // n, problem.available_ticks)
            qmax = (units * problem.available_ticks if d == 1 else
                    sum(n * (w.end - w.start) // d for w in problem.windows))
            variables = {
                name: self.variable(f"{name}[{sku},{line}]", upper)
                for name, upper in [("assign", 1), ("quantity", min(demand, qmax)),
                                    ("duration", pmax), ("start", T), ("end", T), ("first", 1), ("last", 1)]
            }
            self.variables[sku, line] = variables
            y, q, p, s, c = (variables[name] for name in ("assign", "quantity", "duration", "start", "end"))
            self.row({q: 1, y: -demand}, upper=0)
            self.row({q: 1, y: -1}, lower=0)
            self.row({q: d, p: -n}, upper=0)
            self.row({q: d, p: -n, y: n - 1 + loss}, lower=0)
            self.row({p: 1, y: -pmax}, upper=0)
            self.row({c: 1, s: -1, p: -1}, lower=0)
            self.row({makespan: 1, c: -1}, lower=0)
            self.objectives[ObjectiveName.SPLIT][y] = 1
            self.objectives[ObjectiveName.STARTS][s] = 1
            starts, ends = {y: -1}, {y: -1}
            wall_start, wall_end, productive = {s: 1}, {c: 1}, {p: -1}
            window_variables = []
            for index, window in enumerate(problem.windows):
                length = window.end - window.start
                zs = self.variable(f"start_window[{sku},{line},{index}]", 1)
                ze = self.variable(f"end_window[{sku},{line},{index}]", 1)
                sigma = self.variable(f"start_offset[{sku},{line},{index}]", length - 1, integer=False)
                kappa = self.variable(f"end_offset[{sku},{line},{index}]", length, integer=False)
                window_variables.append((zs, ze, sigma, kappa))
                self.row({sigma: 1, zs: -(length - 1)}, upper=0)
                self.row({kappa: 1, ze: -length}, upper=0)
                self.row({kappa: 1, ze: -1}, lower=0)
                starts[zs], ends[ze] = 1, 1
                wall_start.update({zs: -window.start, sigma: -1})
                wall_end.update({ze: -window.start, kappa: -1})
                productive.update({ze: window.cumulative, kappa: 1,
                                   zs: -window.cumulative, sigma: -1})
            for coefficients in (starts, ends, wall_start, wall_end, productive):
                self.row(coefficients, 0, 0)
            if d != 1:
                self.build_fractional_capacity(sku, line, n, d, window_variables)
        for sku, demand in problem.demand.items():
            self.row({v["quantity"]: 1 for (product, _), v in self.variables.items() if product == sku}, demand, demand)
        for line in problem.lines:
            skus = [sku for sku, candidate in self.variables if candidate == line]
            used = self.variable(f"line_used[{line}]", 1)
            firsts, lasts = {used: -1}, {used: -1}
            for sku in skus:
                v = self.variables[sku, line]
                firsts[v["first"]], lasts[v["last"]] = 1, 1
                for other in skus:
                    if other == sku:
                        continue
                    arc = self.variable(f"follows[{sku},{other},{line}]", 1)
                    self.arcs[sku, other, line] = arc
                    changeover = problem.changeover[sku, other]
                    self.objectives[ObjectiveName.CHANGEOVER][arc] = changeover
                    self.row({self.variables[other, line]["start"]: 1, v["end"]: -1,
                              arc: -(T + changeover)}, lower=-T)
            self.row(firsts, 0, 0)
            self.row(lasts, 0, 0)
            for sku in skus:
                v = self.variables[sku, line]
                incoming, outgoing = {v["first"]: 1, v["assign"]: -1}, {v["last"]: 1, v["assign"]: -1}
                for other in skus:
                    if other != sku:
                        incoming[self.arcs[other, sku, line]] = 1
                        outgoing[self.arcs[sku, other, line]] = 1
                self.row(incoming, 0, 0)
                self.row(outgoing, 0, 0)

    def build_fractional_capacity(self, sku, line, n, d, windows) -> None:
        """Whole units per physical work window, with exact rational speed n/d.

        h marks windows traversed by the run and is continuous: its recurrence
        in binary start/end selections already makes it 0 or 1. Nonfinal windows
        fill floor(n*t/d) units; the final window uses the shortest number of ticks.
        No fractional unit of capacity is carried through a nonworking break.
        """
        total = {self.variables[sku, line]["quantity"]: -1}
        previous_active = previous_end = None
        for index, (window, (zs, ze, sigma, kappa)) in enumerate(zip(self.problem.windows, windows)):
            length = window.end - window.start
            active = self.variable(f"active_window[{sku},{line},{index}]", 1, integer=False)
            quantity = self.variable(f"window_quantity[{sku},{line},{index}]",
                                     min(self.problem.demand[sku], n * length // d))
            total[quantity] = 1
            recurrence = {active: 1, zs: -1}
            if previous_active is not None:
                recurrence.update({previous_active: -1, previous_end: 1})
            self.row(recurrence, 0, 0)
            # t = length*h - sigma - length*ze + kappa.
            capacity = {quantity: d, active: -n * length, sigma: n, ze: n * length, kappa: -n}
            self.row(capacity, upper=0)
            minimum = dict(capacity)
            minimum[active] += d - 1
            minimum[ze] += n - d
            self.row(minimum, lower=0)
            # Starting/ending a run requires at least one completed unit here.
            self.row({quantity: 2, zs: -1, ze: -1}, lower=0)
            previous_active, previous_end = active, ze
        self.row(total, 0, 0)

    def build_setup_times(self) -> None:
        """Place each incoming setup in calendar time and measure its occupied work ticks.

        Partition the whole horizon into work and nonwork segments. On each
        segment cumulative work time is affine: F(t) = work_before + slope * offset.
        Select segments for both setup endpoints, then occupied work is F(end)-F(start).
        """
        problem = self.problem
        segments = []
        cursor = work_before = 0
        for window in problem.windows:
            if cursor < window.start:
                segments.append((cursor, window.start, work_before, 0))
            segments.append((window.start, window.end, work_before, 1))
            work_before += window.end - window.start
            cursor = window.end
        if cursor < problem.horizon:
            segments.append((cursor, problem.horizon, work_before, 0))
        for (sku, line), v in self.variables.items():
            start = self.variable(f"setup_start[{sku},{line}]", problem.horizon)
            end = self.variable(f"setup_end[{sku},{line}]", problem.horizon)
            occupied = self.variable(f"setup_working[{sku},{line}]", problem.horizon)
            v.update(setup_start=start, setup_end=end, setup_working=occupied)
            self.working_changeover_objective[occupied] = 1
            # assign-first is one exactly when this run has a predecessor.
            for endpoint in (start, end):
                self.row({endpoint: 1, v["assign"]: -problem.horizon, v["first"]: problem.horizon}, upper=0)
            self.row({end: 1, v["start"]: -1}, upper=0)
            duration = {end: 1, start: -1}
            for (previous, following, candidate), arc in self.arcs.items():
                if following == sku and candidate == line:
                    duration[arc] = -problem.changeover[previous, sku]
                    self.row({start: 1, self.variables[previous, line]["end"]: -1,
                              arc: -problem.horizon}, lower=-problem.horizon)
            self.row(duration, 0, 0)
            work = {occupied: -1}
            for name, endpoint, sign in (("start", start, -1), ("end", end, 1)):
                selection = {v["assign"]: -1, v["first"]: 1}
                clock = {endpoint: -1}
                for k, (a, b, cumulative, slope) in enumerate(segments):
                    z = self.variable(f"setup_{name}_segment[{sku},{line},{k}]", 1)
                    offset = self.variable(f"setup_{name}_offset[{sku},{line},{k}]", b-a, integer=False)
                    self.row({offset: 1, z: -(b-a)}, upper=0)
                    selection[z] = 1
                    clock.update({z: a, offset: 1})
                    work.update({z: sign * cumulative, offset: sign * slope})
                self.row(selection, 0, 0)
                self.row(clock, 0, 0)
            self.row(work, 0, 0)

    def extract(self, values: list[float]) -> list[Run]:
        def integer(index: int) -> int:
            value = values[index]
            if not math.isfinite(value) or abs(value - round(value)) > 1e-5:
                raise ApplicationError(ErrorCode.SOLVER_ERROR, "Nonintegral solver result", ExitCode.INTERNAL_ERROR)
            return round(value)
        runs = []
        for (sku, line), v in self.variables.items():
            if integer(v["assign"]):
                predecessors = [a for (a, b, l), arc in self.arcs.items() if b == sku and l == line and integer(arc)]
                if len(predecessors) > 1:
                    raise ApplicationError(ErrorCode.SOLVER_ERROR, "Invalid predecessor result", ExitCode.INTERNAL_ERROR)
                runs.append(Run(sku, line, *(integer(v[key]) for key in ("quantity", "duration", "start", "end")),
                                predecessors[0] if predecessors else None,
                                integer(v["setup_start"]) if predecessors and "setup_start" in v else None))
        return runs

    def model_size(self) -> dict:
        return {"columns": len(self.names), "rows": len(self.rows),
                "binaries": sum(i and ub == 1 for i, ub in zip(self.integer, self.upper)),
                "integers": sum(self.integer), "nonzeros": sum(map(len, self.rows)),
                "eligible_pairs": len(self.variables), "route_arcs": len(self.arcs),
                "work_windows": len(self.problem.windows)}

    def objective_values(self, values: list[float]) -> dict:
        runs = self.extract(values)
        objectives = run_objectives(self.problem, runs)
        if self.working_changeover_weight:
            objectives[AdditionalObjectiveName.WORKING_CHANGEOVER] = sum(
                round(values[index]) for index in self.working_changeover_objective)
        return objectives

    def fix_objective(self, objective: ObjectiveName, value: int) -> None:
        # The split expression counts assignments; public values subtract active SKU.
        raw = value + (len(self.problem.demand) if objective == ObjectiveName.SPLIT else 0)
        self.row(self.objectives[objective], raw, raw)

    def solve_pass(self, objective, *, time_limit: float, incumbent=None,
                   mip_gap=0, seed=0, log_path=None,
                   expression=None, offset=None) -> PassResult:
        """One bounded native solve. Incumbents survive a limit without a new solution."""
        import highspy
        import numpy as np

        started = perf_counter()
        expression = self.objectives[objective] if expression is None else expression
        if offset is None:
            offset = -len(self.problem.demand) if objective == ObjectiveName.SPLIT else 0
        record = {"objective": objective, "highs_status": "NOT_RUN", "proven_optimal": False,
                  "value": None, "bound": None, "gap": None, "nodes": 0, "elapsed_ms": 0}
        def finish(values):
            record["elapsed_ms"] = (perf_counter() - started) * 1000
            if values is not None:
                record["value"] = sum(c * values[i] for i, c in expression.items()) + offset
            logger.info(EventName.SOLVE_PASS_COMPLETED, extra={"fields": record})
            return PassResult(values, record)
        if time_limit <= 0:
            return finish(incumbent)
        h = highspy.Highs()
        def checked(status) -> None:
            if status == highspy.HighsStatus.kError:
                raise ApplicationError(ErrorCode.SOLVER_ERROR, "HiGHS rejected model or option", ExitCode.INTERNAL_ERROR)
        for key, value in {"threads": 1, "random_seed": seed, "mip_rel_gap": mip_gap,
                           "mip_abs_gap": 0.0, "log_to_console": False, "output_flag": log_path is not None}.items():
            checked(h.setOptionValue(key, value))
        if offset:
            # Include the split constant in the native objective so value, bound and gap share the same scale.
            checked(h.changeObjectiveOffset(offset))
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.touch(exist_ok=True)
            except OSError as exc:
                raise ApplicationError(ErrorCode.LOG_WRITE_ERROR, "Cannot open HiGHS log", ExitCode.ARTIFACT_ERROR) from exc
            checked(h.setOptionValue("log_file", str(log_path)))
        n = len(self.names)
        indices = np.arange(n, dtype=np.int32)
        checked(h.addVars(n, np.array(self.lower), np.array(self.upper)))
        checked(h.changeColsIntegrality(n, indices, np.array(self.integer, dtype=np.uint8)))
        for index, name in enumerate(self.names):
            checked(h.passColName(index, name))
        starts, columns, coefficients = [0], [], []
        for row in self.rows:
            columns.extend(row)
            coefficients.extend(row.values())
            starts.append(len(columns))
        checked(h.addRows(len(self.rows), np.array(self.row_lower), np.array(self.row_upper),
                          len(columns), np.array(starts, dtype=np.int32),
                          np.array(columns, dtype=np.int32), np.array(coefficients, dtype=float)))

        costs = np.zeros(n)
        for index, coefficient in expression.items():
            costs[index] = coefficient
        checked(h.changeColsCost(n, indices, costs))
        if incumbent is not None:
            checked(h.setSolution(n, indices, np.array(incumbent, dtype=float)))
        remaining = time_limit - (perf_counter() - started)
        if remaining <= 0:
            return finish(incumbent)
        checked(h.setOptionValue("time_limit", remaining))
        checked(h.run())
        status, info, solution = h.getModelStatus(), h.getInfo(), h.getSolution()
        record.update(highs_status=status.name,
                      bound=info.mip_dual_bound if math.isfinite(info.mip_dual_bound) else None,
                      gap=info.mip_gap if math.isfinite(info.mip_gap) else None,
                      nodes=info.mip_node_count)
        if status == highspy.HighsModelStatus.kInfeasible:
            finish(incumbent)
            if incumbent is not None:
                raise ApplicationError(ErrorCode.SOLVER_ERROR, "Objective fixing lost a feasible solution", ExitCode.INTERNAL_ERROR)
            raise ApplicationError(ErrorCode.INFEASIBLE, "MILP is infeasible", ExitCode.INFEASIBLE, details=[record])
        if status in {highspy.HighsModelStatus.kLoadError, highspy.HighsModelStatus.kModelError,
                      highspy.HighsModelStatus.kPresolveError, highspy.HighsModelStatus.kSolveError,
                      highspy.HighsModelStatus.kPostsolveError, highspy.HighsModelStatus.kUnbounded,
                      highspy.HighsModelStatus.kUnboundedOrInfeasible, highspy.HighsModelStatus.kUnknown}:
            raise ApplicationError(ErrorCode.SOLVER_ERROR, f"Unexpected HiGHS status: {status.name}", ExitCode.INTERNAL_ERROR)
        feasible = info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible and solution.value_valid
        values = list(solution.col_value) if feasible else incumbent
        # A resumed solve must never replace a better feasible incumbent with a worse one.
        if incumbent is not None and values is not None:
            if sum(c * incumbent[i] for i, c in expression.items()) < sum(c * values[i] for i, c in expression.items()):
                values = incumbent
        if values is not None and record["bound"] is not None:
            value = sum(c * values[i] for i, c in expression.items()) + offset
            record["proven_optimal"] = (
                feasible and status == highspy.HighsModelStatus.kOptimal and info.mip_gap == 0.0
                if objective == ObjectiveMode.WEIGHTED else proven_integer_optimum(value, record["bound"]))
        return finish(values)

    def solve(self, *, time_limit: float = 300, mip_gap: float = 0, seed: int = 0,
              log_path: Path | None = None,
              objective_mode: ObjectiveMode = ObjectiveMode.LEXICOGRAPHIC,
              objective_weights: tuple[float, ...] | None = None,
              optimize_timing: bool = True) -> SolveResult:
        objective_mode, weights = validate_objective_options(objective_mode, objective_weights, self.working_changeover_weight)
        weighted = objective_mode == ObjectiveMode.WEIGHTED
        expressions = {key: expression for key, expression in self.objectives.items()
                       if optimize_timing or key in (ObjectiveName.CHANGEOVER, ObjectiveName.SPLIT)}
        if weighted:
            combined = {}
            for weight, expression in zip(weights, self.objectives.values()):
                for index, coefficient in expression.items():
                    combined[index] = combined.get(index, 0.0) + weight * coefficient
            for index, coefficient in self.working_changeover_objective.items():
                combined[index] = self.working_changeover_weight * coefficient
            if not math.isfinite(-weights[1] * len(self.problem.demand)) or any(not math.isfinite(c) for c in combined.values()):
                raise ApplicationError(ErrorCode.CLI_ERROR, "Weights overflow objective coefficients")
            expressions = {ObjectiveMode.WEIGHTED: {i: c for i, c in combined.items() if c}}
        started = perf_counter()
        model = self.model_size()
        if not self.problem.demand:
            objectives = {key: 0 for key in ObjectiveName}
            if self.working_changeover_weight:
                objectives[AdditionalObjectiveName.WORKING_CHANGEOVER] = 0
            return SolveResult([], SolverStatus.OPTIMAL, objectives, [], model, 0, 0.0 if weighted else None)
        passes, best = [], None
        # Fixings belong to this invocation; repeated solve() calls reuse the original model.
        row_count = len(self.rows)
        try:
            for objective, expression in expressions.items():
                remaining = time_limit - (perf_counter() - started)
                if remaining <= 0:
                    break
                result = self.solve_pass(objective, expression=expression,
                    offset=-weights[1] * len(self.problem.demand) if weighted else None,
                    time_limit=remaining, incumbent=best, mip_gap=mip_gap, seed=seed,
                    log_path=log_path)
                passes.append(result.record)
                best = result.values
                if weighted or not result.record["proven_optimal"]:
                    break
                self.fix_objective(objective, round(result.record["value"]))
        finally:
            del self.rows[row_count:]
            del self.row_lower[row_count:]
            del self.row_upper[row_count:]
        if best is None:
            raise ApplicationError(ErrorCode.NO_INCUMBENT, "No feasible solution within the solve budget", ExitCode.NO_INCUMBENT, details=passes)
        objectives = self.objective_values(best)
        status = SolverStatus.OPTIMAL if len(passes) == len(expressions) and all(p["proven_optimal"] for p in passes) else SolverStatus.FEASIBLE
        weighted_value = sum(w * objectives[name] for w, name in zip(weights, ObjectiveName)) if weighted else None
        if self.working_changeover_weight:
            weighted_value += self.working_changeover_weight * objectives[AdditionalObjectiveName.WORKING_CHANGEOVER]
        return SolveResult(self.extract(best), status, objectives, passes, model, (perf_counter()-started)*1000, weighted_value)


def run_objectives(problem: Problem, runs: list[Run]) -> dict:
    return {
        ObjectiveName.CHANGEOVER: sum(problem.changeover[r.predecessor, r.sku] for r in runs if r.predecessor is not None),
        ObjectiveName.SPLIT: len(runs) - len(problem.demand),
        ObjectiveName.MAKESPAN: max((r.end for r in runs), default=0),
        ObjectiveName.STARTS: sum(r.start for r in runs),
    }
