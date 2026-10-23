"""Use the shipped quit probe with JavaScript fakes; no browser or network."""
from pathlib import Path
import shutil
import subprocess

import pytest

NODE=shutil.which('node')
HARNESS=r'''
const vm=require('node:vm');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const template=fs.readFileSync(process.argv[1],'utf8');
const source=template.match(/async function waitForProjectClosed\(\) \{[\s\S]*?(?=async function confirmExitProject)/)[0];
const scenario=process.argv[2];
const title={textContent:'正在关闭项目…'},message={textContent:''};
let calls=0,closed=0,id=0;
const timers=new Map();
const abort=()=>Object.assign(new Error('slow response'),{name:'AbortError'});
const context=vm.createContext({
 document:{getElementById:key=>key==='shutdownTitle'?title:message},
 Date,AbortController,
 window:{close:()=>closed++},
 setTimeout:(callback,delay)=>{const key=++id;if(delay===250) callback();else timers.set(key,{callback,delay});return key;},
 clearTimeout:key=>timers.delete(key),
 fetch:async()=>{
   calls++;
   if(scenario==='timeouts') throw abort();
   if(scenario==='abort-then-disconnect' && calls===1) throw abort();
   if(scenario==='disconnect' || scenario==='abort-then-disconnect') throw new TypeError('Failed to fetch');
   return {status:scenario==='server-error'?500:503};
 }
});
(async()=>{
 vm.runInContext(source,context);
 await context.waitForProjectClosed();
 assert.ok(!title.textContent.includes('项目已关闭'));
 assert.ok(!message.textContent.includes('后台任务和网络连接已释放'));
 if(scenario==='disconnect' || scenario==='abort-then-disconnect') {
   assert.equal(calls,scenario==='disconnect'?1:2);
   assert.match(title.textContent,/连接已断开/);
   assert.match(message.textContent,/退出请求/);
 } else {
   assert.equal(calls,30);
   assert.match(title.textContent,/退出.*(发起|发送|发出)/);
   assert.equal(closed,0);
   assert.ok(Array.from(timers.values()).every(t=>t.delay!==500));
 }
 assert.ok(Array.from(timers.values()).every(t=>t.delay!==400),'Probe deadline leaked');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario',['timeouts','responsive','server-error','disconnect','abort-then-disconnect'])
def test_shutdown_feedback_never_infers_cleanup_from_a_timeout(scenario):
    if NODE is None: pytest.skip('Node.js is needed for shipped quit probe verification')
    template=Path(__file__).parents[1]/'templates/base.html'
    result=subprocess.run([NODE,'-e',HARNESS,str(template),scenario],capture_output=True,text=True)
    assert result.returncode == 0,result.stderr
