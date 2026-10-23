import shutil
import subprocess

import pytest


@pytest.mark.parametrize('scenario', [
    'duplicate', 'busy', 'cancel', 'toggle', 'invalid', 'success',
    'transport', 'http', 'json', 'null', 'application',
])
def test_shipped_height_editor(scenario):
    result = subprocess.run(
        [shutil.which('node') or '/opt/homebrew/bin/node', 'tests/height_editor_harness.cjs', scenario],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
