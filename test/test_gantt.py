from html.parser import HTMLParser
from datetime import datetime

import pytest

from filling_scheduler.gantt import render_html, sku_color, timing_summary
from filling_scheduler.schedule import Schedule
from data_generators import example, calendar_example
from data_generators import generated
from data_generators import lunch_example


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_gantt_slots_lines_and_offline_resources():
    _, schedule = generated(calendar_example())
    html = render_html(schedule)
    elements = Elements(html).elements
    blocks = [a for tag, a in elements if tag == "g" and "slot" in a.get("class", "")]
    assert len(blocks) == schedule.summary.slot_count
    assert {a["data-line"] for a in blocks} == {l.line_id for l in schedule.lines}
    assert all(float(a["width"]) > 0 for tag, a in elements if tag == "rect")
    assert "url(#setup)" in html
    assert all("src" not in a and "href" not in a for _, a in elements)
    assert "<script" not in html
    assert "Дата начала: 17.08.2026\nВремя начала: 08:00:00" in html
    end = max(s.end for line in schedule.lines for s in line.slots)
    assert f"Дата окончания: {end:%d.%m.%Y}\nВремя окончания: {end:%H:%M:%S}" in html
    assert "Количество:" in html
    assert "Длительность:" in html


def test_stable_sku_colors_and_escaping():
    name = '<script>alert("x")</script>&'
    _, schedule = generated(example({name: 150}))
    html = render_html(schedule)
    assert html.count(f'fill="{sku_color(name)}"') == 2
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'data-label="&lt;script&gt;alert(&quot;x&quot;)' in html


def test_empty_chart_and_invalid_duration():
    problem, empty = generated(example({"A": 0}))
    assert "Расписание пустое" in render_html(empty)
    assert "L1" in render_html(empty)
    assert timing_summary(empty, problem) == dict(changeover_count=0, makespan_minutes=0,
        fully_in_break=0, partly_in_break=0, break_minutes=0)
    _, schedule = generated(example())
    raw = schedule.model_dump(mode="json", by_alias=True)
    raw["lines"][0]["slots"][0]["end"] = raw["lines"][0]["slots"][0]["start"]
    with pytest.raises(ValueError):
        render_html(Schedule.model_validate(raw))


@pytest.mark.parametrize("start,end,full,partial,minutes", [
    ("08:30", "09:00", 0, 0, 0),
    ("10:00", "10:30", 1, 0, 30),
    ("09:50", "10:20", 0, 1, 20),
    ("10:50", "11:20", 0, 1, 10),
    ("09:50", "11:10", 0, 1, 60),
    ("09:30", "10:00", 0, 0, 0),
    ("11:00", "11:30", 0, 0, 0),
])
def test_calendar_statistics_count_full_and_partial_overlaps(start, end, full, partial, minutes):
    def timestamp(clock):
        return f"2026-08-17T{clock}:00+00:00"
    duration = int((datetime.fromisoformat(timestamp(end)) - datetime.fromisoformat(timestamp(start))).total_seconds() / 60)
    schedule = Schedule.model_validate({"scheduleId": "statistics", "timeZone": "UTC",
        "lines": [{"line": "L1", "slots": [
            {"type": "changeover", "start": timestamp(start), "end": timestamp(end),
             "fromProduct": "A", "toProduct": "B", "durationMinutes": duration},
            {"type": "production", "start": timestamp("11:30"), "end": timestamp("12:00"),
             "product": "B", "quantityUnits": 30}]}],
        "summary": {"producedByProduct": {"B": 30}, "totalChangeoverMinutes": duration,
                    "productsSplitAcrossMultipleLines": 0, "slotCount": 2}})
    problem = lunch_example()
    assert timing_summary(schedule, problem) == dict(changeover_count=1, makespan_minutes=240,
        fully_in_break=full, partly_in_break=partial, break_minutes=minutes)
    html = render_html(schedule, problem)
    assert '<b>240 мин</b> Makespan' in html
    assert f'<b>{full}</b> переналадок полностью в перерывах' in html
    assert f'<b>{partial}</b> переналадок частично в перерывах' in html
    assert f'<b>{minutes}</b> мин переналадок в перерывах' in html
    assert '<b>1</b> переналадок' in html


def test_render_without_calendar_does_not_infer_breaks_from_idle_gaps():
    _, schedule = generated(calendar_example())
    stats = timing_summary(schedule, None)
    assert stats["changeover_count"] == 2
    assert stats["fully_in_break"] is None
    assert stats["makespan_minutes"] is None
    assert "недоступны без календаря" in render_html(schedule)


def test_render_rejects_slots_outside_supplied_horizon():
    _, schedule = generated(calendar_example())
    with pytest.raises(ValueError, match="horizon"):
        render_html(schedule, lunch_example())


@pytest.mark.parametrize("empty", [False, True])
def test_gantt_orders_numbered_lines_naturally(empty):
    _, schedule = generated(example({"A": 0} if empty else None))
    raw = schedule.model_dump(mode="json", by_alias=True)
    original = raw["lines"]
    raw["lines"] = [{"line": "L10", "slots": []}, original[1],
                    {"line": "L13", "slots": []}, original[0], {"line": "L3", "slots": []}]
    schedule = Schedule.model_validate(raw)
    html = render_html(schedule)
    if empty:
        assert "Линии: L1, L2, L3, L10, L13" in html
    else:
        positions = [html.index(f'>{line}</text>') for line in ["L1", "L2", "L3", "L10", "L13"]]
        assert positions == sorted(positions)
    assert [line.line_id for line in schedule.lines] == ["L10", "L2", "L13", "L1", "L3"]
