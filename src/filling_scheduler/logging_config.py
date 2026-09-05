import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": self.run_id,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            event["traceback"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False, allow_nan=False)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = json.dumps(getattr(record, "fields", {}), ensure_ascii=False)
        return f"{record.levelname} {record.getMessage()} {fields}"


def configure_logging(run_id: str, level: str, log_file: Path | None) -> logging.Logger:
    logger = logging.getLogger("filling_scheduler")
    close_logging(logger)
    logger.propagate = False
    logger.setLevel(level)
    stderr = logging.StreamHandler()
    stderr.setFormatter(HumanFormatter())
    logger.addHandler(stderr)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter(run_id))
        logger.addHandler(file_handler)
    return logger


def close_logging(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
