import pytest

from filling_scheduler.errors import ApplicationError
from filling_scheduler.models import SchedulingInput
from filling_scheduler.problem import prepare_problem
from test_milp import example, calendar_example


def prepare(data):
    return prepare_problem(SchedulingInput.model_validate(data))


def test_actual_calendar(input_data):
    p = prepare(input_data)
    assert len(p.windows) == 24
    assert p.available_ticks == 6840
    assert p.timestamp(p.windows[-1].end) == "2026-08-23T07:00:00+07:00"
    assert len(p.units_per_tick) == 84


@pytest.mark.parametrize("change", [
    lambda d: d["demand"].append(d["demand"][0]),
    lambda d: d["lines"].append(d["lines"][0]),
    lambda d: d["lines"][0]["eligibleProducts"].append(d["lines"][0]["eligibleProducts"][0]),
    lambda d: d["lines"][0]["eligibleProducts"][0].update(product="unknown"),
    lambda d: d["calendar"].update(appliesToLines=[]),
    lambda d: d["changeoverMatrixMinutes"].clear(),
    lambda d: d["changeoverMatrixMinutes"]["A"].update(A=1),
    lambda d: d["calendar"]["shifts"].append({**d["calendar"]["shifts"][0], "code": "overlap"}),
    lambda d: d["calendar"]["shifts"][0].update(endTime="07:00"),
    lambda d: d["calendar"]["shifts"][0].update(breaks=[{"startTime": "07:00", "endTime": "08:05"}]),
    lambda d: d["planningHorizon"].update(end="2026-08-17T08:00:00+00:00"),
    lambda d: d["planningHorizon"].update(timeZone="Asia/Bangkok"),
])
def test_semantic_errors(change):
    data = example()
    change(data)
    with pytest.raises(ApplicationError) as error:
        prepare(data)
    assert error.value.exit_code == 2
    assert error.value.code == "PROBLEM_INVALID"


@pytest.mark.parametrize("change", [
    lambda d: d["lines"][0]["eligibleProducts"][0].update(capacityUnitsPerHour=61),
    lambda d: d["planningHorizon"].update(precisionMinutes=3),
])
def test_unsupported_grid(change):
    data = example()
    change(data)
    with pytest.raises(ApplicationError) as error:
        prepare(data)
    assert error.value.code == "UNSUPPORTED_PRECISION"


def test_break_at_midnight_and_horizon_clip():
    data = calendar_example()
    data["planningHorizon"]["start"] = "2026-08-17T09:00:00+00:00"
    p = prepare(data)
    assert [(w.start, w.end) for w in p.windows] == [(0, 60), (120, 180), (780, 900), (930, 1020)]


def test_ambiguous_local_boundary():
    data = example()
    data["planningHorizon"].update(start="2026-10-25T00:00:00+02:00", end="2026-10-25T05:00:00+01:00", timeZone="Europe/Berlin")
    data["calendar"].update(workingDays=["SUNDAY"], shifts=[{"code": "DST", "startTime": "02:30", "endTime": "04:00", "breaks": []}])
    with pytest.raises(ApplicationError, match="Ambiguous"):
        prepare(data)
