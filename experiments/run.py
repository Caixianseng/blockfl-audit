"""CLI connecting real local Flower training to the Besu audit contract."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from common.job_lock import job_lock

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('FLWR_TELEMETRY_ENABLED', '0')

def write_json(path, obj):
    path=Path(path)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)

def ledger_bytes():
    total=0
    for p in (ROOT/'network/nodes').rglob('*'):
        if p.name=='besu.log':continue
        try:
            if p.is_file():total+=p.stat().st_size
        except FileNotFoundError:
            pass # RocksDB may replace SST files during compaction.
    return total

class ChainHook:
    def __init__(self, chain, run_id, output):
        self.chain, self.run_id, self.output = chain, run_id, output

    def begin_round(self, round_id):
        return self.chain.begin_round(self.run_id, round_id)

    def submit(self, client_id, round_id, digest):
        return self.chain.submit_update(self.run_id, round_id, client_id+1, digest)

    def submit_async(self, client_id, round_id, digest):
        return self.chain.submit_update_async(self.run_id, round_id, client_id+1, digest)

    def confirm(self, ticket):
        return self.chain.confirm_submission(ticket)

    def verify(self, client_id, round_id, digest):
        committed = self.chain.get_update_hash(self.run_id, round_id, client_id+1)
        ok = committed.removeprefix('0x').lower() == digest.removeprefix('0x').lower()
        with (self.output/'verifications.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'round':round_id, 'client_id':client_id, 'local_hash':digest,
                                  'chain_hash':committed, 'matched':ok, 'source':'eth_call'})+'\n')
        return ok

    def commit(self, round_id, digest):
        record = self.chain.commit_aggregate(self.run_id, round_id, digest)
        if self.chain.get_aggregate_hash(self.run_id, round_id).removeprefix('0x').lower() != digest.lower():
            raise RuntimeError('Aggregate commitment readback mismatch')
        return record

class ResourceSampler:
    def __init__(self, output):
        self.output = output
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        import psutil
        own = psutil.Process()
        processes = [('training', own)]
        pidfile = ROOT/'network/pids.json'
        if pidfile.exists():
            for i,pid in json.loads(pidfile.read_text()).items():
                try: processes.append((f'validator_{int(i)+1}', psutil.Process(pid)))
                except psutil.NoSuchProcess: pass
        for _,p in processes:
            try: p.cpu_percent()
            except psutil.Error: pass
        start = time.monotonic()
        with (self.output/'resources.csv').open('w', newline='', encoding='utf-8') as out:
            w=csv.DictWriter(out,fieldnames=['elapsed_seconds','role','cpu_percent','rss_bytes'])
            w.writeheader()
            while not self.stop_event.wait(0.5):
                for role,p in processes:
                    try: w.writerow({'elapsed_seconds':time.monotonic()-start,'role':role,'cpu_percent':p.cpu_percent(),'rss_bytes':p.memory_info().rss})
                    except psutil.Error: pass
                out.flush()

    def __enter__(self): self.thread.start(); return self
    def __exit__(self,*args): self.stop_event.set(); self.thread.join(timeout=3)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['baseline','blockchain'],default='baseline')
    p.add_argument('--audit-strategy',choices=['sync','pipelined'],default='sync')
    p.add_argument('--study-id',default=None)
    p.add_argument('--rounds',type=int,default=10)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--train-limit',type=int,default=0)
    p.add_argument('--test-limit',type=int,default=0)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='auto')
    p.add_argument('--rpc-url',default='http://127.0.0.1:8545')
    p.add_argument('--manage-network',action='store_true',help='Stop chain for baseline; start for blockchain')
    args=p.parse_args()
    if args.train_limit < 0 or args.test_limit < 0: p.error('limits must be >=0')
    if args.mode=='baseline' and args.audit_strategy!='sync':p.error('pipelined strategy requires blockchain mode')
    output=(args.output or ROOT/'results'/f'{args.mode}-{datetime.now().strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}').resolve()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'config.json').exists(): raise FileExistsError('Refusing to overwrite an existing run')
    started=time.perf_counter()
    try:
        with job_lock('training:'+output.name):
            execute(args,output,started)
    except BaseException as exc:
        previous=json.loads((output/'status.json').read_text(encoding='utf-8')) if (output/'status.json').exists() else {}
        previous.update({'state':'failed','error':f'{type(exc).__name__}: {exc}'})
        write_json(output/'status.json',previous)
        raise

def execute(args,output,started):
        import psutil
        write_json(output/'process.json',{'pid':os.getpid(),'created':psutil.Process().create_time()})
        if args.manage_network:
            from network import manage
            if args.mode=='baseline': manage.stop()
            else: manage.start(); manage.wait_ready()
        audit=None
        size_before=ledger_bytes()
        block_before=None
        setup_seconds=0
        if args.mode=='blockchain':
            from audit.chain import AuditChain
            setup_start=time.perf_counter()
            chain=AuditChain(args.rpc_url,evidence_dir=output)
            run_id=f'{output.name}-{uuid.uuid4().hex}'
            block_before=chain.w3.eth.block_number
            chain.start_run(run_id)
            audit=ChainHook(chain,run_id,output)
            write_json(output/'audit_context.json',{'run_id':run_id,'rpc_url':args.rpc_url,'contract_address':chain.contract.address,'chain_id':chain.w3.eth.chain_id})
            setup_seconds=time.perf_counter()-setup_start
        from fl.runner import run_training
        with ResourceSampler(output):
            summary=run_training({'seed':args.seed,'rounds':args.rounds,'train_samples':args.train_limit or None,
                                  'test_samples':args.test_limit or None,'device':args.device,
                                  'audit_strategy':args.audit_strategy,'study_id':args.study_id},output,audit)
        summary.update({'audit_setup_seconds':setup_seconds,'workflow_wall_seconds':time.perf_counter()-started,
                        'ledger_growth_bytes_all_nodes':ledger_bytes()-size_before,'ledger_scope':'sum_all_validator_files_excluding_logs_including_empty_blocks',
                        'start_block':block_before,'end_block':audit.chain.w3.eth.block_number if audit else None})
        if audit:
            from experiments.metrics import transaction_metrics
            summary.update(transaction_metrics(output))
        write_json(output/'summary.json',summary)
        print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__': main()
