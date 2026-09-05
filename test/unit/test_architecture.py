import ast
from pathlib import Path


def test_acceptance_and_reporting_import_boundaries():
    root = Path(__file__).parents[2] / "src/filling_scheduler"
    for package, forbidden in [
        ("validation", ("optimization", "highspy")),
        ("reporting", ("optimization", "highspy")),
    ]:
        for source in (root / package).rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or "", *(alias.name for alias in node.names)]
                assert not any(part in forbidden for name in names for part in name.split(".")), source


def test_parent_import_does_not_initialize_highs():
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c", "import filling_scheduler.cli, sys; assert 'highspy' not in sys.modules"], check=True)
