# Filling-Line Scheduling Assignment

Build a small solution that accepts production demand and static scheduling facts in JSON format, then returns a generated filling-line schedule in JSON format.

## Input

Use `filling_only_20_product_assignment_input.json`.

The input contains:

- `planningHorizon`: weekly scheduling window, time zone, and precision.
- `demand`: products `A` to `T` with required `quantityUnits`.
- `lines`: filling lines `L1`, `L2`, `L3`, and so on, with eligible products and `capacityUnitsPerHour`.
- `calendar`: shared six-day working calendar with day and night shifts and lunch breaks.
- `changeoverMatrixMinutes`: full product-to-product changeover matrix in minutes.

## Task

Generate a weekly schedule that:

- Produces the full required quantity for every product.
- Assigns products only to eligible filling lines.
- Respects line capacity, working shifts, and breaks.
- Reserves changeover time before switching products on the same line.
- Places changeover time in the gap between shifts, during lunch breaks, or during shift working hours.
- Minimizes total changeover time.
- Keeps each product on as few lines as practical.
- Avoids fragmented sequences such as A-B-A-B-A.

## Output

Return JSON with ordered production and changeover time slots per line. Times should be ISO 8601 strings in the input time zone.

```json
{
  "scheduleId": "candidate-001",
  "timeZone": "Asia/Bangkok",
  "lines": [
    {
      "line": "L1",
      "slots": [
        {
          "type": "production",
          "start": "2026-08-17T08:30:00+07:00",
          "end": "2026-08-17T12:30:00+07:00",
          "product": "A",
          "quantityUnits": 38400
        },
        {
          "type": "changeover",
          "start": "2026-08-17T12:30:00+07:00",
          "end": "2026-08-17T13:00:00+07:00",
          "fromProduct": "A",
          "toProduct": "B",
          "durationMinutes": 30
        },
        {
          "type": "production",
          "start": "2026-08-17T13:30:00+07:00",
          "end": "2026-08-17T15:30:00+07:00",
          "product": "B",
          "quantityUnits": 19200
        }
      ]
    }
  ],
  "summary": {
    "producedByProduct": {
      "A": 38400,
      "B": 19200
    },
    "totalChangeoverMinutes": 30,
    "productsSplitAcrossMultipleLines": 0,
    "slotCount": 3
  }
}
```

Changeover slots must use `type: "changeover"`, `fromProduct`, `toProduct`, and `durationMinutes`.

## Deliverables

- Short README describing the solution, setup, and how to run it.
- Generated schedule in the JSON format above.
- Source code for the scheduler and validation helpers.
- Public GitHub repository containing the artifacts, preferred over a zip archive.
