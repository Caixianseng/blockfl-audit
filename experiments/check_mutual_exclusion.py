"""Verify real CLI/UI mutation entry points refuse a conflicting live job."""
import json
import os
from pathlib import Path
import subprocess
import sys
from common.job_lock import job_lock

ROOT=Path(__file__).resolve().parents[1]

def main():
    output=ROOT/'results/optimization-conflict-checks'
    output.mkdir(exist_ok=True)
    commands={
        'network_stop':['-m','network.manage','stop','--nodes','4'],
        'deploy':['-m','audit.chain','deploy','--deployment',str(output/'forbidden-deployment.json'),'--evidence-dir',str(output/'forbidden-deploy-evidence')],
        'fault':['-m','experiments.fault_test','--output-dir',str(output/'forbidden-fault')],
        'training':['-m','experiments.run','--mode','baseline','--rounds','1','--output',str(output/'blocked-training')],
        'batch':['-m','experiments.batch','--rounds','1','--name','forbidden-conflict-batch'],
        'dashboard':['-c',"from dashboard.data import launch_training;launch_training('baseline',1,42,512,128)"],
        'standalone_training':['-m','fl.runner','--output',str(output/'forbidden-direct-training'),'--rounds','1'],
        'public_training_api':['-c','from fl.runner import run_training;import sys;run_training({"rounds":1},sys.argv[1])',str(output/'forbidden-api-training')],
    }
    checks=[]
    with job_lock('mutual-exclusion-integration-check'):
        # No delegated lease is passed: even our own ordinary child CLIs must
        # refuse writes. Only the batch scheduler explicitly delegates.
        env=os.environ.copy();env.pop('BLOCKFL_JOB_LEASE',None)
        env['PYTHONUTF8']='1'
        for name,args in commands.items():
            result=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25)
            text=result.stdout+result.stderr
            (output/f'{name}.log').write_text(text,encoding='utf-8')
            passed=result.returncode!=0 and 'JobBusyError' in text
            checks.append({'entry':name,'returncode':result.returncode,'rejected':passed})
    assert not (output/'forbidden-deployment.json').exists()
    assert not (output/'forbidden-fault/fault_results.json').exists()
    assert not (output/'forbidden-direct-training').exists()
    assert not (output/'forbidden-api-training').exists()
    from network.manage import wait_ready
    nodes=wait_ready(timeout=15,poll_interval=.5)
    report={'passed':all(x['rejected'] for x in checks),'checks':checks,'network_still_ready':True,'heights':[r['block_number'] for r in nodes]}
    (output/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not report['passed']:raise AssertionError('A conflicting mutation was not rejected; inspect evidence')

if __name__=='__main__':main()
