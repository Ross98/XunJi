import shutil
import subprocess

import pytest


@pytest.mark.parametrize('scenario', [
    'success', 'old_error', 'body', 'busy', 'same_month',
    'failure', 'http_error', 'invalid', 'lifecycle', 'delegation',
])
def test_shipped_calendar_month_controller(scenario):
    result = subprocess.run(
        [shutil.which('node') or '/opt/homebrew/bin/node', 'tests/calendar_month_harness.cjs', scenario],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
