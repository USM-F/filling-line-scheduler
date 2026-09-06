"""Offline HTML/SVG view of the public schedule contract."""

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import re
from zoneinfo import ZoneInfo

from filling_scheduler.schedule import Schedule
from filling_scheduler.problem import Problem


def sku_color(sku: str) -> str:
    hue = int.from_bytes(sha256(sku.encode("utf-8")).digest()[:4], "big") % 360
    return f"hsl({hue}, 62%, 45%)"


def line_sort_key(line):
    # Compare digit groups numerically, preserving arbitrary identifier prefixes.
    parts = re.split(r"(\d+)", line.line_id)
    return tuple((1, int(part)) if index % 2 else (0, part) for index, part in enumerate(parts)), line.line_id


def timing_summary(schedule: Schedule, problem: Problem | None) -> dict:
    """Calendar statistics from physical slots; idle gaps alone do not identify breaks."""
    slots = [slot for line in schedule.lines for slot in line.slots]
    setups = [slot for slot in slots if slot.type == "changeover"]
    result = dict(changeover_count=len(setups), makespan_minutes=None,
                  fully_in_break=None, partly_in_break=None, break_minutes=None)
    if problem is None:
        return result
    origin = problem.start.timestamp()
    step = problem.precision * 60
    windows = [(origin + w.start * step, origin + w.end * step) for w in problem.windows]
    if any(s.start.timestamp() < origin or s.end.timestamp() > origin + problem.horizon * step for s in slots):
        raise ValueError("Schedule slots fall outside the supplied planning horizon")
    ends = [s.end.timestamp() for s in slots if s.type == "production"]
    result["makespan_minutes"] = (max(ends) - origin) / 60 if ends else 0
    fully = partly = 0
    paused = 0.0
    for slot in setups:
        start, end = slot.start.timestamp(), slot.end.timestamp()
        working = sum(max(0, min(end, b) - max(start, a)) for a, b in windows)
        nonworking = end - start - working
        paused += nonworking
        fully += working == 0
        partly += working > 0 and nonworking > 0
    result.update(fully_in_break=fully, partly_in_break=partly, break_minutes=paused / 60)
    return result


