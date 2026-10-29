"""Signed Besu transactions and evidence for the FLAudit commitment contract.

Account files are local demo secrets. They are never copied into evidence records.
Run labels are converted to keccak256 UTF-8; model commitments are supplied as
32-byte hex digests by the deterministic model serialization module.
"""
from __future__ import annotations

import argparse
from functools import wraps
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.logs import DISCARD

from common.job_lock import job_lock

ROOT = Path(__file__).resolve().parents[1]


def _write_job(operation):
    """Protect public chain mutations, reusing an enclosing training job lock."""
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            with job_lock(operation):
                return function(*args, **kwargs)
        return guarded
    return decorate


class AuditTransactionError(RuntimeError):
    """A mined transaction reverted; its status-zero receipt is retained."""

    def __init__(self, record: dict[str, Any]):
        self.record = record
        super().__init__(f"{record['operation']} reverted: {record['transaction_hash']}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return Web3.to_hex(value)
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _read_accounts(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        raw = raw.get("accounts", [])
    if len(raw) < 5:
        raise ValueError("At least five funded accounts are required: aggregator then four clients")
    result = []
    for entry in raw:
        key = entry.get("private_key", entry.get("privateKey"))
        if not key:
            raise ValueError("Each account needs a private_key")
        account = Web3().eth.account.from_key(key)
        expected = entry.get("address", account.address)
        if expected.lower() != account.address.lower():
            raise ValueError("Account address does not match its signing key")
        result.append({"address": account.address, "private_key": key})
    return result


class AuditChain:
    _account_locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()
    _evidence_lock = threading.Lock()

    def __init__(
        self,
        rpc_url: str = "http://127.0.0.1:8545",
        deployment_path: str | Path = ROOT / "network/deployment.json",
        accounts_path: str | Path = ROOT / "network/accounts.json",
        evidence_dir: str | Path | None = None,
        *,
        receipt_timeout: float = 180.0,
    ):
        self.rpc_url = rpc_url
        self.deployment_path = Path(deployment_path)
        self.accounts_path = Path(accounts_path)
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        self.receipt_timeout = receipt_timeout
        self._submitted_tickets: dict[str, dict[str, Any]] = {}
        self._confirmed_tickets: dict[str, dict[str, Any]] = {}
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if not self.w3.is_connected():
            raise ConnectionError(f"Besu RPC is unavailable: {rpc_url}")
        self.accounts = _read_accounts(self.accounts_path)
        self.deployment = json.loads(self.deployment_path.read_text(encoding="utf-8-sig"))
        if int(self.deployment["chain_id"]) != self.w3.eth.chain_id:
            raise ValueError("Deployment chain ID does not match RPC chain ID")
        address = Web3.to_checksum_address(self.deployment["address"])
        code = bytes(self.w3.eth.get_code(address))
        if not code:
            raise ValueError("No contract code exists at deployment address; redeploy after a chain reset")
        expected_code = self.deployment.get("runtime_code_hash")
        if expected_code and Web3.to_hex(Web3.keccak(code)) != expected_code:
            raise ValueError("Contract runtime code does not match the deployment artifact")
        self.contract = self.w3.eth.contract(address=address, abi=self.deployment["abi"])
        if self.evidence_dir:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    @_write_job("contract-deploy")
    def deploy(
        cls,
        rpc_url: str = "http://127.0.0.1:8545",
        deployment_path: str | Path = ROOT / "network/deployment.json",
        accounts_path: str | Path = ROOT / "network/accounts.json",
        evidence_dir: str | Path | None = None,
        *,
        artifact_path: str | Path = ROOT / "contracts/artifacts/FLAudit.json",
        receipt_timeout: float = 180.0,
    ) -> "AuditChain":
        """Deploy with administrator account zero; approve clients one through four."""
        accounts = _read_accounts(Path(accounts_path))
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if not w3.is_connected():
            raise ConnectionError(f"Besu RPC is unavailable: {rpc_url}")
        factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        tx = factory.constructor(accounts[0]["address"], [x["address"] for x in accounts[1:5]]).build_transaction({
            "from": accounts[0]["address"], "nonce": w3.eth.get_transaction_count(accounts[0]["address"], "pending"),
            "chainId": w3.eth.chain_id, "gas": 8_000_000, "gasPrice": 0,
        })
        signed = w3.eth.account.sign_transaction(tx, accounts[0]["private_key"])
        submitted = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=receipt_timeout, poll_latency=0.2)
        record = {
            "operation": "deploy", "submitted_at": submitted, "transaction_hash": Web3.to_hex(tx_hash),
            "status": receipt.status, "block_number": receipt.blockNumber,
            "confirmation_seconds": time.perf_counter() - start, "receipt": _jsonable(receipt),
            "sender": accounts[0]["address"], "chain_id": w3.eth.chain_id,
            "nonce": tx["nonce"], "gas_limit": tx["gas"], "gas_price": tx["gasPrice"],
        }
        if evidence_dir:
            out = Path(evidence_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "deployment_receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if receipt.status != 1:
            raise AuditTransactionError(record)
        address = receipt.contractAddress
        deployment = {
            "address": address, "chain_id": w3.eth.chain_id, "rpc_url": rpc_url,
            "transaction_hash": Web3.to_hex(tx_hash), "block_number": receipt.blockNumber,
            "abi": artifact["abi"], "compiler": artifact["compiler"], "evm_version": artifact["evmVersion"],
            "runtime_code_hash": Web3.to_hex(Web3.keccak(w3.eth.get_code(address))),
            "administrator": accounts[0]["address"], "participants": [a["address"] for a in accounts[1:5]],
        }
        target = Path(deployment_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(deployment, indent=2) + "\n", encoding="utf-8")
        return cls(rpc_url, target, accounts_path, evidence_dir, receipt_timeout=receipt_timeout)

    @staticmethod
    def run_key(run_id: str | bytes) -> bytes:
        if isinstance(run_id, bytes):
            if len(run_id) != 32:
                raise ValueError("A raw run ID must contain 32 bytes")
            return run_id
        # String IDs are labels, including strings that happen to look like hex.
        return bytes(Web3.keccak(text=run_id))

    @staticmethod
    def hash_bytes(model_hash: str | bytes) -> bytes:
        value = model_hash if isinstance(model_hash, bytes) else bytes.fromhex(model_hash.removeprefix("0x"))
        if len(value) != 32:
            raise ValueError("Model commitment must be exactly 32 bytes")
        return value

    @_write_job("audit-write")
    def _send_transaction(self, function: Any, operation: str, signer: int = 0, **metadata: Any) -> dict[str, Any]:
        """Sign and send once; return a receipt ticket without waiting for mining.

        The enclosing training/batch job keeps the project lock for the complete
        workflow. The per-account lock protects pending nonce allocation only
        through send acceptance, so distinct participant identities can overlap.
        """
        actor = self.accounts[signer]
        lock_key = f"{self.w3.eth.chain_id}:{actor['address']}"
        with self._locks_guard:
            lock = self._account_locks.setdefault(lock_key, threading.RLock())
        with lock:
            tx = function.build_transaction({
                "from": actor["address"], "nonce": self.w3.eth.get_transaction_count(actor["address"], "pending"),
                "chainId": self.w3.eth.chain_id, "gas": 3_000_000, "gasPrice": 0,
            })
            signed = self.w3.eth.account.sign_transaction(tx, actor["private_key"])
            prepared = {
                "operation": operation, "transaction_hash": Web3.to_hex(signed.hash), "sender": actor["address"],
                "chain_id": self.w3.eth.chain_id, "contract_address": self.contract.address,
                "nonce": tx["nonce"], "gas_limit": tx["gas"], "gas_price": tx["gasPrice"], **metadata,
            }
            # A send timeout may still have reached Besu. Keep its deterministic
            # signed hash before sending, without retaining raw bytes or keys.
            self._append("submission_attempts.jsonl", {**prepared, "prepared_at": datetime.now(timezone.utc).isoformat()})
            submitted = datetime.now(timezone.utc).isoformat()
            start = time.perf_counter()
            try:
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            except Exception as exc:
                self._append("submission_failures.jsonl", {**prepared, "submitted_at": submitted,
                    "error": f"{type(exc).__name__}: {exc}", "needs_reconciliation": True})
                raise
            if Web3.to_hex(tx_hash) != prepared["transaction_hash"]:
                raise RuntimeError("RPC returned a transaction hash different from the signed transaction")
            pending = {**prepared, "submitted_at": submitted, "submit_rpc_seconds": time.perf_counter() - start}
            self._submitted_tickets[pending["transaction_hash"]] = {"pending": pending, "start": start}
            self._append("submitted_transactions.jsonl", pending)
            return dict(pending)

    @_write_job("audit-confirm")
    def confirm_submission(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """Observe a previously sent transaction, never resend it on timeout.

        Confirmation time is send-to-first-observed-receipt, including the
        scheduler/training interval in pipelined mode. It is not an estimate of
        the unseen exact instant at which a block finalized.
        """
        tx_hash = ticket["transaction_hash"]
        lock_key = f"receipt:{self.w3.eth.chain_id}:{tx_hash}"
        with self._locks_guard:
            receipt_lock = self._account_locks.setdefault(lock_key, threading.RLock())
        with receipt_lock:
            if tx_hash in self._confirmed_tickets:
                record = self._confirmed_tickets[tx_hash]
                if record["status"] != 1:
                    raise AuditTransactionError(record)
                return dict(record)
            if tx_hash not in self._submitted_tickets:
                raise ValueError("Receipt ticket does not belong to this AuditChain instance")
            stored = self._submitted_tickets[tx_hash]
            pending, start = stored["pending"], stored["start"]
            try:
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.receipt_timeout, poll_latency=0.2)
            except Exception as exc:
                self._append("confirmation_failures.jsonl", {**pending,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(exc).__name__}: {exc}", "needs_reconciliation": True})
                raise
            observed = time.perf_counter()
            observed_at = datetime.now(timezone.utc).isoformat()
            if Web3.to_hex(receipt["transactionHash"]) != tx_hash:
                raise RuntimeError("Receipt transaction hash mismatch")
            events = []
            for event_name in ("RunCreated", "RoundOpened", "UpdateCommitted", "AggregateCommitted"):
                event = getattr(self.contract.events, event_name)()
                for decoded in event.process_receipt(receipt, errors=DISCARD):
                    events.append({"event": event_name, "args": _jsonable(decoded["args"]), "log_index": decoded["logIndex"]})
            record = {
                **pending, "status": receipt.status, "block_number": receipt.blockNumber,
                "confirmation_seconds": observed - start, "receipt_observed_at": observed_at,
                "confirmation_metric": "submission_to_first_receipt_observation_including_scheduler_wait",
                "gas_used": receipt.gasUsed,
                "events": events, "receipt": _jsonable(receipt),
            }
            record["tx_hash"] = record["transaction_hash"]
            record["latency_seconds"] = record["confirmation_seconds"]
            self._append("transactions.jsonl", record)
            self._confirmed_tickets[tx_hash] = record
            del self._submitted_tickets[tx_hash]
            if receipt.status != 1:
                raise AuditTransactionError(record)
            return dict(record)

    @_write_job("audit-write")
    def _transact(self, function: Any, operation: str, signer: int = 0, **metadata: Any) -> dict[str, Any]:
        """Backward-compatible send-and-wait operation."""
        return self.confirm_submission(self._send_transaction(function, operation, signer, **metadata))

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        if self.evidence_dir is not None:
            with self._evidence_lock:
                with (self.evidence_dir / filename).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")

    def start_run(self, run_id: str, participant_indices: list[int] | None = None) -> dict[str, Any]:
        indices = participant_indices if participant_indices is not None else [1, 2, 3, 4]
        actors = [self.accounts[i]["address"] for i in indices]
        return self._transact(self.contract.functions.createRun(self.run_key(run_id), actors), "create_run", run_id=run_id)

    def begin_round(self, run_id: str, round_no: int) -> dict[str, Any]:
        return self._transact(self.contract.functions.beginRound(self.run_key(run_id), round_no), "begin_round", run_id=run_id, round=round_no)

    def submit_update(self, run_id: str, round_no: int, client_index: int, update_hash: str | bytes) -> dict[str, Any]:
        value = self.hash_bytes(update_hash)
        return self._transact(self.contract.functions.submitUpdate(self.run_key(run_id), round_no, value), "submit_update", signer=client_index, run_id=run_id, round=round_no, client_id=client_index - 1, model_hash=Web3.to_hex(value), digest=Web3.to_hex(value))

    def submit_update_async(self, run_id: str, round_no: int, client_index: int, update_hash: str | bytes) -> dict[str, Any]:
        """Send one participant commitment; caller must confirm before aggregation."""
        value = self.hash_bytes(update_hash)
        return self._send_transaction(self.contract.functions.submitUpdate(self.run_key(run_id), round_no, value), "submit_update", signer=client_index, run_id=run_id, round=round_no, client_id=client_index - 1, model_hash=Web3.to_hex(value), digest=Web3.to_hex(value))

    def commit_aggregate(self, run_id: str, round_no: int, aggregate_hash: str | bytes) -> dict[str, Any]:
        value = self.hash_bytes(aggregate_hash)
        return self._transact(self.contract.functions.commitAggregate(self.run_key(run_id), round_no, value), "commit_aggregate", run_id=run_id, round=round_no, model_hash=Web3.to_hex(value), digest=Web3.to_hex(value))

    def get_update_hash(self, run_id: str, round_no: int, client_index: int) -> str:
        return Web3.to_hex(self.contract.functions.updateHashes(self.run_key(run_id), round_no, self.accounts[client_index]["address"]).call())

    def get_aggregate_hash(self, run_id: str, round_no: int) -> str:
        return Web3.to_hex(self.contract.functions.aggregateHashes(self.run_key(run_id), round_no).call())

    def get_run(self, run_id: str) -> dict[str, Any]:
        actor, round_no, open_, participants = self.contract.functions.getRun(self.run_key(run_id)).call()
        return {"run_id": run_id, "run_key": Web3.to_hex(self.run_key(run_id)), "aggregator": actor, "current_round": round_no, "round_open": open_, "participants": participants}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy or inspect the FLAudit contract on Besu")
    parser.add_argument("command", choices=["deploy", "inspect"])
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--deployment", default=str(ROOT / "network/deployment.json"))
    parser.add_argument("--accounts", default=str(ROOT / "network/accounts.json"))
    parser.add_argument("--evidence-dir", default=str(ROOT / "results/deployment"))
    args = parser.parse_args()
    kwargs = dict(rpc_url=args.rpc_url, deployment_path=args.deployment, accounts_path=args.accounts, evidence_dir=args.evidence_dir)
    chain = AuditChain.deploy(**kwargs) if args.command == "deploy" else AuditChain(**kwargs)
    print(json.dumps({"address": chain.contract.address, "chain_id": chain.w3.eth.chain_id, "block_number": chain.w3.eth.block_number}, indent=2))


if __name__ == "__main__":
    main()
