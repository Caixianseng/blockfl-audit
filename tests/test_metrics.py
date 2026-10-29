import json
import pytest
from experiments.metrics import transaction_metrics

def test_actual_application_rate_includes_preparation_gap_and_excludes_reverts(tmp_path):
    # Two successes across an 11-second observation window, including a long
    # preparation gap; one rejection is evidence but not a confirmed success.
    records=[
        {'submitted_at':'2026-10-29T00:00:00+00:00','confirmation_seconds':1,'status':1},
        {'submitted_at':'2026-10-29T00:00:09+00:00','confirmation_seconds':1,'status':0},
        {'submitted_at':'2026-10-29T00:00:10+00:00','confirmation_seconds':1,'status':1},
    ]
    (tmp_path/'transactions.jsonl').write_text('\n'.join(map(json.dumps,records)))
    result=transaction_metrics(tmp_path)
    assert result['application_audit_window_seconds']==11
    assert result['application_audit_tps']==pytest.approx(2/11)
    assert result['reverted_audit_transactions']==1
