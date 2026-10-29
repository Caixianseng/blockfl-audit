"""Derive application transaction rates from the actual submission/receipt window."""
from datetime import datetime
import json
from pathlib import Path

def transaction_metrics(output):
    path=Path(output)/'transactions.jsonl'
    txs=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    accepted=[t for t in txs if t['status']==1]
    starts=[datetime.fromisoformat(t['submitted_at']).timestamp() for t in txs]
    ends=[datetime.fromisoformat(t['submitted_at']).timestamp()+t['confirmation_seconds'] for t in txs]
    window=max(ends)-min(starts)
    return {'confirmed_audit_transactions':len(accepted),'reverted_audit_transactions':len(txs)-len(accepted),
            'application_audit_window_seconds':window,'application_audit_tps':len(accepted)/window,
            'application_audit_tps_scope':'first submission to last receipt; includes run creation and preparation gaps; not saturated TPS',
            'mean_confirmation_seconds':sum(t['confirmation_seconds'] for t in txs)/len(txs)}

def refresh(output):
    path=Path(output)/'summary.json'
    summary=json.loads(path.read_text(encoding='utf-8'))
    if summary['mode']=='blockchain':
        summary.update(transaction_metrics(output))
        path.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('runs',nargs='+',type=Path);args=p.parse_args()
    for run in args.runs:
        if (run/'summary.json').exists():refresh(run);print(run)
