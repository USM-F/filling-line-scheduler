"""Inspection pipeline. Scheduling stages will be connected in later increments."""

from typing import Any

from filling_scheduler.models import SchedulingInput


def inspect_input(problem: SchedulingInput) -> dict[str, Any]:
    return {
        "id": problem.id,
        "planningHorizon": problem.planning_horizon.model_dump(mode="json", by_alias=True),
        "productCount": len(problem.demand),
        "activeProductCount": sum(item.demand_units > 0 for item in problem.demand),
        "lineCount": len(problem.lines),
        "eligiblePairCount": sum(len(line.eligible_products) for line in problem.lines),
        "totalDemandUnits": sum(item.demand_units for item in problem.demand),
    }
