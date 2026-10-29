"""Readiness must distinguish available old RPC state from a live managed chain."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from network import manage


EXPECTED = {'chain_id':1337, 'validators':{f'0x{i:040x}' for i in range(1, 5)}}
GENESIS = '0x' + 'ab' * 32
REAL_IDENTITY_SNAPSHOT = manage._identity_snapshot


def rows(height=10, *, running=True):
    return [{'node':i, 'pid':100+i, 'running':running, 'rpc':f'http://127.0.0.1:{8544+i}',
             'block_number':height} for i in range(1, 5)]


def identities():
    return [{'node':i, 'chain_id':1337, 'genesis_hash':GENESIS,
             'validators':EXPECTED['validators'].copy()} for i in range(1, 5)]


class Clock:
    def __init__(self): self.now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


@pytest.fixture
def ready_env(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(manage.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(manage.time, 'sleep', clock.sleep)
    monkeypatch.setattr(manage, '_expected_identity', lambda:deepcopy(EXPECTED))
    monkeypatch.setattr(manage, '_identity_snapshot', lambda *_a, **_k:identities())
    return clock


def test_old_rpc_height_cannot_hide_dead_or_unowned_processes(ready_env, monkeypatch):
    monkeypatch.setattr(manage, 'status', lambda **_:rows(50, running=False))
    with pytest.raises(RuntimeError, match='not owned/running'):
        manage.wait_ready(timeout=3, poll_interval=1)
    assert ready_env.now == 0


def test_stale_height_is_not_ready_even_when_rpc_responds(ready_env, monkeypatch):
    monkeypatch.setattr(manage, 'status', lambda **_:rows(50))
    with pytest.raises(TimeoutError, match='no fresh block progress'):
        manage.wait_ready(timeout=3, poll_interval=1)
    assert ready_env.now == 3


def test_requires_fresh_progress_from_every_validator(ready_env, monkeypatch):
    snapshots = [rows(10), rows(11)]
    snapshots[1][3]['block_number'] = 10
    monkeypatch.setattr(manage, 'status', lambda **_:snapshots.pop(0) if len(snapshots)>1 else snapshots[0])
    with pytest.raises(TimeoutError, match=r'nodes \[4\]'):
        manage.wait_ready(timeout=3, poll_interval=1)


def test_matching_network_and_new_blocks_is_ready(ready_env, monkeypatch):
    snapshots = [rows(10), rows(11)]
    monkeypatch.setattr(manage, 'status', lambda **_:snapshots.pop(0))
    result = manage.wait_ready(timeout=3, poll_interval=1)
    assert [r['block_number'] for r in result] == [11]*4
    assert ready_env.now == 1


@pytest.mark.parametrize('kind, message', [('chain', 'chain ID mismatch'),
                                          ('validators', 'validator set'),
                                          ('genesis', 'genesis block hash')])
def test_mismatched_identity_fails_without_waiting(ready_env, monkeypatch, kind, message):
    wrong = identities()
    if kind=='chain': wrong[0]['chain_id']=1
    elif kind=='validators': wrong[0]['validators']={'0x'+'ff'*20}
    else: wrong[0]['genesis_hash']='0x'+'cd'*32
    monkeypatch.setattr(manage, 'status', lambda **_:rows(100))
    monkeypatch.setattr(manage, '_identity_snapshot', lambda *_a, **_k:wrong)
    with pytest.raises(RuntimeError, match=message):
        manage.wait_ready(timeout=3, poll_interval=1)
    assert ready_env.now == 0


def test_rpc_startup_delay_is_retried_until_deadline(ready_env, monkeypatch):
    monkeypatch.setattr(manage, 'status', lambda **_:rows(None))
    with pytest.raises(TimeoutError, match='RPC unavailable'):
        manage.wait_ready(timeout=2.5, poll_interval=1)
    assert ready_env.now == 2.5


def test_identity_rpc_startup_delay_can_recover(ready_env, monkeypatch):
    snapshots = [rows(10), rows(10), rows(11)]
    checks = [[{'node':i, 'unavailable':'timeout'} for i in range(1,5)], identities(), identities()]
    monkeypatch.setattr(manage, 'status', lambda **_:snapshots.pop(0))
    monkeypatch.setattr(manage, '_identity_snapshot', lambda *_a, **_k:checks.pop(0))
    assert manage.wait_ready(timeout=4, poll_interval=1)[0]['block_number']==11


def test_genesis_cannot_change_between_validated_observations(ready_env, monkeypatch):
    snapshots=[identities(),identities()]
    for identity in snapshots[1]:identity['genesis_hash']='0x'+'cd'*32
    heights=[rows(10),rows(11)]
    monkeypatch.setattr(manage, 'status', lambda **_:heights.pop(0))
    monkeypatch.setattr(manage, '_identity_snapshot', lambda *_a, **_k:snapshots.pop(0))
    with pytest.raises(RuntimeError, match='changed while waiting'):
        manage.wait_ready(timeout=3, poll_interval=1)


def test_invalid_wait_configuration_is_rejected():
    with pytest.raises(ValueError, match='positive'): manage.wait_ready(timeout=0)
    with pytest.raises(ValueError, match='positive'): manage.wait_ready(poll_interval=0)


def test_expected_identity_reads_local_configuration(tmp_path, monkeypatch):
    (tmp_path/'genesis.json').write_text('{"config":{"chainId":"0x539"}}')
    import json
    (tmp_path/'validators.json').write_text(json.dumps({'validators':sorted(EXPECTED['validators'])}))
    monkeypatch.setattr(manage,'NET',tmp_path)
    assert manage._expected_identity()==EXPECTED


def rpc_result(validator_list):
    return [{'jsonrpc':'2.0','id':1,'result':'0x539'},
            {'jsonrpc':'2.0','id':2,'result':{'hash':GENESIS}},
            {'jsonrpc':'2.0','id':3,'result':validator_list}]


class Response:
    def __init__(self, payload): self.payload=payload
    def raise_for_status(self): pass
    def json(self): return self.payload


def test_transient_null_validator_result_recovers_without_skipping_progress(ready_env, monkeypatch):
    """An RPC null is retried, then a validated baseline and fresh block are still required."""
    import requests
    from threading import Lock
    calls = {}
    guard = Lock()

    def post(url, **_):
        with guard:
            count=calls.get(url,0)
            calls[url]=count+1
        return Response(rpc_result(None if count==0 else sorted(EXPECTED['validators'])))

    snapshots=[rows(10),rows(10),rows(11)]
    monkeypatch.setattr(requests,'post',post)
    monkeypatch.setattr(manage,'_identity_snapshot',REAL_IDENTITY_SNAPSHOT)
    monkeypatch.setattr(manage,'status',lambda **_:snapshots.pop(0))
    assert manage.wait_ready(timeout=4,poll_interval=1)[0]['block_number']==11
    assert ready_env.now==2
    assert set(calls.values())=={3}


def test_persistent_null_validator_result_times_out_with_rpc_evidence(ready_env, monkeypatch):
    import requests
    monkeypatch.setattr(requests,'post',lambda *_a,**_k:Response(rpc_result(None)))
    monkeypatch.setattr(manage,'_identity_snapshot',REAL_IDENTITY_SNAPSHOT)
    monkeypatch.setattr(manage,'status',lambda **_:rows(100))
    with pytest.raises(TimeoutError,match=r'qbft_getValidatorsByBlockNumber\(latest\) result=None'):
        manage.wait_ready(timeout=3,poll_interval=1)
    assert ready_env.now==3


@pytest.mark.parametrize('wrong',[{},'not a validator list',7])
def test_non_null_invalid_validator_type_still_fails_fast(ready_env, monkeypatch, wrong):
    import requests
    monkeypatch.setattr(requests,'post',lambda *_a,**_k:Response(rpc_result(wrong)))
    monkeypatch.setattr(manage,'_identity_snapshot',REAL_IDENTITY_SNAPSHOT)
    monkeypatch.setattr(manage,'status',lambda **_:rows(100))
    with pytest.raises(RuntimeError,match='invalid QBFT validator list') as error:
        manage.wait_ready(timeout=3,poll_interval=1)
    assert repr(wrong) in str(error.value)
    assert ready_env.now==0


def test_empty_validator_set_still_fails_identity_comparison(ready_env, monkeypatch):
    import requests
    monkeypatch.setattr(requests,'post',lambda *_a,**_k:Response(rpc_result([])))
    monkeypatch.setattr(manage,'_identity_snapshot',REAL_IDENTITY_SNAPSHOT)
    monkeypatch.setattr(manage,'status',lambda **_:rows(100))
    with pytest.raises(RuntimeError,match='validator set'):
        manage.wait_ready(timeout=3,poll_interval=1)
    assert ready_env.now==0
