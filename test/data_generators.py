"""Synthetic input and schedule generators for tests."""
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.milp import SchedulingMilp
from filling_scheduler.schedule import materialize_schedule
from filling_scheduler.timing import left_shift


def prepared(data):
    return prepare_problem(SchedulingInput.model_validate(data))


def solve(data):
    problem = prepared(data)
    result = SchedulingMilp(problem).solve(time_limit=20)
    result.runs = left_shift(problem, result.runs)
    return result


def generated(data):
    problem = prepared(data)
    return problem, materialize_schedule(problem, solve(data), "synthetic")


def example(demand=None, lines=2, capacity=100, units=1):
    demand = demand or {"A": 150}
    return {
        "id": "synthetic", "description": "Exact synthetic case",
        "planningHorizon": {"start": "2026-08-17T08:00:00+00:00", "end": "2026-08-17T10:00:00+00:00",
                            "timeZone": "UTC", "precisionMinutes": 1},
        "demand": [{"product": sku, "quantityUnits": q} for sku, q in demand.items()],
        "lines": [{"line": f"L{i+1}", "eligibleProducts": [
            {"product": sku, "capacityUnitsPerHour": 60 * units} for sku in demand]} for i in range(lines)],
        "calendar": {"appliesToLines": [f"L{i+1}" for i in range(lines)], "workingDays": ["MONDAY"],
                     "shifts": [{"code": "DAY", "startTime": "08:00", "endTime": f"{8+capacity//60:02d}:{capacity%60:02d}", "breaks": []}]},
        "changeoverMatrixMinutes": {a: {b: 0 if a == b else 2 for b in demand} for a in demand},
    }


def calendar_example():
    data = example({"A": 100, "B": 90, "C": 120, "D": 250})
    data["planningHorizon"]["end"] = "2026-08-18T02:00:00+00:00"
    data["calendar"]["shifts"] = [
        {"code": "DAY", "startTime": "08:00", "endTime": "12:00", "breaks": [{"startTime": "10:00", "endTime": "11:00"}]},
        {"code": "NIGHT", "startTime": "22:00", "endTime": "02:00", "endsNextDay": True,
         "breaks": [{"startTime": "00:00", "endTime": "00:30", "startsNextDay": True}]},
    ]
    data["lines"][0]["eligibleProducts"] = [{"product": a, "capacityUnitsPerHour": 60} for a in "ABD"]
    data["lines"][1]["eligibleProducts"] = [{"product": a, "capacityUnitsPerHour": 120 if a == "B" else 60} for a in "BCD"]
    data["changeoverMatrixMinutes"] = {a: {b: 0 if a == b else 20 for b in "ABCD"} for a in "ABCD"}
    for a, b in [("A", "B"), ("C", "D")]:
        data["changeoverMatrixMinutes"][a][b] = 5
        data["changeoverMatrixMinutes"][b][a] = 9
    return data


def lunch_example(a=120, b=60, setup=30, break_end="11:00"):
    data = example({"A": a, "B": b}, lines=1)
    data["planningHorizon"]["end"] = "2026-08-17T13:00:00+00:00"
    data["calendar"]["shifts"] = [{"code": "DAY", "startTime": "08:00", "endTime": "13:00",
                                   "breaks": [{"startTime": "10:00", "endTime": break_end}]}]
    data["changeoverMatrixMinutes"]["A"]["B"] = setup
    data["changeoverMatrixMinutes"]["B"]["A"] = setup
    return prepare_problem(SchedulingInput.model_validate(data))
