"""Semantic validation and a shared, event-based production calendar."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    units_per_tick: dict[tuple[str, str], int]
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
            units = product.capacity_units_per_hour * horizon.precision_minutes / 60
            if units != units.to_integral_value() or units < 1:
                raise ApplicationError(ErrorCode.UNSUPPORTED_PRECISION, "Capacity must give integer units per planning tick",
                                       details=[{"path": f"lines.{line.line_id}.{product.sku_id}.capacityUnitsPerHour"}])
            if product.sku_id in demand:
                rates[product.sku_id, line.line_id] = int(units)
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
