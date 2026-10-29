"""Run four genuine Besu processes on loopback with project-local runtime/data."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / 'network'

def runtime():
    return json.loads((NET / 'runtime.json').read_text(encoding='utf-8'))

def java_command():
    cfg = runtime()
    return [str(Path(cfg['java_home']) / 'bin/java.exe'), '-Xms128m', '-Xmx512m',
            '-Dvertx.disableFileCPResolving=true', '-Dlog4j.shutdownHookEnabled=false',
            '-Djava.util.logging.manager=org.apache.logging.log4j.jul.LogManager',
            '--add-opens=java.base/sun.security.provider=ALL-UNNAMED',
            '--add-opens=java.base/java.util=ALL-UNNAMED', '--add-opens=java.base/java.nio=ALL-UNNAMED',
            '--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED', '--sun-misc-unsafe-memory-access=allow',
            '--enable-native-access=ALL-UNNAMED', '--add-opens=java.base/sun.nio.ch=ALL-UNNAMED',
            '-cp', str(Path(cfg['besu_home']) / 'lib/*'), 'org.hyperledger.besu.Besu']

def _prepare():
    from eth_account import Account
    from eth_keys import keys
    import rlp
    if (NET / 'genesis.json').exists():
        return
    accounts = []
    for _ in range(6):
        a = Account.create()
        accounts.append({'address': a.address, 'private_key': '0x' + a.key.hex().removeprefix('0x')})
    (NET / 'accounts.json').write_text(json.dumps(accounts, indent=2), encoding='utf-8')
    validators = []
    enodes = []
    for i in range(4):
        node = NET / 'nodes' / f'node{i+1}'
        node.mkdir(parents=True, exist_ok=True)
        a = Account.create()
        raw = bytes(a.key)
        (node / 'key').write_text(raw.hex(), encoding='ascii')
        validators.append(bytes.fromhex(a.address[2:]))
        pub = keys.PrivateKey(raw).public_key.to_bytes().hex()
        enodes.append(f'enode://{pub}@127.0.0.1:{30303+i}')
    for i in range(4):
        node = NET / 'nodes' / f'node{i+1}'
        (node / 'static-nodes.json').write_text(json.dumps(enodes, indent=2), encoding='utf-8')
        (node / 'permissions_config.toml').write_text('nodes-allowlist=' + json.dumps(enodes), encoding='utf-8')
    genesis = {'config': {'chainId': 1337, 'berlinBlock': 0, 'londonBlock': 0, 'zeroBaseFee': True,
                         'qbft': {'blockperiodseconds': 1, 'epochlength': 30000, 'requesttimeoutseconds': 4}},
               'nonce': '0x0', 'timestamp': '0x0', 'gasLimit': '0x1c9c380', 'difficulty': '0x1',
               'mixHash': '0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365',
               'coinbase': '0x' + '00'*20,
               'extraData': '0x' + rlp.encode([bytes(32), validators, [], 0, []]).hex(),
               'alloc': {a['address'][2:]: {'balance': '0x3635c9adc5dea00000'} for a in accounts}}
    (NET / 'genesis.json').write_text(json.dumps(genesis, indent=2), encoding='utf-8')
    (NET / 'validators.json').write_text(json.dumps({'validators': ['0x'+v.hex() for v in validators], 'enodes': enodes}, indent=2), encoding='utf-8')

def _pids():
    path = NET / 'pids.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def _write_pids(pids):
    # Readers must never see a truncated PID document during start/stop.
    temp = NET / f'pids.{os.getpid()}.tmp'
    temp.write_text(json.dumps(pids, indent=2), encoding='utf-8')
    temp.replace(NET / 'pids.json')

def _owned(pid, index):
    import psutil
    try:
        p = psutil.Process(int(pid))
        command = ' '.join(p.cmdline()).lower().replace('\\', '/')
        return p if str((NET / 'nodes' / f'node{index+1}').resolve()).lower().replace('\\','/') in command and 'org.hyperledger.besu.besu' in command else None
    except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
        return None

def prepare():
    from common.job_lock import job_lock
    with job_lock('network-prepare'):
        return _prepare()

def _start(indices=None):
    prepare()
    pids = _pids()
    for i in (range(4) if indices is None else indices):
        if str(i) in pids and _owned(pids[str(i)], i):
            continue
        node = NET / 'nodes' / f'node{i+1}'
        cmd = java_command() + [f'--data-path={node}', f'--genesis-file={NET / "genesis.json"}',
            f'--node-private-key-file={node / "key"}', '--network-id=1337', '--discovery-enabled=false',
            '--p2p-host=127.0.0.1', '--p2p-interface=127.0.0.1', f'--p2p-port={30303+i}',
            '--rpc-http-enabled=true', '--rpc-http-host=127.0.0.1', f'--rpc-http-port={8545+i}',
            '--rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN', '--host-allowlist=localhost,127.0.0.1',
            '--min-gas-price=0', '--sync-mode=FULL', '--logging=INFO',
            '--permissions-nodes-config-file-enabled=true', f'--permissions-nodes-config-file={node / "permissions_config.toml"}']
        with (node / 'besu.log').open('ab') as log:
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        pids[str(i)] = p.pid
    _write_pids(pids)
    return status()

def start(indices=None, *, wait=False):
    from common.job_lock import job_lock
    with job_lock('network-start'):
        result = _start(indices)
        return wait_ready() if wait else result

def _stop(indices=None):
    pids = _pids()
    for i in (range(4) if indices is None else indices):
        if str(i) in pids:
            p = _owned(pids[str(i)], i)
            if p:
                p.terminate()
                try: p.wait(timeout=15)
                except Exception: p.kill()
            pids.pop(str(i), None)
    _write_pids(pids)
    return status()

def stop(indices=None):
    from common.job_lock import job_lock
    with job_lock('network-stop'):
        return _stop(indices)

def status(request_timeout=2.0):
    import requests
    pids = _pids()

    def probe(i):
        row = {'node': i+1, 'pid': pids.get(str(i)), 'running': bool(_owned(pids.get(str(i), 0), i)), 'rpc': f'http://127.0.0.1:{8545+i}'}
        try:
            r = requests.post(row['rpc'], json={'jsonrpc':'2.0','id':1,'method':'eth_blockNumber','params':[]}, timeout=request_timeout)
            r.raise_for_status()
            row['block_number'] = int(r.json()['result'], 16)
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            row['block_number'] = None
            row['rpc_error'] = str(exc)
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(probe, range(4)))

def _expected_identity():
    genesis = json.loads((NET / 'genesis.json').read_text(encoding='utf-8'))
    configured = json.loads((NET / 'validators.json').read_text(encoding='utf-8'))['validators']
    validators = {str(address).lower() for address in configured}
    if len(validators) != 4:
        raise RuntimeError('Network configuration must specify four distinct QBFT validators')
    value = genesis['config']['chainId']
    return {'chain_id': int(value, 0) if isinstance(value, str) else int(value), 'validators': validators}

def _identity_snapshot(rows, request_timeout):
    """Read fixed network identity using JSON-RPC batches; never sends a transaction."""
    import requests

    def probe(row):
        requests_ = [
            {'jsonrpc':'2.0', 'id':1, 'method':'eth_chainId', 'params':[]},
            {'jsonrpc':'2.0', 'id':2, 'method':'eth_getBlockByNumber', 'params':['0x0', False]},
            {'jsonrpc':'2.0', 'id':3, 'method':'qbft_getValidatorsByBlockNumber', 'params':['latest']},
        ]
        try:
            response = requests.post(row['rpc'], json=requests_, timeout=request_timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError(f"Node {row['node']} did not return a JSON-RPC identity batch")
            by_id = {item['id']: item for item in payload}
            for request in requests_:
                answer = by_id[request['id']]
                if 'error' in answer:
                    raise RuntimeError(f"Node {row['node']} {request['method']} failed: {answer['error']}")
            genesis = by_id[2]['result']
            if genesis is None:
                return {'node':row['node'], 'unavailable':'eth_getBlockByNumber(0x0) result=None (temporarily unavailable)'}
            if not isinstance(genesis, dict) or not genesis.get('hash'):
                raise RuntimeError(f"Node {row['node']} did not return a genesis block hash: {repr(genesis)[:160]}")
            validator_list = by_id[3]['result']
            if validator_list is None:
                return {'node':row['node'], 'unavailable':'qbft_getValidatorsByBlockNumber(latest) result=None (temporarily unavailable)'}
            if not isinstance(validator_list, list):
                raise RuntimeError(f"Node {row['node']} returned an invalid QBFT validator list: {repr(validator_list)[:160]}")
            return {'node':row['node'], 'chain_id':int(by_id[1]['result'], 16),
                    'genesis_hash':str(genesis['hash']).lower(),
                    'validators':{str(address).lower() for address in validator_list}}
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return {'node':row['node'], 'unavailable':str(exc)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(probe, rows))

def _validate_identity(identities, expected, initial_genesis=None):
    for identity in identities:
        node = identity['node']
        if identity['chain_id'] != expected['chain_id']:
            raise RuntimeError(f"Node {node} chain ID mismatch: expected {expected['chain_id']}, got {identity['chain_id']}")
        if identity['validators'] != expected['validators']:
            raise RuntimeError(f'Node {node} QBFT validator set does not match network/validators.json')
    hashes = {identity['genesis_hash'] for identity in identities}
    if len(hashes) != 1:
        raise RuntimeError('Validator RPC nodes disagree on the genesis block hash')
    genesis_hash = next(iter(hashes))
    if initial_genesis is not None and genesis_hash != initial_genesis:
        raise RuntimeError('Genesis block hash changed while waiting for readiness')
    return genesis_hash

def wait_ready(timeout=90, poll_interval=1.0):
    """Require owned processes, matching chain identity and fresh progress on all nodes.

    RPC availability and a stored height alone do not establish QBFT liveness.
    Each call starts a new baseline and requires every validator RPC to advance
    beyond its first fully validated observation.
    """
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError('timeout and poll_interval must be positive')
    expected = _expected_identity()
    end = time.monotonic() + timeout
    baseline = None
    genesis_hash = None
    last_reason = 'waiting for validator RPCs'
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        rows = status(request_timeout=max(0.001, min(2.0, remaining)))
        if len(rows) != 4 or {r['node'] for r in rows} != {1, 2, 3, 4}:
            raise RuntimeError('Readiness requires exactly four managed validator processes')
        missing = [r['node'] for r in rows if not r['running']]
        if missing:
            raise RuntimeError(f'Validator processes not owned/running: {missing}; inspect network/nodes/node*/besu.log')
        unavailable = [r['node'] for r in rows if r['block_number'] is None]
        if unavailable:
            last_reason = f'RPC unavailable on validator nodes {unavailable}'
        elif time.monotonic() < end:
            identities = _identity_snapshot(rows, request_timeout=max(0.001, min(2.0, end-time.monotonic())))
            unavailable = [identity['node'] for identity in identities if 'unavailable' in identity]
            if unavailable:
                details = '; '.join(f"node {identity['node']}: {identity['unavailable']}" for identity in identities if 'unavailable' in identity)
                last_reason = f'Chain identity RPC unavailable on validator nodes {unavailable}: {details}'
            else:
                genesis_hash = _validate_identity(identities, expected, genesis_hash)
                heights = {row['node']: row['block_number'] for row in rows}
                if baseline is None:
                    baseline = heights
                waiting = [node for node, height in heights.items() if height <= baseline[node]]
                if not waiting and time.monotonic() < end:
                    return rows
                last_reason = f'no fresh block progress on validator nodes {waiting}'
        remaining = end-time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    raise TimeoutError(f'QBFT readiness timed out after {timeout:g}s: {last_reason}')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['start', 'stop', 'status', 'prepare'])
    p.add_argument('--nodes', help='1-based comma-separated node numbers')
    args = p.parse_args()
    ids = [int(x)-1 for x in args.nodes.split(',')] if args.nodes else None
    if ids is not None and any(i not in range(4) for i in ids): p.error('nodes must be 1..4')
    result = prepare() if args.command == 'prepare' else start(ids, wait=ids is None) if args.command == 'start' else stop(ids) if args.command == 'stop' else status()
    print(json.dumps(result, indent=2))
