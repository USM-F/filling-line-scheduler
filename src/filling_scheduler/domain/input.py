"""Strict input boundary. Cross-field scheduling semantics are a later stage."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def numeric_decimal(value: object) -> Decimal:
    # The JSON loader preserves fractional tokens as Decimal, never binary float.
    if type(value) not in (int, Decimal):
        raise ValueError("expected a JSON number")
    return Decimal(value)


def aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("expected an offset-aware ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("expected an offset-aware ISO 8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone offset is required")
    return parsed


Identifier = Annotated[str, Field(min_length=1)]
Minutes = Annotated[int, Field(ge=0)]
ClockTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Timestamp = Annotated[datetime, BeforeValidator(aware_datetime)]
Rate = Annotated[Decimal, Field(gt=0, allow_inf_nan=False), BeforeValidator(numeric_decimal)]
Weekday = Literal["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


class InputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class PlanningHorizon(InputModel):
    start: Timestamp
    end: Timestamp
    time_zone: Annotated[str, Field(alias="timeZone", min_length=1)]
    precision_minutes: Annotated[int, Field(alias="precisionMinutes", gt=0)]

    @field_validator("time_zone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class SkuDemand(InputModel):
    sku_id: Annotated[Identifier, Field(alias="product")]
    demand_units: Annotated[int, Field(alias="quantityUnits", ge=0)]


class EligibleProduct(InputModel):
    sku_id: Annotated[Identifier, Field(alias="product")]
    capacity_units_per_hour: Annotated[Rate, Field(alias="capacityUnitsPerHour")]


class FillingLine(InputModel):
    line_id: Annotated[Identifier, Field(alias="line")]
    eligible_products: Annotated[list[EligibleProduct], Field(alias="eligibleProducts")]


class ShiftBreak(InputModel):
    start_time: Annotated[ClockTime, Field(alias="startTime")]
    end_time: Annotated[ClockTime, Field(alias="endTime")]
    starts_next_day: Annotated[bool, Field(alias="startsNextDay")] = False


class Shift(InputModel):
    code: Identifier
    start_time: Annotated[ClockTime, Field(alias="startTime")]
    end_time: Annotated[ClockTime, Field(alias="endTime")]
    ends_next_day: Annotated[bool, Field(alias="endsNextDay")] = False
    breaks: list[ShiftBreak]


class Calendar(InputModel):
    applies_to_lines: Annotated[list[Identifier], Field(alias="appliesToLines")]
    working_days: Annotated[list[Weekday], Field(alias="workingDays")]
    shifts: list[Shift]


class SchedulingInput(InputModel):
    id: Identifier
    description: str
    planning_horizon: Annotated[PlanningHorizon, Field(alias="planningHorizon")]
    demand: list[SkuDemand]
    lines: list[FillingLine]
    calendar: Calendar
    changeover_matrix_minutes: Annotated[
        dict[Identifier, dict[Identifier, Minutes]], Field(alias="changeoverMatrixMinutes")
    ]
