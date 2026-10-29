"""Real subprocess coverage of OS exclusion, crash release and delegation."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import psutil

from common.job_lock import JobBusyError, delegated_env, job_lock, job_status
from common.processes import terminate_child_tree

ROOT = Path(__file__).resolve().parents[1]

def stop_child_tree(process):
    try:
        parent=psutil.Process(process.pid)
        children=parent.children(recursive=True)+[parent]
    except psutil.NoSuchProcess:return
    for child in reversed(children):
        try:child.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(children,timeout=5)
    for child in alive:
        try:child.kill()
        except psutil.NoSuchProcess:pass
    process.wait(timeout=5)
TRY_LOCK = """
import sys
from common.job_lock import job_lock, JobBusyError
try:
    with job_lock('child',lock_dir=sys.argv[1]):print('ACQUIRED',flush=True)
except JobBusyError:print('BUSY',flush=True)
"""


def child(script, path, env=None):
    return subprocess.run([sys.executable, '-u', '-c', script, str(path)], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=15, check=True).stdout.strip()


def test_threads_reenter_and_lock_file_persists(tmp_path):
    def worker(_):
        with job_lock('worker',lock_dir=tmp_path):
            return job_status(lock_dir=tmp_path)['operation']
    with job_lock('outer',lock_dir=tmp_path):
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(worker,range(4)))==['outer']*4
        assert job_status(lock_dir=tmp_path)['pid']==os.getpid()
    assert (tmp_path/'.job.lock').exists()
    assert job_status(lock_dir=tmp_path) is None


def test_unrelated_process_rejected_even_with_bad_lease(tmp_path):
    with job_lock('owner',lock_dir=tmp_path):
        assert child(TRY_LOCK,tmp_path)=='BUSY'
        env=dict(os.environ,BLOCKFL_JOB_LEASE='forged-token')
        assert child(TRY_LOCK,tmp_path,env)=='BUSY'


def test_partial_metadata_never_breaks_real_lock(tmp_path):
    with job_lock('owner',lock_dir=tmp_path):
        (tmp_path/'.job-status.json').write_text('{',encoding='utf-8')
        assert child(TRY_LOCK,tmp_path)=='BUSY'
        assert job_status(lock_dir=tmp_path)['busy'] is True


def test_exception_releases_os_lock(tmp_path):
    with pytest.raises(ValueError):
        with job_lock('exception',lock_dir=tmp_path):raise ValueError('expected')
    assert child(TRY_LOCK,tmp_path)=='ACQUIRED'


def test_batch_child_inherits_valid_lease_but_does_not_release_parent(tmp_path):
    with job_lock('batch',lock_dir=tmp_path):
        env=delegated_env(lock_dir=tmp_path)
        assert child(TRY_LOCK,tmp_path,env)=='ACQUIRED'
        # Child exit must not release its parent's OS mutex.
        assert child(TRY_LOCK,tmp_path)=='BUSY'
        assert job_status(lock_dir=tmp_path)['operation']=='batch'


def test_owner_crash_releases_despite_stale_metadata(tmp_path):
    script="""
import sys,time
from common.job_lock import job_lock
with job_lock('crash',lock_dir=sys.argv[1]):
    print('LOCKED',flush=True)
    time.sleep(30)
"""
    process=subprocess.Popen([sys.executable,'-u','-c',script,str(tmp_path)],cwd=ROOT,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='LOCKED'
        assert job_status(lock_dir=tmp_path)['busy'] is True
        # The venv executable on Windows is a redirector. Kill the actual
        # recorded lock owner, then reap the redirector, to test owner crash.
        owner=psutil.Process(job_status(lock_dir=tmp_path)['pid'])
        owner.terminate();owner.wait(timeout=5)
        process.wait(timeout=5)
        assert job_status(lock_dir=tmp_path) is None
        assert child(TRY_LOCK,tmp_path)=='ACQUIRED'
    finally:
        if process.poll() is None:stop_child_tree(process)


def test_correct_token_from_non_descendant_is_rejected(tmp_path):
    # The contender is a sibling of owner, not an owner descendant. Possession
    # of an environment token by itself must not authorize a second CLI.
    script="""
