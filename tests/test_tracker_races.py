import shutil
import subprocess
import pytest


@pytest.mark.parametrize('scenario',['success','error','json','input','invalid','loading','lifecycle','object','null','string','mixed','blocked','write_blocked','current_error','http_error','current_json_error','same_name','cleanup'])
def test_shipped_tracker_controller(scenario):
    result=subprocess.run([shutil.which('node') or '/opt/homebrew/bin/node','tests/tracker_harness.cjs',scenario],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
