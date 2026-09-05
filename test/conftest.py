import json
from pathlib import Path

import pytest


@pytest.fixture
def input_path() -> Path:
    return Path(__file__).parent / "data/filling_only_20_product_assignment_input.json"


@pytest.fixture
def input_data(input_path: Path) -> dict:
    return json.loads(input_path.read_text())