def render_html(schedule: Schedule, problem: Problem | None = None) -> str:
    e = escape
    zone = ZoneInfo(schedule.time_zone)
    lines = sorted(schedule.lines, key=line_sort_key)
    slots = [slot for line in schedule.lines for slot in line.slots]
    if any(slot.end <= slot.start for slot in slots):
        raise ValueError("Cannot draw a slot with nonpositive duration")
    products = sorted({slot.sku_id for slot in slots if slot.type == "production"})
    header = f"<h1>График разлива</h1><p>{e(schedule.schedule_id)} · {e(schedule.time_zone)}</p>"
    summary = schedule.summary
    timing = timing_summary(schedule, problem)
    makespan = f'{timing["makespan_minutes"]:g} мин' if problem is not None else 'нет данных'
    header += (f'<div class="stats"><span><b>{sum(summary.produced_by_product.values()):,}</b> единиц</span>'
               f'<span><b>{makespan}</b> Makespan — от начала горизонта</span>'
               f'<span><b>{timing["changeover_count"]}</b> переналадок</span>'
               f'<span><b>{summary.total_changeover_minutes:,}</b> мин переналадок</span>'
               f'<span><b>{summary.products_split}</b> SKU на нескольких линиях</span>'
               f'<span><b>{summary.slot_count}</b> слотов</span>')
    if problem is not None:
        header += (f'<span><b>{timing["fully_in_break"]}</b> переналадок полностью в перерывах</span>'
                   f'<span><b>{timing["partly_in_break"]}</b> переналадок частично в перерывах</span>'
                   f'<span><b>{timing["break_minutes"]:g}</b> мин переналадок в перерывах</span></div>'
                   '<p>Перерывы — всё нерабочее время календаря, включая промежутки между сменами. '
                   'Полные и частичные попадания считаются отдельно.</p>')
    else:
        header += ('</div><p>Makespan и попадание переналадок в перерывы недоступны без календаря. '
                   'Для их расчёта укажите исходный файл через render --input.</p>')
    legend = '<div class="legend">' + "".join(
        f'<span><i style="background:{sku_color(sku)}"></i>{e(sku)} — {summary.produced_by_product.get(sku, 0):,} ед.</span>'
        for sku in products
    ) + '<span><i class="setup-key"></i>Переналадка</span></div>'
    drawing = '<p class="empty">Расписание пустое: производственных слотов нет.</p>'
    if slots:
        begin = min(s.start.timestamp() for s in slots)
        end = max(s.end.timestamp() for s in slots)
        span = end - begin
        plot_width = max(1000, span / 60 * 0.8)
        left, top, row_height = 130, 65, 58
        width, height = plot_width + left + 80, top + row_height * len(schedule.lines) + 20
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="График разлива" width="{width:.2f}" height="{height}">',
               '<defs><pattern id="setup" width="8" height="8" patternUnits="userSpaceOnUse">'
               '<rect width="8" height="8" fill="#d8dee8"/><path d="M-2,2 l4,-4 M0,8 l8,-8 M6,10 l4,-4" stroke="#65748b" stroke-width="2"/>'
               '</pattern></defs>']
        divisions = max(4, int(plot_width // 165))
        for index in range(divisions + 1):
            x = left + plot_width * index / divisions
            instant = datetime.fromtimestamp(begin + span * index / divisions, timezone.utc).astimezone(zone)
            svg.append(f'<path d="M{x:.2f},50 V{height-15}" stroke="#dce3ec"/>')
            svg.append(f'<text x="{x:.2f}" y="23" text-anchor="middle" class="axis">{instant:%d.%m}</text>')
            svg.append(f'<text x="{x:.2f}" y="42" text-anchor="middle" class="axis">{instant:%H:%M %z}</text>')
        for index, line in enumerate(lines):
            y = top + index * row_height
            svg.append(f'<text class="line-label" x="12" y="{y+24}">{e(line.line_id)}</text>')
            svg.append(f'<path d="M{left},{y+42} H{width-15:.2f}" stroke="#e7ecf2"/>')
            for slot in line.slots:
                x = left + (slot.start.timestamp()-begin) / span * plot_width
                bar_width = (slot.end.timestamp()-slot.start.timestamp()) / span * plot_width
                duration = (slot.end.timestamp()-slot.start.timestamp()) / 60
                if slot.type == "production":
                    label = slot.sku_id
                    detail = f"Продукт: {label}\nКоличество: {slot.quantity:,} ед."
                    fill = sku_color(label)
                else:
                    label = f"{slot.from_sku} → {slot.to_sku}"
                    detail = "Переналадка: " + label
                    fill = "url(#setup)"
                start, finish = slot.start.astimezone(zone), slot.end.astimezone(zone)
                tooltip = (f"Линия: {line.line_id}\n{detail}\n"
                           f"Дата начала: {start:%d.%m.%Y}\nВремя начала: {start:%H:%M:%S}\n"
                           f"Дата окончания: {finish:%d.%m.%Y}\nВремя окончания: {finish:%H:%M:%S}\n"
                           f"Длительность: {duration:g} мин\nЧасовой пояс: {schedule.time_zone}")
                svg.append(f'<g class="slot {slot.type}" data-line="{e(line.line_id)}" data-label="{e(label)}">'
                           f'<title>{e(tooltip)}</title><rect x="{x:.4f}" y="{y}" width="{bar_width:.6f}" height="34" rx="3" fill="{fill}"/>')
                if bar_width > 25 + len(label)*7:
                    svg.append(f'<text x="{x+6:.2f}" y="{y+22}" class="bar-label {slot.type}">{e(label)}</text>')
                svg.append('</g>')
        drawing = '<div class="chart">' + "".join(svg) + '</svg></div>'
    else:
        drawing += '<p>Линии: ' + ', '.join(e(line.line_id) for line in lines) + '</p>'
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>График разлива — {e(schedule.schedule_id)}</title><style>'
            'body{margin:0;padding:32px;font:15px system-ui,sans-serif;color:#243249;background:#f4f7fb}'
            'main{max-width:1800px;margin:auto}h1{margin:0;font-size:30px}p{color:#57677c}'
            '.stats,.legend{display:flex;flex-wrap:wrap;gap:20px;margin:24px 0}.stats span{background:white;padding:16px;border-radius:8px}'
            '.stats b{font-size:23px;display:block}.legend span{display:flex;align-items:center;gap:6px}'
            '.legend i{display:inline-block;width:16px;height:16px;border-radius:3px}'
            '.setup-key{background:repeating-linear-gradient(135deg,#d8dee8 0 4px,#65748b 4px 6px)}'
            '.chart{overflow-x:auto;background:white;padding:16px;border:1px solid #dce3ec;border-radius:8px}'
            '.axis{font-size:12px;fill:#57677c}.line-label{font-size:14px;font-weight:600;fill:#243249}'
            '.bar-label{fill:white;font-size:12px;pointer-events:none}.bar-label.changeover{fill:#243249}'
            '.slot:hover rect{stroke:#17263d;stroke-width:2}.empty{padding:30px;background:white}'
            '</style></head><body><main>' + header + drawing + legend +
            '<p>Промежутки между блоками — паузы. Наведите указатель на блок для подробностей. '
            'Диаграмма отображает данные JSON; результат независимой проверки сообщает команда validate.</p>'
            '</main></body></html>\n')
