from copy import deepcopy

import pytest

from filling_scheduler.input import load_document
from filling_scheduler.schedule import Schedule
from filling_scheduler.validation import validate_schedule
from data_generators import example, calendar_example, generated


@pytest.mark.parametrize("data", [example(), example({"A": 250}, lines=3), example({"A": 3, "B": 0}, units=2), example({"A": 0}), calendar_example()])
def test_materialization_and_validation(data, tmp_path):
    problem, schedule = generated(data)
    assert validate_schedule(problem, schedule)["valid"]
    path = tmp_path / "schedule.json"
    path.write_text(schedule.model_dump_json(by_alias=True))
    assert load_document(path, Schedule) == schedule
    if len(data["lines"]) == 3:
        assert schedule.summary.products_split == 1


def mutate_production(data, **changes):
    next(slot for line in data["lines"] for slot in line["slots"] if slot["type"] == "production").update(changes)


@pytest.mark.parametrize("mutate,code", [
    (lambda d: mutate_production(d, quantityUnits=99999), "CAPACITY"),
    (lambda d: mutate_production(d, product="unknown"), "ELIGIBILITY"),
    (lambda d: mutate_production(d, start="2026-08-17T08:00:01+00:00"), "GRID"),
    (lambda d: mutate_production(d, start="2026-08-17T07:59:00+00:00"), "HORIZON"),
    (lambda d: d.update(timeZone="Asia/Bangkok"), "TIME_ZONE"),
    (lambda d: d["lines"].append(deepcopy(d["lines"][0])), "LINES"),
    (lambda d: d["summary"].update(productsSplitAcrossMultipleLines=99), "SUMMARY"),
    (lambda d: d["lines"][0]["slots"].reverse(), "OVERLAP"),
    (lambda d: [s.update(durationMinutes=1) for l in d["lines"] for s in l["slots"] if s["type"] == "changeover"], "CHANGEOVER_DURATION"),
    (lambda d: [l.update(slots=[s for s in l["slots"] if s["type"] != "changeover"]) for l in d["lines"]], "CHANGEOVER_SEQUENCE"),
    (lambda d: mutate_production(d, end="2026-08-17T10:30:00+00:00"), "CALENDAR"),
])
def test_reject_corruption(mutate, code):
    problem, schedule = generated(calendar_example())
    raw = schedule.model_dump(mode="json", by_alias=True)
    mutate(raw)
    report = validate_schedule(problem, Schedule.model_validate(raw))
    assert not report["valid"]
    assert code in {e["code"] for e in report["errors"]}


def test_partial_tick_only_at_run_end():
    data = example({"A": 7}, lines=1, capacity=8, units=2)
    data["calendar"]["shifts"][0]["breaks"] = [{"startTime": "08:02", "endTime": "08:04"}]
    problem, schedule = generated(data)
    raw = schedule.model_dump(mode="json", by_alias=True)
    raw["lines"][0]["slots"][0]["quantityUnits"] -= 1
    raw["lines"][0]["slots"][1]["quantityUnits"] += 1
    report = validate_schedule(problem, Schedule.model_validate(raw))
    assert "ROUNDING" in {e["code"] for e in report["errors"]}


def test_run_gaps_and_returning_product():
    data = example({"A": 4, "B": 2}, lines=1, capacity=20)
    problem, schedule = generated(data)
    raw = schedule.model_dump(mode="json", by_alias=True)
    raw["lines"][0]["slots"] = [
        {"type": "production", "product": "A", "quantityUnits": 2,
         "start": "2026-08-17T08:00:00+00:00", "end": "2026-08-17T08:02:00+00:00"},
        {"type": "production", "product": "A", "quantityUnits": 2,
         "start": "2026-08-17T08:03:00+00:00", "end": "2026-08-17T08:05:00+00:00"},
    ]
    assert "RUN_GAP" in {e["code"] for e in validate_schedule(problem, Schedule.model_validate(raw))["errors"]}
    raw["lines"][0]["slots"].insert(1, {
        "type": "production", "product": "B", "quantityUnits": 1,
        "start": "2026-08-17T08:02:00+00:00", "end": "2026-08-17T08:03:00+00:00"})
    assert "FRAGMENTATION" in {e["code"] for e in validate_schedule(problem, Schedule.model_validate(raw))["errors"]}
