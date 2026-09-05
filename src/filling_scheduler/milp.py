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
        makespan = self.variable("makespan", T)
        self.objectives[ObjectiveName.MAKESPAN][makespan] = 1
        for (sku, line), units in problem.units_per_tick.items():
            demand = problem.demand[sku]
            pmax = min((demand + units - 1) // units, problem.available_ticks)
            variables = {
                name: self.variable(f"{name}[{sku},{line}]", upper)
                for name, upper in [("assign", 1), ("quantity", min(demand, units * problem.available_ticks)),
                                    ("duration", pmax), ("start", T), ("end", T), ("first", 1), ("last", 1)]
            }
            self.variables[sku, line] = variables
            y, q, p, s, c = (variables[name] for name in ("assign", "quantity", "duration", "start", "end"))
            self.row({q: 1, y: -demand}, upper=0)
            self.row({q: 1, y: -1}, lower=0)
            self.row({q: 1, p: -units}, upper=0)
            self.row({q: 1, p: -units, y: units - 1}, lower=0)
            self.row({p: 1, y: -pmax}, upper=0)
            self.row({c: 1, s: -1, p: -1}, lower=0)
            self.row({makespan: 1, c: -1}, lower=0)
            self.objectives[ObjectiveName.SPLIT][y] = 1
            self.objectives[ObjectiveName.STARTS][s] = 1
            starts, ends = {y: -1}, {y: -1}
            wall_start, wall_end, productive = {s: 1}, {c: 1}, {p: -1}
            for index, window in enumerate(problem.windows):
                length = window.end - window.start
                zs = self.variable(f"start_window[{sku},{line},{index}]", 1)
                ze = self.variable(f"end_window[{sku},{line},{index}]", 1)
                sigma = self.variable(f"start_offset[{sku},{line},{index}]", length - 1, integer=False)
                kappa = self.variable(f"end_offset[{sku},{line},{index}]", length, integer=False)
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

    def solve(self, *, time_limit: float = 300, mip_gap: float = 0, seed: int = 0,
              threads: int = 1, log_path: Path | None = None) -> SolveResult:
        import highspy
        import numpy as np

        started = perf_counter()
        model = {"columns": len(self.names), "rows": len(self.rows),
                 "binaries": sum(i and ub == 1 for i, ub in zip(self.integer, self.upper)),
                 "integers": sum(self.integer), "nonzeros": sum(map(len, self.rows)),
                 "eligible_pairs": len(self.variables), "route_arcs": len(self.arcs),
                 "work_windows": len(self.problem.windows)}
        if not self.problem.demand:
            return SolveResult([], SolverStatus.OPTIMAL, {key: 0 for key in ObjectiveName}, [], model, 0)
        # HiGHS keeps a process-global thread pool. Sequential CLI calls may request a different size.
        highspy.Highs.resetGlobalScheduler(True)
        h = highspy.Highs()
        def checked(status) -> None:
            if status == highspy.HighsStatus.kError:
                raise ApplicationError(ErrorCode.SOLVER_ERROR, "HiGHS rejected model or option", ExitCode.INTERNAL_ERROR)
        for key, value in {"threads": threads, "random_seed": seed, "mip_rel_gap": mip_gap,
                           "mip_abs_gap": 0.0, "log_to_console": False, "output_flag": log_path is not None}.items():
            checked(h.setOptionValue(key, value))
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
        passes = []
        best = None
        for objective, expression in self.objectives.items():
            remaining = time_limit - (perf_counter() - started)
            if remaining <= 0:
                break
            costs = np.zeros(n)
            for index, coefficient in expression.items():
                costs[index] = coefficient
            checked(h.changeColsCost(n, indices, costs))
            checked(h.setOptionValue("time_limit", remaining))
            if best is not None:
                checked(h.setSolution(best))
            pass_started = perf_counter()
            checked(h.run())
            status, info, solution = h.getModelStatus(), h.getInfo(), h.getSolution()
            if status in {highspy.HighsModelStatus.kLoadError, highspy.HighsModelStatus.kModelError,
                          highspy.HighsModelStatus.kPresolveError, highspy.HighsModelStatus.kSolveError,
                          highspy.HighsModelStatus.kPostsolveError, highspy.HighsModelStatus.kUnbounded,
                          highspy.HighsModelStatus.kUnboundedOrInfeasible, highspy.HighsModelStatus.kUnknown}:
                raise ApplicationError(ErrorCode.SOLVER_ERROR, f"Unexpected HiGHS status: {status.name}", ExitCode.INTERNAL_ERROR)
            feasible = info.primal_solution_status == highspy.SolutionStatus.kSolutionStatusFeasible and solution.value_valid
            if feasible:
                best = solution
            value = sum(coefficient * solution.col_value[index] for index, coefficient in expression.items()) if feasible else None
            bound = info.mip_dual_bound if math.isfinite(info.mip_dual_bound) else None
            proven = feasible and bound is not None and proven_integer_optimum(value, bound)
            offset = -len(self.problem.demand) if objective == ObjectiveName.SPLIT else 0
            record = {"objective": objective, "highs_status": status.name, "proven_optimal": proven,
                      "value": value + offset if value is not None else None,
                      "bound": bound + offset if bound is not None else None,
                      "gap": info.mip_gap if math.isfinite(info.mip_gap) else None,
                      "nodes": info.mip_node_count, "elapsed_ms": (perf_counter() - pass_started) * 1000}
            passes.append(record)
            logger.info(EventName.SOLVE_PASS_COMPLETED, extra={"fields": record})
            if status == highspy.HighsModelStatus.kInfeasible:
                if best is not None:
                    raise ApplicationError(ErrorCode.SOLVER_ERROR, "Objective fixing lost a feasible solution", ExitCode.INTERNAL_ERROR)
                raise ApplicationError(ErrorCode.INFEASIBLE, "MILP is infeasible", ExitCode.INFEASIBLE, details=passes)
            if not proven:
                break
            checked(h.addRow(round(value), round(value), len(expression),
                             np.array(list(expression), dtype=np.int32), np.array(list(expression.values()), dtype=float)))
        if best is None:
            raise ApplicationError(ErrorCode.NO_INCUMBENT, "No feasible solution within the solve budget", ExitCode.NO_INCUMBENT, details=passes)
        runs = self.extract(best.col_value)
        objectives = {
            ObjectiveName.CHANGEOVER: sum(self.problem.changeover[r.predecessor, r.sku] for r in runs if r.predecessor),
            ObjectiveName.SPLIT: len(runs) - len(self.problem.demand),
            ObjectiveName.MAKESPAN: max((r.end for r in runs), default=0),
            ObjectiveName.STARTS: sum(r.start for r in runs),
        }
        status = SolverStatus.OPTIMAL if len(passes) == 4 and all(p["proven_optimal"] for p in passes) else SolverStatus.FEASIBLE
        return SolveResult(runs, status, objectives, passes, model, (perf_counter() - started) * 1000)
