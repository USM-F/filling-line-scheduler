import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from filling_scheduler.domain.enums import ErrorCode
from filling_scheduler.domain.input import SchedulingInput
from filling_scheduler.errors import ApplicationError
from filling_scheduler.io.input import load_input
from filling_scheduler.pipeline import inspect_input


def test_base_input(input_path):
    problem = load_input(input_path)
    assert problem.demand[0].sku_id == "A"
    assert problem.demand[0].demand_units == 49344
    assert problem.lines[0].eligible_products[0].capacity_units_per_hour == Decimal("9600")
    assert problem.calendar.shifts[1].ends_next_day is True
    assert problem.calendar.shifts[1].breaks[0].starts_next_day is True
    assert problem.planning_horizon.start.utcoffset().total_seconds() == 7 * 3600
    assert problem.model_dump(by_alias=True)["demand"][0]["product"] == "A"
    with pytest.raises(ValidationError, match="frozen"):
        problem.id = "changed"
    report = inspect_input(problem)
    assert report == {
        "id": "filling_only_20_product_assignment_v1",
        "planningHorizon": {
            "start": "2026-08-17T08:30:00+07:00", "end": "2026-08-24T08:30:00+07:00",
            "timeZone": "Asia/Bangkok", "precisionMinutes": 1,
        },
        "productCount": 20, "activeProductCount": 20, "lineCount": 13,
        "eligiblePairCount": 84, "totalDemandUnits": 1133892,
    }


@pytest.mark.parametrize("value", ["49344", 1.5, True, -1, None])
def test_strict_demand(input_data, value):
    input_data["demand"][0]["quantityUnits"] = value
    with pytest.raises(ValidationError):
        SchedulingInput.model_validate(input_data)


@pytest.mark.parametrize("value", ["9600", 1.5, True, 0, -1, Decimal("Infinity"), Decimal("NaN")])
def test_strict_positive_rate(input_data, value):
    input_data["lines"][0]["eligibleProducts"][0]["capacityUnitsPerHour"] = value
    with pytest.raises(ValidationError):
        SchedulingInput.model_validate(input_data)


def test_decimal_preserved(input_path, tmp_path):
    path = tmp_path / "decimal.json"
    path.write_text(input_path.read_text().replace('"capacityUnitsPerHour": 9600', '"capacityUnitsPerHour": 9600.1234567890123456789', 1))
    assert load_input(path).lines[0].eligible_products[0].capacity_units_per_hour == Decimal("9600.1234567890123456789")


@pytest.mark.parametrize("field,value", [
    ("precisionMinutes", 0), ("precisionMinutes", True), ("timeZone", "Unknown/Zone"),
    ("timeZone", "/etc/passwd"), ("start", "2026-08-17T08:30:00"),
    ("start", 123), ("start", "bad-date"),
])
def test_horizon_fields(input_data, field, value):
    input_data["planningHorizon"][field] = value
    with pytest.raises(ValidationError):
        SchedulingInput.model_validate(input_data)


@pytest.mark.parametrize("section,field,value", [
    ("shift", "startTime", "25:00"), ("shift", "endsNextDay", 1),
    ("calendar", "workingDays", ["monday"]),
    ("matrix", "A", -1), ("matrix", "A", "30"), ("matrix", "A", True),
])
def test_calendar_matrix_fields(input_data, section, field, value):
    target = {"shift": input_data["calendar"]["shifts"][0], "calendar": input_data["calendar"],
              "matrix": input_data["changeoverMatrixMinutes"]["A"]}[section]
    target[field] = value
    with pytest.raises(ValidationError):
        SchedulingInput.model_validate(input_data)


def test_error_path_and_unknown_field(input_data, tmp_path):
    input_data["demand"][0]["unexpected"] = "value"
    path = tmp_path / "input.json"
    path.write_text(json.dumps(input_data))
    with pytest.raises(ApplicationError) as caught:
        load_input(path)
    assert caught.value.code == ErrorCode.SCHEMA_ERROR
    assert caught.value.details[0]["path"] == ["demand", 0, "unexpected"]
    assert "input" not in caught.value.details[0]


@pytest.mark.parametrize("content,code", [
    (b"{", ErrorCode.INVALID_JSON), (b"\xff", ErrorCode.INVALID_JSON),
    (b'{"x": NaN}', ErrorCode.INVALID_JSON), (b'{"x": Infinity}', ErrorCode.INVALID_JSON),
    (b'{"x": 1, "x": 2}', ErrorCode.DUPLICATE_JSON_KEY),
    (b'{"demand": [{"product": "A", "product": "B"}]}', ErrorCode.DUPLICATE_JSON_KEY),
    (b"[]", ErrorCode.SCHEMA_ERROR), (b"null", ErrorCode.SCHEMA_ERROR),
])
def test_invalid_documents(tmp_path, content, code):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    with pytest.raises(ApplicationError) as caught:
        load_input(path)
    assert caught.value.code == code
    assert caught.value.details


def test_duplicate_path(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"demand": [{"product": "A", "product": "B"}]}')
    with pytest.raises(ApplicationError) as caught:
        load_input(path)
    assert caught.value.details[0]["path"] == '$["demand"][0]["product"]'


def test_missing_input(tmp_path):
    with pytest.raises(ApplicationError) as caught:
        load_input(tmp_path / "absent.json")
    assert caught.value.code == ErrorCode.INPUT_READ_ERROR


def test_zero_demand_and_semantics_deferred(input_data):
    input_data["demand"][0]["quantityUnits"] = 0
    input_data["calendar"]["appliesToLines"] = ["not-a-known-line"]
    report = inspect_input(SchedulingInput.model_validate(input_data))
    assert report["productCount"] == 20
    assert report["activeProductCount"] == 19
