import logging
from contextlib import contextmanager
from time import perf_counter_ns
from typing import Iterator

from filling_scheduler.domain.enums import EventName, StageName

logger = logging.getLogger(__name__)


@contextmanager
def timed_stage(stage: StageName) -> Iterator[None]:
    started = perf_counter_ns()
    event = EventName.STAGE_COMPLETED
    try:
        yield
    except Exception:
        event = EventName.STAGE_FAILED
        raise
    finally:
        logger.debug(event, extra={"fields": {
            "stage": stage, "elapsed_ms": (perf_counter_ns() - started) / 1_000_000,
        }})
