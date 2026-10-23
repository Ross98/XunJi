"""Exercise the shipped import controller with plain JavaScript fakes, no browser."""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which('node')
HARNESS = Path(__file__).with_name('health_import_harness.cjs')


@pytest.mark.parametrize('scenario', [
    'missing-file', 'network-retry', 'hozz-label', 'duplicate-start',
    'stale-status', 'http-error', 'busy-not-owned', 'page-leave',
    'source-disappears', 'terminal-error-resets', 'terminal-on-load', 'legacy-service', 'back-cache', 'start-status-fails',
])
def test_health_import_controller(scenario):
    if NODE is None:
        pytest.skip('Node.js is needed for shipped controller verification')
    result = subprocess.run([NODE, str(HARNESS), scenario], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
