"""Semantic validation and a shared, event-based production calendar."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from filling_scheduler.enums import ErrorCode
from filling_scheduler.errors import ApplicationError
from filling_scheduler.models import SchedulingInput


def invalid(message: str, path: str = "$") -> None:
    raise ApplicationError(ErrorCode.PROBLEM_INVALID, message, details=[{"path": path}])


def unique(values: list[str], path: str) -> None:
    if len(values) != len(set(values)):
        invalid("Identifiers must be unique", path)


def local_time(day: date, clock: str, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, time.fromisoformat(clock))
    candidates = set()
    for fold in (0, 1):
        utc = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(utc)
    if len(candidates) != 1:
        invalid("Ambiguous or nonexistent local calendar boundary", "calendar")
    return candidates.pop()


def changeover_lower_bound(source: SchedulingInput) -> dict:
    """Structural lower bound in minutes, without solving or assuming calendar feasibility.

    inspect accepts schema-valid inputs before semantic validation, so ambiguous
    identifiers, missing transitions or uncovered demand yield an unavailable bound.
    """
    sku_ids = [d.sku_id for d in source.demand]
    line_ids = [line.line_id for line in source.lines]
    if len(set(sku_ids)) != len(sku_ids) or len(set(line_ids)) != len(line_ids):
        return {"lower_bound_minutes": None, "reason": "Duplicate product or line identifiers"}
    active = {d.sku_id for d in source.demand if d.demand_units > 0}
    eligible = [{p.sku_id for p in line.eligible_products if p.sku_id in active} for line in source.lines]
    if active - set().union(*eligible):
        return {"lower_bound_minutes": None, "reason": "Active product has no eligible line"}
    transitions = {(a, b) for products in eligible for a in products for b in products if a != b}
    matrix = source.changeover_matrix_minutes
    if any(a not in matrix or b not in matrix[a] for a, b in transitions):
        return {"lower_bound_minutes": None, "reason": "Missing eligible transition duration"}
    usable_lines = sum(bool(products) for products in eligible)
    minimum_count = max(0, len(active) - usable_lines)
    shortest = min((matrix[a][b] for a, b in transitions), default=0)
    return {"lower_bound_minutes": minimum_count * shortest, "active_products": len(active),
            "eligible_lines": usable_lines, "minimum_transition_count": minimum_count,
            "minimum_transition_minutes": shortest}


@dataclass(frozen=True)
class WorkWindow:
    start: int
    end: int
    cumulative: int


@dataclass(frozen=True)
class Problem:
    source: SchedulingInput
    start: datetime  # UTC: subtraction must measure elapsed, not naive local time.
    horizon: int
    precision: int
    demand: dict[str, int]
    lines: tuple[str, ...]
    units_per_tick: dict[tuple[str, str], int | Fraction]
    changeover: dict[tuple[str, str], int]
    windows: tuple[WorkWindow, ...]

    def timestamp(self, tick: int) -> str:
        return (self.start + timedelta(minutes=tick * self.precision)).astimezone(
            ZoneInfo(self.source.planning_horizon.time_zone)
        ).isoformat()

    def tick(self, value: datetime) -> int:
        seconds = (value.astimezone(timezone.utc) - self.start).total_seconds()
        tick, remainder = divmod(seconds, self.precision * 60)
        if remainder:
            raise ValueError("Timestamp is outside the precision grid")
        return int(tick)

    @property
    def available_ticks(self) -> int:
        return sum(w.end - w.start for w in self.windows)


def prepare_problem(source: SchedulingInput) -> Problem:
    horizon = source.planning_horizon
    zone = ZoneInfo(horizon.time_zone)
    start, end = horizon.start.astimezone(timezone.utc), horizon.end.astimezone(timezone.utc)
    step = horizon.precision_minutes * 60
    for value in (horizon.start, horizon.end):
        if value.utcoffset() != value.astimezone(zone).utcoffset():
            invalid("Timestamp offset disagrees with timeZone", "planningHorizon")
    seconds = (end - start).total_seconds()
    if seconds <= 0 or seconds % step:
        invalid("Horizon must have positive duration aligned to precision", "planningHorizon")
    sku_ids = [d.sku_id for d in source.demand]
    lines = [line.line_id for line in source.lines]
    unique(sku_ids, "demand")
    unique(lines, "lines")
    calendar = source.calendar
    unique(calendar.applies_to_lines, "calendar.appliesToLines")
    unique(calendar.working_days, "calendar.workingDays")
    unique([shift.code for shift in calendar.shifts], "calendar.shifts")
    if set(calendar.applies_to_lines) != set(lines):
        invalid("The shared calendar must cover exactly the declared lines", "calendar.appliesToLines")
    demand = {d.sku_id: d.demand_units for d in sorted(source.demand, key=lambda d: d.sku_id) if d.demand_units}
    rates = {}
    for line in source.lines:
        unique([p.sku_id for p in line.eligible_products], f"lines.{line.line_id}")
        for product in line.eligible_products:
            if product.sku_id not in sku_ids:
                invalid("Eligibility references an unknown product", f"lines.{line.line_id}")
            # Decimal -> Fraction preserves the input exactly, including rates
            # below one unit per tick. Do not round the physical line speed.
            units = Fraction(product.capacity_units_per_hour) * horizon.precision_minutes / 60
            if product.sku_id in demand:
                rates[product.sku_id, line.line_id] = units.numerator if units.denominator == 1 else units
    for sku in demand:
        if not any(pair[0] == sku for pair in rates):
            invalid("Active product has no eligible line", f"demand.{sku}")
    matrix = source.changeover_matrix_minutes
    if set(matrix) != set(sku_ids) or any(set(row) != set(sku_ids) for row in matrix.values()):
        invalid("Changeover matrix must cover all declared products", "changeoverMatrixMinutes")
    for sku, row in matrix.items():
        if row[sku] != 0:
            invalid("Diagonal changeover must be zero", "changeoverMatrixMinutes")
        if any(value % horizon.precision_minutes for value in row.values()):
            raise ApplicationError(ErrorCode.UNSUPPORTED_PRECISION, "Changeovers must align to planning ticks",
                                   details=[{"path": f"changeoverMatrixMinutes.{sku}"}])

    intervals = []
    shifts = []
    day = start.astimezone(zone).date() - timedelta(days=1)
    last_day = end.astimezone(zone).date()
    # Validate each shift template even when its weekday is absent from this horizon.
    for shift in calendar.shifts:
        begin = time.fromisoformat(shift.start_time)
        finish = time.fromisoformat(shift.end_time)
        if (not shift.ends_next_day and finish <= begin) or (shift.ends_next_day and finish > begin):
            invalid("Shift must span more than zero and at most 24 hours", f"calendar.{shift.code}")
        shift_minutes = (finish.hour * 60 + finish.minute + 1440 * shift.ends_next_day) - (begin.hour * 60 + begin.minute)
        breaks = []
        for pause in shift.breaks:
            b = time.fromisoformat(pause.start_time)
            e = time.fromisoformat(pause.end_time)
            a = b.hour * 60 + b.minute + 1440 * pause.starts_next_day - (begin.hour * 60 + begin.minute)
            z = e.hour * 60 + e.minute + 1440 * pause.starts_next_day - (begin.hour * 60 + begin.minute)
            if e < b:
                z += 1440
            if not 0 <= a < z <= shift_minutes:
                invalid("Break must lie inside its shift", f"calendar.{shift.code}.breaks")
            breaks.append((a, z))
        breaks.sort()
        if any(left[1] > right[0] for left, right in zip(breaks, breaks[1:])):
            invalid("Breaks overlap", f"calendar.{shift.code}.breaks")

    while day <= last_day:
        if day.strftime("%A").upper() in calendar.working_days:
            for shift in calendar.shifts:
                a = local_time(day, shift.start_time, zone)
                b = local_time(day + timedelta(days=shift.ends_next_day), shift.end_time, zone)
                shifts.append((a, b))
                pauses = []
                for pause in shift.breaks:
                    pause_day = day + timedelta(days=pause.starts_next_day)
                    pa = local_time(pause_day, pause.start_time, zone)
                    end_day = pause_day + timedelta(days=pause.end_time < pause.start_time)
                    pb = local_time(end_day, pause.end_time, zone)
                    pauses.append((pa, pb))
                cursor = a
                for pa, pb in sorted(pauses):
                    intervals.append((cursor, pa))
                    cursor = pb
                intervals.append((cursor, b))
        day += timedelta(days=1)
    shifts.sort()
    if any(a[1] > b[0] for a, b in zip(shifts, shifts[1:])):
        invalid("Shifts overlap", "calendar.shifts")
    windows = []
    cumulative = 0
    for a, b in sorted(intervals):
        a, b = max(a, start), min(b, end)
        if a >= b:
            continue
        offsets = [(value - start).total_seconds() for value in (a, b)]
        if any(value % step for value in offsets):
            raise ApplicationError(ErrorCode.UNSUPPORTED_PRECISION, "Calendar boundaries must align to planning ticks",
                                   details=[{"path": "calendar"}])
        left, right = (int(value / step) for value in offsets)
        # Adjacent windows have no actual pause and should become one physical slot.
        if windows and windows[-1].end == left:
            previous = windows.pop()
            windows.append(WorkWindow(previous.start, right, previous.cumulative))
        else:
            windows.append(WorkWindow(left, right, cumulative))
        cumulative += right - left
    return Problem(source, start, int(seconds / step), horizon.precision_minutes, demand,
                   tuple(sorted(lines)), dict(sorted(rates.items())),
                   {(a, b): matrix[a][b] // horizon.precision_minutes for a in demand for b in demand},
                   tuple(windows))
