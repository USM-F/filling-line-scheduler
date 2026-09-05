def test_parent_import_does_not_initialize_highs():
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c", "import filling_scheduler.cli, sys; assert 'highspy' not in sys.modules"], check=True)
