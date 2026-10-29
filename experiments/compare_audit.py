"""Seed-paired sync/pipelined audit comparison with a shared exclusive job lease."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import subprocess
import sys
from common.job_lock import job_lock,delegated_env
from common.processes import terminate_child_tree

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--rounds',type=int,default=10)
    p.add_argument('--seeds',default='42,43,44')
    p.add_argument('--train-limit',type=int,default=0)
    p.add_argument('--test-limit',type=int,default=0)
    p.add_argument('--name',default=datetime.now().strftime('audit-optimization-%Y%m%d-%H%M%S'))
    args=p.parse_args()
    with job_lock('audit-comparison:'+args.name):
        rng=random.Random(2027);jobs=[]
        for seed in map(int,args.seeds.split(',')):
            strategies=['sync','pipelined'];rng.shuffle(strategies)
            jobs.extend({'seed':seed,'strategy':s} for s in strategies)
        manifest=ROOT/'results'/f'{args.name}-schedule.json'
        if manifest.exists():raise FileExistsError(manifest)
        manifest.write_text(json.dumps({'schedule_seed':2027,'rounds':args.rounds,'train_limit':args.train_limit,
                                       'test_limit':args.test_limit,'jobs':jobs},indent=2),encoding='utf-8')
        for job in jobs:
            output=ROOT/'results'/f'{args.name}-{job["strategy"]}-s{job["seed"]}'
            output.mkdir(exist_ok=False)
            cmd=[sys.executable,'-u','-m','experiments.run','--mode','blockchain','--audit-strategy',job['strategy'],
                 '--rounds',str(args.rounds),'--seed',str(job['seed']),'--train-limit',str(args.train_limit),
                 '--test-limit',str(args.test_limit),'--output',str(output),'--manage-network']
            cmd.extend(['--study-id',args.name])
            print('Starting '+output.name,flush=True)
            with (output/'run.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen(cmd,cwd=ROOT,env=delegated_env(),stdout=log,stderr=subprocess.STDOUT)
                try:returncode=process.wait()
                except BaseException:
                    terminate_child_tree(process)
                    raise
            if returncode:raise RuntimeError('Comparison run failed: '+output.name)
            print('Completed '+output.name,flush=True)
        print('Audit comparison complete',flush=True)

if __name__=='__main__':main()
