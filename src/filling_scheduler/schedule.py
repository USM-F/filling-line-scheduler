"""Public JSON contract and conversion from semantic runs to physical slots."""

from collections import defaultdict
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filling_scheduler.models import InputModel, Identifier, Timestamp
from filling_scheduler.problem import Problem

if TYPE_CHECKING:
    from filling_scheduler.milp import SolveResult


class ProductionSlot(InputModel):
    type: Literal["production"]
    start: Timestamp
    end: Timestamp
    sku_id: Annotated[Identifier, Field(alias="product")]
    quantity: Annotated[int, Field(alias="quantityUnits", gt=0)]


class ChangeoverSlot(InputModel):
    type: Literal["changeover"]
    start: Timestamp
    end: Timestamp
    from_sku: Annotated[Identifier, Field(alias="fromProduct")]
    to_sku: Annotated[Identifier, Field(alias="toProduct")]
    duration_minutes: Annotated[int, Field(alias="durationMinutes", gt=0)]


class ScheduleLine(InputModel):
    line_id: Annotated[Identifier, Field(alias="line")]
    slots: list[Annotated[ProductionSlot | ChangeoverSlot, Field(discriminator="type")]]


class Summary(InputModel):
    produced_by_product: Annotated[dict[Identifier, Annotated[int, Field(ge=0)]], Field(alias="producedByProduct")]
    total_changeover_minutes: Annotated[int, Field(alias="totalChangeoverMinutes", ge=0)]
    products_split: Annotated[int, Field(alias="productsSplitAcrossMultipleLines", ge=0)]
    slot_count: Annotated[int, Field(alias="slotCount", ge=0)]


class Schedule(InputModel):
    schedule_id: Annotated[Identifier, Field(alias="scheduleId")]
    time_zone: Annotated[Identifier, Field(alias="timeZone")]
    lines: list[ScheduleLine]
    summary: Summary

    @field_validator("time_zone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


def summarize(lines: list[dict], products) -> dict:
    quantities = dict.fromkeys(products, 0)
    assignments = defaultdict(set)
    changeover = count = 0
    for line in lines:
        for slot in line["slots"]:
            count += 1
            if slot["type"] == "production":
                sku = slot["product"]
                quantities[sku] = quantities.get(sku, 0) + slot["quantityUnits"]
                assignments[sku].add(line["line"])
            else:
                changeover += slot["durationMinutes"]
    return {"producedByProduct": quantities, "totalChangeoverMinutes": changeover,
            "productsSplitAcrossMultipleLines": sum(len(lines) > 1 for lines in assignments.values()),
            "slotCount": count}


def materialize_schedule(problem: Problem, result: "SolveResult", schedule_id: str) -> Schedule:
    lines = []
    for line in problem.lines:
        slots = []
        previous = None
        for run in sorted((r for r in result.runs if r.line == line), key=lambda r: r.start):
            if previous is not None:
                duration = problem.changeover[previous.sku, run.sku]
                if duration:
                    start = run.setup_start if run.setup_start is not None else previous.end
                    slots.append({"type": "changeover", "start": problem.timestamp(start),
                                  "end": problem.timestamp(start + duration), "fromProduct": previous.sku,
                                  "toProduct": run.sku, "durationMinutes": duration * problem.precision})
            remaining = run.quantity
            for window in problem.windows:
                a, b = max(run.start, window.start), min(run.end, window.end)
                if a < b:
                    quantity = min(remaining, (b-a) * problem.units_per_tick[run.sku, line])
                    slots.append({"type": "production", "start": problem.timestamp(a), "end": problem.timestamp(b),
                                  "product": run.sku, "quantityUnits": quantity})
                    remaining -= quantity
            if remaining:
                raise ValueError("Run cannot be materialized into work windows")
            previous = run
        lines.append({"line": line, "slots": slots})
    return Schedule.model_validate({"scheduleId": schedule_id, "timeZone": problem.source.planning_horizon.time_zone,
                                    "lines": lines, "summary": summarize(lines, [d.sku_id for d in problem.source.demand])})
