from itertools import permutations, product

import pytest

from filling_scheduler.errors import ApplicationError
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from filling_scheduler.milp import SchedulingMilp
from data_generators import example, calendar_example, solve


def test_forced_split():
    result = solve(example())
    assert result.status == "OPTIMAL"
    assert len(result.runs) == 2
    assert sum(r.quantity for r in result.runs) == 150
    assert all(0 < r.quantity <= 100 for r in result.runs)
    assert result.objectives == dict(changeover_ticks=0, split_excess=1)


def test_optional_split():
    result = solve(example({"A": 80}))
    assert len(result.runs) == 1
    assert result.runs[0].end == 80


def test_three_lines():
    result = solve(example({"A": 250}, lines=3))
    assert len(result.runs) == 3
    assert result.objectives["split_excess"] == 2


def test_calendar():
    result = solve(calendar_example())
    assert result.status == "OPTIMAL"
    assert result.objectives == dict(changeover_ticks=10, split_excess=0)


def test_rounding_and_zero_demand():
    result = solve(example({"A": 3, "B": 0}, units=2))
    assert len(result.runs) == 1
    assert result.runs[0].duration == 2
    assert solve(example({"A": 0})).runs == []


def test_infeasible_and_no_incumbent():
    with pytest.raises(ApplicationError) as error:
        solve(example({"A": 201}))
    assert error.value.exit_code == 3
    with pytest.raises(ApplicationError) as error:
        SchedulingMilp(prepare_problem(SchedulingInput.model_validate(example()))).solve(time_limit=0)
    assert error.value.exit_code == 4


def brute_force(demand, windows, units, setup):
    """Independent integer allocation/order enumeration and minute-by-minute calendar."""
    best = None
    work = {tick for a, b in windows for tick in range(a, b)}
    for allocation in product(*(range(q + 1) for q in demand.values())):
        quantities = [dict(zip(demand, allocation)), {a: q-allocation[i] for i, (a, q) in enumerate(demand.items())}]
        orders = [list(permutations([a for a, q in amounts.items() if q])) for amounts in quantities]
        for routes in product(*orders):
            changes = runs = 0
            feasible = True
            for line, route in enumerate(routes):
                tick = 0
                previous = None
                for sku in route:
                    cost = setup[previous][sku] if previous is not None else 0
                    changes += cost
                    tick += cost
                    while tick not in work and tick < max(work) + 1:
                        tick += 1
                    runs += 1
                    remaining = (quantities[line][sku] + units - 1) // units
                    while remaining and tick <= max(work):
                        if tick in work:
                            remaining -= 1
                        tick += 1
                    feasible &= remaining == 0
                    previous = sku
            value = changes, runs - len(demand)
            if feasible and (best is None or value < best):
                best = value
    return best


@pytest.mark.parametrize("demand,units", [({"A": 4, "B": 5}, 1), ({"A": 7, "B": 4}, 1), ({"A": 9, "B": 8}, 2)])
def test_independent_enumeration(demand, units):
    data = example(demand, capacity=8, units=units)
    data["calendar"]["shifts"][0]["breaks"] = [{"startTime": "08:03", "endTime": "08:05"}]
    expected = brute_force(demand, [(0, 3), (5, 8)], units, data["changeoverMatrixMinutes"])
    if expected is None:
        with pytest.raises(ApplicationError) as error:
            solve(data)
        assert error.value.exit_code == 3
    else:
        assert tuple(solve(data).objectives.values()) == expected
