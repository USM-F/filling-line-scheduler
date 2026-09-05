from hypothesis import given, strategies as st

from filling_scheduler.models import SchedulingInput
from filling_scheduler.pipeline import inspect_input


@given(st.lists(st.integers(min_value=0, max_value=10**12), max_size=40))
def test_counts_and_total_follow_demands(quantities):
    data = {
        "id": "property", "description": "generated",
        "planningHorizon": {"start": "2026-08-17T08:30:00+07:00", "end": "2026-08-24T08:30:00+07:00", "timeZone": "Asia/Bangkok", "precisionMinutes": 1},
        "demand": [{"product": str(index), "quantityUnits": value} for index, value in enumerate(quantities)],
        "lines": [], "calendar": {"appliesToLines": [], "workingDays": [], "shifts": []},
        "changeoverMatrixMinutes": {},
    }
    report = inspect_input(SchedulingInput.model_validate(data))
    assert report["totalDemandUnits"] == sum(quantities)
    assert report["activeProductCount"] == sum(value > 0 for value in quantities)
    data["demand"].reverse()
    assert inspect_input(SchedulingInput.model_validate(data)) == report