import sys,time
from common.job_lock import job_lock,delegated_env
with job_lock('owner',lock_dir=sys.argv[1]):
    print(delegated_env(lock_dir=sys.argv[1])['BLOCKFL_JOB_LEASE'],flush=True)
    time.sleep(30)
"""
    process=subprocess.Popen([sys.executable,'-u','-c',script,str(tmp_path)],cwd=ROOT,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        token=process.stdout.readline().strip()
        assert token
        assert child(TRY_LOCK,tmp_path,dict(os.environ,BLOCKFL_JOB_LEASE=token))=='BUSY'
    finally:
        stop_child_tree(process)


def test_delegated_worker_rechecks_parent_lease_before_nested_write(tmp_path):
    script="""
import sys
from common.job_lock import job_lock,JobLeaseLostError
with job_lock('worker',lock_dir=sys.argv[1]):
    print('ENTERED',flush=True)
    sys.stdin.readline()
    try:
        with job_lock('write',lock_dir=sys.argv[1]):print('BAD',flush=True)
    except JobLeaseLostError:print('LOST',flush=True)
"""
    process=None
    try:
        with job_lock('batch',lock_dir=tmp_path):
            process=subprocess.Popen([sys.executable,'-u','-c',script,str(tmp_path)],cwd=ROOT,
                                     env=delegated_env(lock_dir=tmp_path),stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            assert process.stdout.readline().strip()=='ENTERED'
        output,error=process.communicate('continue\n',timeout=10)
        assert process.returncode==0,error
        assert output.strip()=='LOST'
    finally:
        if process is not None and process.poll() is None:stop_child_tree(process)


def test_terminate_popen_tree_includes_actual_venv_interpreter(tmp_path):
    script="""
import os,time
print(os.getpid(),flush=True)
time.sleep(30)
"""
    process=subprocess.Popen([sys.executable,'-u','-c',script],cwd=ROOT,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        actual_pid=int(process.stdout.readline().strip())
        terminated=terminate_child_tree(process,timeout=5)
        assert actual_pid in terminated
        assert process.poll() is not None
        assert not psutil.pid_exists(actual_pid)
    finally:
        if process.poll() is None:stop_child_tree(process)


def test_dashboard_forwards_pipelined_but_baseline_stays_sync(tmp_path,monkeypatch):
    from dashboard import data
    (tmp_path/'experiments').mkdir()
    (tmp_path/'experiments/run.py').write_text('# entrypoint fixture')
    monkeypatch.setattr(data,'ROOT',tmp_path)
    monkeypatch.setattr(data,'RESULTS',tmp_path/'results')
    monkeypatch.setattr(data,'job_status',lambda:None)
    captured=[]
    def start(argv,**kwargs):
        captured.append(argv)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(data.subprocess,'Popen',start)
    data.launch_training('blockchain',1,42,512,128,'pipelined')
    data.launch_training('baseline',1,42,512,128,'pipelined')
    assert [argv[argv.index('--audit-strategy')+1] for argv in captured]==['pipelined','sync']


def test_batch_parser_accepts_pipelined_while_holding_job_lock(tmp_path,monkeypatch):
    from experiments import batch
    captured=[]
    monkeypatch.setattr(sys,'argv',['batch','--audit-strategy','pipelined','--name','test-batch'])
    monkeypatch.setattr(batch,'job_lock',lambda operation:job_lock(operation,lock_dir=tmp_path))
    def run(args):
        captured.append(args.audit_strategy)
        assert job_status(lock_dir=tmp_path)['operation']=='batch:test-batch'
    monkeypatch.setattr(batch,'run_batch',run)
    batch.main()
    assert captured==['pipelined']
