import shutil
import subprocess
import pytest


@pytest.mark.parametrize('scenario',['success','old_error','body','busy','failure','lifecycle','delegation','keyboard','space','http_error','invalid','same_date'])
def test_shipped_training_detail_controller(scenario):
    result=subprocess.run([shutil.which('node') or '/opt/homebrew/bin/node','tests/training_detail_harness.cjs',scenario],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
