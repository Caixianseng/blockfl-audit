"""Measure local four-validator QBFT availability under one/two node stops.

This is a stop-failure experiment, not a Byzantine attack or partition test.
Run while training and contract writes are idle. The RPC endpoint must belong
to node zero, which this experiment never stops.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.middleware import ExtraDataToPOAMiddleware

from audit.chain import _jsonable, _read_accounts
from common.job_lock import job_lock


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@job_lock("validator-fault-test")
def run_fault_test(
    rpc_url: str = "http://127.0.0.1:8545",
    accounts_path: str | Path = ROOT / "network/accounts.json",
    output_dir: str | Path | None = None,
    observation_seconds: float = 15.0,
    receipt_timeout: float = 60.0,
) -> dict[str, Any]:
    from network import manage

    if observation_seconds < 5:
        raise ValueError("Use an observation window of at least five seconds")
    out = Path(output_dir) if output_dir else ROOT / "results" / f"fault-{uuid.uuid4().hex[:10]}"
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "experiment": "four-validator QBFT stop failures", "started_at": utc_now(),
        "rpc_url": rpc_url, "observation_seconds": observation_seconds,
        "stopped_nodes": [], "phases": [], "passed": False,
        "limitation": "Local stop failures only; not a Byzantine attack or network-partition test.",
    }
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    accounts = _read_accounts(Path(accounts_path))
    signer = accounts[0]
    stopped: list[int] = []

    def save() -> None:
        (out / "fault_results.json").write_text(json.dumps(_jsonable(result), indent=2) + "\n", encoding="utf-8")

    def record_phase(name: str, **data: Any) -> None:
        result["phases"].append({"phase": name, "at": utc_now(), **_jsonable(data)})
        save()
        print(name, flush=True)

    def submit_transfer(label: str) -> tuple[Any, float]:
        tx = {
            "from": signer["address"], "to": accounts[1]["address"], "value": 1,
            "nonce": w3.eth.get_transaction_count(signer["address"], "pending"),
            "chainId": w3.eth.chain_id, "gas": 21000, "gasPrice": 0,
        }
        signed = w3.eth.account.sign_transaction(tx, signer["private_key"])
        start = time.perf_counter()
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        record_phase(label + "_submitted", tx_hash=Web3.to_hex(tx_hash), nonce=tx["nonce"], block_number=w3.eth.block_number)
        return tx_hash, start

    def wait_success(label: str, tx_hash: Any, start: float, timeout: float) -> None:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=0.25)
        if receipt.status != 1:
            raise AssertionError(f"{label}: transfer reverted")
        record_phase(label, passed=True, receipt=receipt, confirmation_seconds=time.perf_counter() - start)

    try:
        if not w3.is_connected():
            raise ConnectionError("The surviving node-zero RPC is not reachable")
        initial = manage.status()
        if len(initial) != 4 or not all(node.get("running") and node.get("block_number") is not None for node in initial):
            raise AssertionError("Start with all four managed validator processes running and RPC-ready")
        response = w3.provider.make_request("qbft_getValidatorsByBlockNumber", ["latest"])
        if "error" in response or len(response.get("result", [])) != 4:
            raise AssertionError("RPC must report exactly four configured QBFT validators")
        result["chain_id"] = w3.eth.chain_id
        result["validators"] = response["result"]
        record_phase("baseline_state", network_status=initial, block_number=w3.eth.block_number)
        tx_hash, start = submit_transfer("baseline")
        wait_success("baseline_confirmation", tx_hash, start, receipt_timeout)

        # Node indices are zero-based in the network manager; node zero survives.
        manage.stop(indices=[3])
        stopped.append(3)
        result["stopped_nodes"] = stopped.copy()
        record_phase("one_validator_stopped", network_status=manage.status())
        tx_hash, start = submit_transfer("one_validator_stopped")
        wait_success("one_validator_stop_confirmation", tx_hash, start, receipt_timeout)

        manage.stop(indices=[2])
        stopped.append(2)
        result["stopped_nodes"] = stopped.copy()
        record_phase("two_validators_stopped", network_status=manage.status())
        tx_hash, start = submit_transfer("two_validators_stopped")
        observations = []
        deadline = time.monotonic() + observation_seconds
        while time.monotonic() < deadline:
            try:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                receipt = None
            if receipt is not None:
                raise AssertionError("A newly submitted transfer finalized with two validators stopped")
            observations.append({"elapsed_seconds": time.perf_counter() - start, "block_number": w3.eth.block_number})
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        record_phase("two_validator_stop_no_confirmation", passed=True, tx_hash=Web3.to_hex(tx_hash), observation_seconds=observation_seconds, observations=observations)

        manage.start(indices=[2, 3])
        stopped.clear()
        result["stopped_nodes"] = []
        record_phase("validators_restarted", network_status=manage.status())
        wait_success("pending_transfer_confirmed_after_recovery", tx_hash, start, max(receipt_timeout, 120.0))
        tx_hash, start = submit_transfer("recovered")
        wait_success("recovered_new_transfer_confirmation", tx_hash, start, receipt_timeout)
        result["passed"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if stopped:
            try:
                manage.start(indices=sorted(stopped))
                result["restoration_requested"] = sorted(stopped)
                result["stopped_nodes"] = []
            except Exception as exc:
                result["restoration_error"] = f"{type(exc).__name__}: {exc}"
                result["passed"] = False
        result["finished_at"] = utc_now()
        save()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--accounts", default=str(ROOT / "network/accounts.json"))
    parser.add_argument("--output-dir")
    parser.add_argument("--observation-seconds", type=float, default=15.0)
    parser.add_argument("--receipt-timeout", type=float, default=60.0)
    args = parser.parse_args()
    data = run_fault_test(args.rpc_url, args.accounts, args.output_dir, args.observation_seconds, args.receipt_timeout)
    print(json.dumps({"passed": data["passed"], "phases": len(data["phases"]), "finished_at": data["finished_at"]}, indent=2))


if __name__ == "__main__":
    main()
