"""Validate physical JSON slots; deliberately independent of MILP and HiGHS."""

from collections import defaultdict
from zoneinfo import ZoneInfo

from filling_scheduler.problem import Problem
from filling_scheduler.schedule import Schedule, summarize


def changeover_calendar_usage(problem: Problem, schedule: Schedule) -> dict:
    """Measure occupied working/nonworking minutes in an already validated schedule."""
    total = working = 0
    for line in schedule.lines:
        for slot in line.slots:
            if slot.type == "changeover":
                start, end = problem.tick(slot.start), problem.tick(slot.end)
                total += end - start
                working += sum(max(0, min(end, w.end) - max(start, w.start)) for w in problem.windows)
    return {"total_changeover_minutes": total * problem.precision,
            "working_changeover_minutes": working * problem.precision,
            "nonworking_changeover_minutes": (total - working) * problem.precision}


def validate_schedule(problem: Problem, schedule: Schedule) -> dict:
    errors = []

    def fail(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    if schedule.time_zone != problem.source.planning_horizon.time_zone:
        fail("TIME_ZONE", "timeZone", "Schedule timezone differs from input")
    zone = ZoneInfo(schedule.time_zone)
    ids = [line.line_id for line in schedule.lines]
    if len(ids) != len(set(ids)) or set(ids) != set(problem.lines):
        fail("LINES", "lines", "Schedule must contain each declared line exactly once")
    produced = defaultdict(int)
    for li, line in enumerate(schedule.lines):
        previous_end = 0
        runs = []
        pending = []
        for si, slot in enumerate(line.slots):
            path = f"lines[{li}].slots[{si}]"
            for value in (slot.start, slot.end):
                if value.utcoffset() != value.astimezone(zone).utcoffset():
                    fail("TIME_ZONE", path, "Timestamp offset disagrees with timezone")
            try:
                a, b = problem.tick(slot.start), problem.tick(slot.end)
            except ValueError:
                fail("GRID", path, "Timestamp is outside the planning grid")
                continue
            if not 0 <= a < b <= problem.horizon:
                fail("HORIZON", path, "Slot must have positive duration inside the horizon")
            if a < previous_end:
                fail("OVERLAP", path, "Slots must be ordered and disjoint")
            previous_end = b
            if slot.type == "changeover":
                if (b-a)*problem.precision != slot.duration_minutes:
                    fail("CHANGEOVER_DURATION", path, "Declared duration differs from timestamps")
                pending.append(slot)
                continue
            sku = slot.sku_id
            produced[sku] += slot.quantity
            units = problem.units_per_tick.get((sku, line.line_id))
            if units is None:
                fail("ELIGIBILITY", path, "Product is inactive, unknown, or incompatible with line")
            elif not (b-a-1)*units < slot.quantity <= (b-a)*units:
                fail("CAPACITY", path, "Only the last tick of production may be partially filled")
            if not any(w.start <= a < b <= w.end for w in problem.windows):
                fail("CALENDAR", path, "Production slot must lie inside one work window")
            if runs and runs[-1]["sku"] == sku:
                last = runs[-1]
                if pending:
                    fail("CHANGEOVER_SEQUENCE", path, "No changeover is allowed within a run")
                if any(max(last["end"], w.start) < min(a, w.end) for w in problem.windows):
                    fail("RUN_GAP", path, "A run may pause only during nonworking time")
                if last["last_capacity"] is not None and last["last_quantity"] != last["last_capacity"]:
                    fail("ROUNDING", path, "A partial tick is allowed only at the end of a run")
                last.update(end=b, last_quantity=slot.quantity, last_capacity=(b-a)*units if units else None)
            else:
                if any(r["sku"] == sku for r in runs):
                    fail("FRAGMENTATION", path, "Product returns to a line after another product")
                expected = problem.changeover.get((runs[-1]["sku"], sku), 0) if runs else 0
                if expected:
                    if len(pending) != 1 or pending[0].from_sku != runs[-1]["sku"] or pending[0].to_sku != sku or pending[0].duration_minutes != expected * problem.precision:
                        fail("CHANGEOVER_SEQUENCE", path, "Missing or incorrect transition between products")
                elif pending:
                    fail("CHANGEOVER_SEQUENCE", path, "Unexpected changeover before the first run or a zero-cost transition")
                runs.append(dict(sku=sku, end=b, last_quantity=slot.quantity, last_capacity=(b-a)*units if units else None))
            pending = []
        if pending:
            fail("CHANGEOVER_SEQUENCE", f"lines[{li}]", "Trailing changeover has no subsequent production")
    expected_demand = {d.sku_id: d.demand_units for d in problem.source.demand}
    if any(produced[sku] != q for sku, q in expected_demand.items()) or set(produced) - set(expected_demand):
        fail("DEMAND", "lines", "Total production across all lines must equal demand")
    lines = [line.model_dump(mode="json", by_alias=True) for line in schedule.lines]
    expected_summary = summarize(lines, expected_demand)
    if schedule.summary.model_dump(by_alias=True) != expected_summary:
        fail("SUMMARY", "summary", "Summary disagrees with physical slots")
    return {"valid": not errors, "scheduleId": schedule.schedule_id, "errors": errors, "summary": expected_summary}
