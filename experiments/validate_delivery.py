"""Check delivered study artifacts against successful decoded contract events."""
import argparse
import json
from pathlib import Path
from fl.artifacts import verify_artifact

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--prefix',default='full-study-20261029');args=p.parse_args()
    pairs=[];artifacts=0;confirmed=0
    for seed in (42,43,44):
        runs={m:ROOT/'results'/f'{args.prefix}-{m}-s{seed}' for m in ('baseline','blockchain')}
        summaries={m:json.loads((d/'summary.json').read_text()) for m,d in runs.items()}
        a,b=summaries['baseline'],summaries['blockchain']
        assert a['train_samples']==b['train_samples']==50000 and a['test_samples']==b['test_samples']==10000
        assert a['rounds']==b['rounds']==10
        for field in ('initial_hash','final_hash','data_indices_sha256'):assert a[field]==b[field],(seed,field)
        chainrun=runs['blockchain']
        txs=[json.loads(x) for x in (chainrun/'transactions.jsonl').read_text().splitlines()]
        assert len(txs)==61 and all(t['status']==1 for t in txs)
        confirmed+=len(txs)
        records={}
        for tx in txs:
            for event in tx['events']:
                e=event['args']
                if event['event']=='UpdateCommitted':
                    records[('client_update',e['round'],tx['client_id'])]=e['updateHash'].removeprefix('0x')
                elif event['event']=='AggregateCommitted':
                    records[('global_model',e['round'],None)]=e['aggregateHash'].removeprefix('0x')
        for line in (chainrun/'manifest.jsonl').read_text().splitlines():
            m=json.loads(line)
            if m['round']==0:continue
            expected=records[(m['kind'],m['round'],m.get('client_id'))]
            assert expected==m['hash'] and verify_artifact(chainrun/m['path'],expected)
            artifacts+=1
        readbacks=[json.loads(x) for x in (chainrun/'verifications.jsonl').read_text().splitlines()]
        assert len(readbacks)==40 and all(x['matched'] for x in readbacks)
        pairs.append({'seed':seed,'initial_data_final_hash_equal':True,'accuracy':a['final_accuracy'],
                      'baseline_seconds':a['total_seconds'],'blockchain_seconds':b['total_seconds']})
    result={'passed':True,'complete_training_runs':6,'pairs':pairs,'confirmed_audit_transactions':confirmed,
            'model_artifacts_verified_against_receipt_events':artifacts,'note':'Receipts/events captured from genuine Besu; independent eth_call readbacks saved during training.'}
    (ROOT/'results/delivery_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
