from html.parser import HTMLParser

import pytest

from filling_scheduler.gantt import render_html, sku_color
from filling_scheduler.schedule import Schedule
from test_milp import example, calendar_example
from test_schedule import generated


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
    assert "01:40" in html


def test_stable_sku_colors_and_escaping():
    name = '<script>alert("x")</script>&'
    _, schedule = generated(example({name: 150}))
    html = render_html(schedule)
    assert html.count(f'fill="{sku_color(name)}"') == 2
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'data-label="&lt;script&gt;alert(&quot;x&quot;)' in html
    assert sku_color(name) == sku_color(name)


def test_empty_chart_and_invalid_duration():
    _, empty = generated(example({"A": 0}))
    assert "Расписание пустое" in render_html(empty)
    assert "L1" in render_html(empty)
    _, schedule = generated(example())
    raw = schedule.model_dump(mode="json", by_alias=True)
    raw["lines"][0]["slots"][0]["end"] = raw["lines"][0]["slots"][0]["start"]
    with pytest.raises(ValueError):
        render_html(Schedule.model_validate(raw))
