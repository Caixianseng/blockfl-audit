"""Bounded readers and process control for the local dashboard.

Readers tolerate a partially written final record, but retain parse errors for
display. Results are never synthesized when a run has not produced them.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from common.job_lock import JobBusyError, job_lock, job_status

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def within_results(path: str | Path) -> Path:
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(RESULTS.resolve()):
        raise ValueError("Only files inside the project results directory are allowed")
    return candidate


def read_json(path: Path) -> tuple[dict, str | None]:
    try:
        if not path.exists():
            return {}, None
        if path.stat().st_size > 4 * 1024 * 1024:
            return {}, f"{path.name}: file exceeds 4 MiB"
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return {}, f"{path.name}: expected a JSON object"
        return value, None
    except (OSError, ValueError) as exc:
        return {}, f"{path.name}: {exc}"


def read_jsonl(path: Path, limit: int = 20000) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    records, errors = [], []
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if number > limit:
                    errors.append(f"{path.name}: showing the first {limit} records")
                    break
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("expected an object")
                    records.append(record)
                except ValueError:
                    errors.append(f"{path.name}: record {number} is incomplete or invalid")
    except OSError as exc:
        errors.append(f"{path.name}: {exc}")
    return records, errors


def read_rounds(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = []
            for row in csv.DictReader(handle):
                if len(rows) >= 10000:
                    return rows, ["rounds.csv: showing the first 10000 rounds"]
                converted = {}
                for key, value in row.items():
                    if key is None:
                        continue
                    try:
                        converted[key] = float(value)
                    except (ValueError, TypeError):
                        converted[key] = value
                rows.append(converted)
            return rows, []
    except (OSError, csv.Error) as exc:
        return [], [f"rounds.csv: {exc}"]


def load_run(path: Path) -> dict:
    path = within_results(path)
    run = {"path": path, "name": path.name, "errors": []}
    for key in ("config", "summary", "status", "launch", "process", "audit_context"):
        run[key], error = read_json(path / f"{key}.json")
        if error:
            run["errors"].append(error)
    run["rounds"], errors = read_rounds(path / "rounds.csv")
    run["errors"].extend(errors)
    run["manifest"], errors = read_jsonl(path / "manifest.jsonl")
    run["errors"].extend(errors)
    run["transactions"] = []
    for name in ("transactions.jsonl", "receipts.jsonl"):
        rows, errors = read_jsonl(path / name)
        run["transactions"].extend(rows)
        run["errors"].extend(errors)
    # Also accept audit modules which keep evidence in a subdirectory.
    if not run["transactions"]:
        rows, errors = read_jsonl(path / "audit" / "transactions.jsonl")
        run["transactions"].extend(rows)
        run["errors"].extend(errors)
    run["events"], errors = read_jsonl(path / "events.jsonl")
    run["errors"].extend(errors)
    run["verifications"], errors = read_jsonl(path / "verifications.jsonl")
    run["errors"].extend(errors)
    run["mode"] = run["summary"].get("mode", run["config"].get("mode", run["status"].get("mode", "unknown")))
    return run


def list_runs() -> list[dict]:
    if not RESULTS.exists():
        return []
    paths = []
    for path in RESULTS.iterdir():
        if path.is_dir() and not path.is_symlink() and any((path / f).exists() for f in ("config.json", "status.json", "summary.json", "launch.json", "process.json")):
            paths.append(path)
    return [load_run(path) for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)]


def is_live(run: dict) -> bool:
    launch = run.get("launch", {})
    process = run.get("process", {})
    # Windows venv Popen PID may belong to its redirector; process.json is
    # written by the actual training interpreter and is authoritative.
    pid = process.get("pid", launch.get("pid", run.get("status", {}).get("pid")))
    if not pid:
        return False
    try:
        import psutil
    except ImportError:
        return False
    try:
        proc = psutil.Process(int(pid))
        expected_time = process.get("created", launch.get("create_time"))
        if expected_time is not None and abs(proc.create_time() - float(expected_time)) > 1:
            return False
        argv = proc.cmdline()
        return proc.is_running() and "experiments.run" in argv
    except (OSError, ValueError, psutil.Error):
        return False


def run_scope(run: dict) -> str:
    config, summary = run.get("config", {}), run.get("summary", {})
    for field, full in (("train_limit", 50000), ("test_limit", 10000)):
        limit = config.get(field)
        if limit is not None and 0 < int(limit) < full:
            return "subset"
    for field, full in (("train_samples", 50000), ("test_samples", 10000)):
        count = summary.get(field, config.get(f"actual_{field}", config.get(field)))
        if count is not None and int(count) < full:
            return "subset"
    if config or summary:
        return "full"
    return "unknown"


def comparison_key(run: dict) -> str:
    """Group only runs with matching declared training conditions."""
    cfg, summary = run["config"], run["summary"]
    fields = ("rounds", "local_epochs", "epochs", "batch_size", "learning_rate", "lr", "momentum", "cpu_threads", "train_limit", "test_limit", "train_samples", "test_samples", "model", "partition", "partition_seed", "device", "num_clients", "clients")
    key = {name: cfg[name] for name in fields if name in cfg}
    for field in ("train_samples", "test_samples", "client_count", "deployment", "resolved_device"):
        if field in summary:
            key[field] = summary[field]
    key["scope"] = run_scope(run)
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def valid_pair(first: dict, second: dict) -> tuple[bool, str]:
    """Require recorded matching initialization and data before calling runs paired."""
    for key in ("initial_hash", "data_indices_sha256"):
        values = [run["summary"].get(key, run["config"].get(key)) for run in (first, second)]
        if any(value is None for value in values):
            return False, f"missing {key} evidence"
        if values[0] != values[1]:
            return False, f"different {key}"
    return True, "matching recorded initialization and data indices"


def tail_log(run_dir: Path, max_bytes: int = 20000) -> str:
    for name in ("run.log", "training.log"):
        path = within_results(run_dir / name)
        if path.exists():
            try:
                with path.open("rb") as handle:
                    handle.seek(max(0, path.stat().st_size - max_bytes))
                    return handle.read(max_bytes).decode("utf-8", errors="replace")
            except OSError:
                pass
    return "日志尚未生成。"


def launch_training(mode: str, rounds: int, seed: int, train_limit: int, test_limit: int, audit_strategy: str = "sync") -> Path:
    if mode not in ("baseline", "blockchain"):
        raise ValueError("Unsupported mode")
    if audit_strategy not in ("sync", "pipelined"):
        raise ValueError("Unsupported audit strategy")
    if mode=="baseline":
        audit_strategy="sync"
    if not 1 <= rounds <= 100 or seed < 0 or not 0 <= train_limit <= 50000 or not 0 <= test_limit <= 10000:
        raise ValueError("Invalid experiment configuration")
    if not (ROOT / "experiments" / "run.py").is_file():
        raise RuntimeError("训练入口尚未就绪。")
    import psutil
    RESULTS.mkdir(parents=True, exist_ok=True)
    # This short OS mutex serializes UI launch requests. The child acquires the
    # authoritative global job lock itself, so no short-lived lease is passed.
    with job_lock("dashboard-launch", lock_dir=RESULTS / ".dashboard-launch"):
        busy=job_status()
        if busy:
            raise JobBusyError(f"已有操作运行中：{busy.get('operation', '任务启动中')}（PID {busy.get('pid', '未知')}）")
        if any(is_live(run) for run in list_runs()):
            raise RuntimeError("已有训练任务运行中，请等待完成后再启动。")
        suffix = "full" if train_limit == test_limit == 0 else "subset"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        name = f"{stamp}-{mode}-s{seed}-{suffix}"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid run name")
        output = within_results(RESULTS / name)
        output.mkdir()
        argv = [sys.executable, "-u", "-m", "experiments.run", "--mode", mode, "--rounds", str(rounds), "--seed", str(seed), "--train-limit", str(train_limit), "--test-limit", str(test_limit), "--output", str(output), "--manage-network", "--audit-strategy", audit_strategy]
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with (output / "run.log").open("ab") as log:
            proc = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags)
        manifest = {"pid": proc.pid, "create_time": psutil.Process(proc.pid).create_time(), "started_at": datetime.now(timezone.utc).isoformat(), "argv": argv, "source": "dashboard"}
        (output / "launch.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return output


def network_snapshot() -> list[dict]:
    """Query each fixed local RPC concurrently, without starting or stopping nodes."""
    from concurrent.futures import ThreadPoolExecutor
    import requests

    def probe(index):
        url = f"http://127.0.0.1:{8545 + index}"
        row = {"validator": index + 1, "rpc": url, "responsive": False, "block_number": None}
        try:
            response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, timeout=1.5)
            response.raise_for_status()
            row["block_number"] = int(response.json()["result"], 16)
            row["responsive"] = True
        except (requests.RequestException, ValueError, KeyError):
            pass
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(probe, range(4)))


def query_commitment(run: dict, record: dict) -> dict:
    """Read the selected commitment at a specific block; never reads signing keys."""
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    context = run.get("audit_context", {})
    if not context:
        raise ValueError("本次运行没有 audit_context.json，无法定位链上承诺")
    round_no = int(record["round"])
    if round_no < 1:
        raise ValueError("初始模型未作为训练轮次的聚合结果上链")
    w3 = Web3(Web3.HTTPProvider(context["rpc_url"], request_kwargs={"timeout": 4}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if w3.eth.chain_id != int(context["chain_id"]):
        raise ValueError("当前网络 chain ID 与运行记录不同")
    address = Web3.to_checksum_address(context["contract_address"])
    if not w3.eth.get_code(address):
        raise ValueError("当前网络找不到原审计合约，可能已重置")
    abi = [
        {"type": "function", "name": "getRun", "stateMutability": "view", "inputs": [{"name": "runId", "type": "bytes32"}], "outputs": [{"name": "aggregator", "type": "address"}, {"name": "currentRound", "type": "uint256"}, {"name": "roundOpen", "type": "bool"}, {"name": "participants", "type": "address[]"}]},
        {"type": "function", "name": "updateHashes", "stateMutability": "view", "inputs": [{"name": "runId", "type": "bytes32"}, {"name": "round", "type": "uint256"}, {"name": "participant", "type": "address"}], "outputs": [{"name": "digest", "type": "bytes32"}]},
        {"type": "function", "name": "aggregateHashes", "stateMutability": "view", "inputs": [{"name": "runId", "type": "bytes32"}, {"name": "round", "type": "uint256"}], "outputs": [{"name": "digest", "type": "bytes32"}]},
    ]
    contract = w3.eth.contract(address=address, abi=abi)
    block = w3.eth.block_number
    run_key = bytes(Web3.keccak(text=context["run_id"]))
    run_info = contract.functions.getRun(run_key).call(block_identifier=block)
    if int(run_info[0], 16) == 0:
        raise ValueError("当前合约没有这次运行，可能已重置网络")
    if record.get("kind") == "client_update":
        client = int(record["client_id"])
        participants = run_info[3]
        if not 0 <= client < len(participants):
            raise ValueError("客户端不在本次运行的参与者名单中")
        value = contract.functions.updateHashes(run_key, round_no, participants[client]).call(block_identifier=block)
    else:
        value = contract.functions.aggregateHashes(run_key, round_no).call(block_identifier=block)
    if not any(value):
        raise ValueError("所选轮次的承诺尚未写入当前合约")
    return {"hash": Web3.to_hex(value).removeprefix("0x"), "block_number": block, "contract_address": address, "chain_id": context["chain_id"], "rpc_url": context["rpc_url"], "source": "live eth_call"}
