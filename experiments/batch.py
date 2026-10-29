"""Run the preregistered seed-paired A/B schedule sequentially."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import subprocess
import sys
from common.job_lock import delegated_env, job_lock
from common.processes import terminate_child_tree

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--rounds',type=int,default=10)
    p.add_argument('--seeds',default='42,43,44')
    p.add_argument('--train-limit',type=int,default=0)
    p.add_argument('--test-limit',type=int,default=0)
    p.add_argument('--audit-strategy',choices=['sync','pipelined'],default='sync')
    p.add_argument('--name',default=datetime.now().strftime('study-%Y%m%d-%H%M%S'))
    args=p.parse_args()
    with job_lock(f'batch:{args.name}'):
        run_batch(args)

def run_batch(args):
    rng=random.Random(2026)
    schedule=[]
    for seed in map(int,args.seeds.split(',')):
        modes=['baseline','blockchain'];rng.shuffle(modes)
        schedule.extend({'seed':seed,'mode':mode} for mode in modes)
    manifest=ROOT/'results'/f'{args.name}-schedule.json'
    if manifest.exists(): raise FileExistsError(manifest)
    manifest.write_text(json.dumps({'schedule_seed':2026,'rounds':args.rounds,'train_limit':args.train_limit,
                                  'test_limit':args.test_limit,'audit_strategy':args.audit_strategy,'schedule':schedule},indent=2),encoding='utf-8')
    for job in schedule:
        output=ROOT/'results'/f'{args.name}-{job["mode"]}-s{job["seed"]}'
        output.mkdir(exist_ok=False)
        cmd=[sys.executable,'-u','-m','experiments.run','--mode',job['mode'],'--seed',str(job['seed']),
             '--rounds',str(args.rounds),'--train-limit',str(args.train_limit),'--test-limit',str(args.test_limit),
             '--output',str(output),'--manage-network','--audit-strategy',args.audit_strategy if job['mode']=='blockchain' else 'sync','--study-id',args.name]
        print(f'Starting {output.name}',flush=True)
        with (output/'run.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=delegated_env())
            try:
                returncode=process.wait()
            except BaseException:
                terminate_child_tree(process)
                raise
        if returncode: raise RuntimeError(f'{output.name} failed; see run.log')
        print(f'Completed {output.name}',flush=True)
    print('All paired runs complete',flush=True)

if __name__=='__main__':main()
