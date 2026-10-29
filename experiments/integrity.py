"""Demonstrate artifact tampering against a commitment read from Besu."""
import argparse
import json
from pathlib import Path
from audit.chain import AuditChain
from fl.artifacts import load_parameters,save_parameters,verify_artifact

def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);args=p.parse_args()
    context=json.loads((args.run/'audit_context.json').read_text())
    chain=AuditChain(context['rpc_url'])
    manifest=[json.loads(x) for x in (args.run/'manifest.jsonl').read_text().splitlines()]
    selected=next(x for x in manifest if x['kind']=='client_update')
    original=args.run/selected['path']
    committed=chain.get_update_hash(context['run_id'],selected['round'],selected['client_id']+1).removeprefix('0x')
    good=verify_artifact(original,committed)
    params=load_parameters(original)
    name=sorted(params)[0];params[name]=params[name].copy();params[name].flat[0]+=1
    tampered=args.run/'tampered_demo.npz'
    actual=save_parameters(tampered,params)
    bad=verify_artifact(tampered,committed)
    result={'original_path':selected['path'],'chain_hash':committed,'original_matches':good,
            'tampered_hash':actual,'tampered_matches':bad,'passed':good and not bad,'source':'live eth_call'}
    (args.run/'integrity_test.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    if not result['passed']:raise AssertionError('Integrity demonstration failed')

if __name__=='__main__':main()
