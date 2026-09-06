"""One integrated split/route/event-calendar MILP; native HiGHS is loaded lazily."""

from dataclasses import dataclass
import logging
import math
from pathlib import Path
from time import perf_counter

from filling_scheduler.enums import ErrorCode, EventName, ExitCode, ObjectiveName, SolverStatus
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


@dataclass
class SolveResult:
    runs: list[Run]
    status: SolverStatus
    objectives: dict[str, int]
    passes: list[dict]
    model: dict[str, int]
    elapsed_ms: float


@dataclass
class PassResult:
    values: list[float] | None
    record: dict


def proven_integer_optimum(value: float, bound: float) -> bool:
    return math.isfinite(bound) and math.ceil(bound - 1e-6) >= round(value)


class SchedulingMilp:
    def __init__(self, problem: Problem) -> None:
        self.problem = problem
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
            self.objectives[ObjectiveName.SPLIT][y] = 1
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
                                predecessors[0] if predecessors else None))
        return runs

    def model_size(self) -> dict:
        return {"columns": len(self.names), "rows": len(self.rows),
                "binaries": sum(i and ub == 1 for i, ub in zip(self.integer, self.upper)),
                "integers": sum(self.integer), "nonzeros": sum(map(len, self.rows)),
                "eligible_pairs": len(self.variables), "route_arcs": len(self.arcs),
                "work_windows": len(self.problem.windows)}

    def objective_values(self, values: list[float]) -> dict:
        return run_objectives(self.problem, self.extract(values))

    def fix_objective(self, objective: ObjectiveName, value: int) -> None:
        # The split expression counts assignments; public values subtract active SKU.
        raw = value + (len(self.problem.demand) if objective == ObjectiveName.SPLIT else 0)
        self.row(self.objectives[objective], raw, raw)

    def solve_pass(self, objective, *, time_limit: float, incumbent=None,
                   mip_gap=0, seed=0, log_path=None) -> PassResult:
        """One bounded native solve. Incumbents survive a limit without a new solution."""
        import highspy
        import numpy as np

        started = perf_counter()
        expression = self.objectives[objective]
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
        # Presolve falsely proves 240 instead of 210 on doubled demand;
        # see test_supplied_baseline[doubled].
        for key, value in {"presolve": "off", "threads": 1, "random_seed": seed, "mip_rel_gap": mip_gap,
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
            record["proven_optimal"] = proven_integer_optimum(value, record["bound"])
        return finish(values)

    def solve(self, *, time_limit: float = 300, mip_gap: float = 0, seed: int = 0,
              log_path: Path | None = None) -> SolveResult:
        started = perf_counter()
        model = self.model_size()
        if not self.problem.demand:
            return SolveResult([], SolverStatus.OPTIMAL, {key: 0 for key in ObjectiveName}, [], model, 0)
        passes, best = [], None
        row_count = len(self.rows)
        try:
            for objective in ObjectiveName:
                remaining = time_limit - (perf_counter() - started)
                if remaining <= 0:
                    break
                result = self.solve_pass(objective, time_limit=remaining, incumbent=best,
                                         mip_gap=mip_gap, seed=seed, log_path=log_path)
                passes.append(result.record)
                best = result.values
                if not result.record["proven_optimal"]:
                    break
                if objective == ObjectiveName.CHANGEOVER:
                    self.fix_objective(objective, round(result.record["value"]))
        finally:
            del self.rows[row_count:]
            del self.row_lower[row_count:]
            del self.row_upper[row_count:]
        if best is None:
            raise ApplicationError(ErrorCode.NO_INCUMBENT, "No feasible solution within the solve budget",
                                   ExitCode.NO_INCUMBENT, details=passes)
        proven = len(passes) == len(ObjectiveName) and all(p["proven_optimal"] for p in passes)
        return SolveResult(self.extract(best), SolverStatus.OPTIMAL if proven else SolverStatus.FEASIBLE,
                           self.objective_values(best), passes, model, (perf_counter()-started)*1000)


def run_objectives(problem: Problem, runs: list[Run]) -> dict:
    return {
        ObjectiveName.CHANGEOVER: sum(problem.changeover[r.predecessor, r.sku] for r in runs if r.predecessor is not None),
        ObjectiveName.SPLIT: len(runs) - len(problem.demand),
    }
