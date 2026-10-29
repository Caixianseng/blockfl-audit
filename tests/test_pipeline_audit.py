"""Safety regression tests for receipt pipelining, using explicit unit fixtures."""
import csv
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import torch
from hexbytes import HexBytes
from torch.utils.data import TensorDataset
from web3.datastructures import AttributeDict

from audit.chain import AuditChain, AuditTransactionError
from fl.artifacts import load_parameters, save_parameters
from fl.runner import run_training


def fixture_data(config, output_dir):
    """Synthetic unit-test inputs only; never included in experiment evidence."""
    rng = torch.Generator().manual_seed(99)
    x = torch.randint(0, 256, (32, 3, 32, 32), dtype=torch.uint8, generator=rng)
    y = torch.arange(32) % 10
    return [TensorDataset(x[i*8:(i+1)*8], y[i*8:(i+1)*8]) for i in range(4)], TensorDataset(x[:8], y[:8]), "unit-fixture"


class TrackingAudit:
    def __init__(self, failure=None, output=None):
        self.failure, self.output = failure, output
        self.sent, self.confirmed, self.verified, self.commits = {}, set(), set(), {}
        self.events = []

    def begin_round(self, r):
        self.events.append((r, "begin", None))

    def submit(self, c, r, digest):
        self.sent[r, c] = digest
        self.confirmed.add((r, c))
        self.events.append((r, "sync_submit", c))

    def submit_async(self, c, r, digest):
        if self.failure == "send" and c == 2:
            raise TimeoutError("unit send timeout; signed hash must be reconciled")
        self.sent[r, c] = digest
        self.events.append((r, "send", c))
        return {"round": r, "client_id": c, "transaction_hash": f"unit-{r}-{c}"}

    def confirm(self, ticket):
        r, c = ticket["round"], ticket["client_id"]
        assert all((r, i) in self.sent for i in range(4)), "confirmation began before all sends"
        if self.failure == "receipt" and c == 2:
            raise TimeoutError("unit receipt timeout")
        self.confirmed.add((r, c))
        self.events.append((r, "confirm", c))
        if self.failure == "artifact" and c == 3:
            path = self.output / "updates/round_001_client_0.npz"
            parameters = load_parameters(path)
            name = sorted(parameters)[0]
            parameters[name].flat[0] += 1
            save_parameters(path, parameters)

    def verify(self, c, r, digest):
        if any(event[1] == "send" for event in self.events):
            assert all((r, i) in self.confirmed for i in range(4)), "verification preceded receipt barrier"
        assert (r, c) in self.confirmed
        if self.failure == "verify" and c == 2:
            return False
        self.verified.add((r, c))
        self.events.append((r, "verify", c))
        return self.sent[r, c] == digest

    def commit(self, r, digest):
        assert all((r, i) in self.verified for i in range(4))
        self.commits[r] = digest
        self.events.append((r, "commit", None))


def test_pipeline_matches_sync_every_round_and_preserves_receipt_barrier(monkeypatch, tmp_path):
    import fl.runner as runner
    monkeypatch.setattr(runner, "load_data", fixture_data)
    config = {"rounds": 2, "batch_size": 8, "device": "cpu", "download": False}
    sync, pipeline = TrackingAudit(), TrackingAudit()
    first = run_training(config, tmp_path / "sync", sync)
    original = runner.aggregate_updates

    def checked_aggregate(updates, round_id=1):
        assert all((round_id, i) in pipeline.verified for i in range(4))
        return original(updates, round_id)

    monkeypatch.setattr(runner, "aggregate_updates", checked_aggregate)
    second = run_training({**config, "audit_strategy": "pipelined"}, tmp_path / "pipeline", pipeline)
    assert first["initial_hash"] == second["initial_hash"]
    assert first["final_hash"] == second["final_hash"]
    assert first["final_accuracy"] == second["final_accuracy"]
    assert sync.commits == pipeline.commits
    assert second["audit_strategy"] == second["effective_audit_strategy"] == "pipelined"
    for round_id in (1, 2):
        events = [e[1] for e in pipeline.events if e[0] == round_id]
        assert events == ["begin"] + ["send"]*4 + ["confirm"]*4 + ["verify"]*4 + ["commit"]
    with (tmp_path / "sync/rounds.csv").open() as a, (tmp_path / "pipeline/rounds.csv").open() as b:
        assert [r["global_hash"] for r in csv.DictReader(a)] == [r["global_hash"] for r in csv.DictReader(b)]


@pytest.mark.parametrize("failure,match", [
    ("send", "send timeout"), ("receipt", "receipt timeout"),
    ("verify", "Chain commitment mismatch"), ("artifact", "Artifact changed"),
])
def test_pipeline_failure_never_aggregates_or_commits(monkeypatch, tmp_path, failure, match):
    import fl.runner as runner
    monkeypatch.setattr(runner, "load_data", fixture_data)
    calls = []
    monkeypatch.setattr(runner, "aggregate_updates", lambda *a, **k: calls.append(True))
    output = tmp_path / failure
    audit = TrackingAudit(failure, output)
    with pytest.raises((TimeoutError, RuntimeError), match=match):
        run_training({"rounds": 1, "batch_size": 8, "device": "cpu", "audit_strategy": "pipelined"}, output, audit)
    assert calls == [] and audit.commits == {}
    assert not (output / "global_001.npz").exists() and not (output / "summary.json").exists()
    assert json.loads((output / "status.json").read_text())["state"] == "failed"


def test_pipeline_rejects_missing_async_interface_before_training(tmp_path):
    with pytest.raises(TypeError, match="submit_async and confirm"):
        run_training({"audit_strategy": "pipelined"}, tmp_path / "unsupported", object())


def fake_chain(monkeypatch, tmp_path):
    """Model send and receipt RPC boundaries; no real chain writes or OS lock."""
    monkeypatch.setattr("audit.chain.job_lock", lambda operation: nullcontext())
    digest = HexBytes("0x" + "12"*32)

    class Eth:
        chain_id = 1337
        account = SimpleNamespace(sign_transaction=lambda tx, key: SimpleNamespace(hash=digest, raw_transaction=b"signed-unit-test"))
        send_calls = 0
        wait_calls = 0
        wait_error = None
        send_error = None
        receipt_status = 1

        def get_transaction_count(self, address, tag):
            assert tag == "pending"
            return 7

        def send_raw_transaction(self, raw):
            self.send_calls += 1
            if self.send_error:
                raise self.send_error
            return digest

        def wait_for_transaction_receipt(self, tx_hash, **kwargs):
            self.wait_calls += 1
            if self.wait_error:
                raise self.wait_error
            return AttributeDict({"status": self.receipt_status, "blockNumber": 10, "gasUsed": 123,
                                  "transactionHash": digest})

    chain = object.__new__(AuditChain)
    chain.accounts = [{"address": "0x" + "11"*20, "private_key": "unit-key-not-for-logging"}]
    event = lambda: SimpleNamespace(process_receipt=lambda receipt, **kwargs: [])
    chain.contract = SimpleNamespace(address="0x" + "ab"*20, events=SimpleNamespace(
        RunCreated=event, RoundOpened=event, UpdateCommitted=event, AggregateCommitted=event))
    chain.w3 = SimpleNamespace(eth=Eth())
    chain.evidence_dir, chain.receipt_timeout = tmp_path, 0.01
    chain._submitted_tickets, chain._confirmed_tickets = {}, {}
    function = SimpleNamespace(build_transaction=lambda fields: fields)
    return chain, function


def test_split_send_does_not_wait_and_confirmation_is_idempotent(monkeypatch, tmp_path):
    chain, function = fake_chain(monkeypatch, tmp_path)
    ticket = chain._send_transaction(function, "submit_update", client_id=0, round=1)
    assert chain.w3.eth.send_calls == 1 and chain.w3.eth.wait_calls == 0
    assert (tmp_path / "submitted_transactions.jsonl").exists()
    assert not (tmp_path / "transactions.jsonl").exists()
    record = chain.confirm_submission(ticket)
    assert record["status"] == 1 and record["nonce"] == 7
    assert record["confirmation_metric"] == "submission_to_first_receipt_observation_including_scheduler_wait"
    assert chain.confirm_submission(ticket) == record
    assert chain.w3.eth.wait_calls == 1
    assert len((tmp_path / "transactions.jsonl").read_text().splitlines()) == 1
    assert "unit-key-not-for-logging" not in "".join(p.read_text() for p in tmp_path.iterdir())


def test_receipt_timeout_retains_ticket_and_retry_only_rechecks_receipt(monkeypatch, tmp_path):
    chain, function = fake_chain(monkeypatch, tmp_path)
    ticket = chain._send_transaction(function, "submit_update")
    chain.w3.eth.wait_error = TimeoutError("receipt not yet observed")
    with pytest.raises(TimeoutError):
        chain.confirm_submission(ticket)
    assert ticket["transaction_hash"] in chain._submitted_tickets
    failure = json.loads((tmp_path / "confirmation_failures.jsonl").read_text())
    assert failure["needs_reconciliation"] and failure["transaction_hash"] == ticket["transaction_hash"]
    chain.w3.eth.wait_error = None
    assert chain.confirm_submission(ticket)["status"] == 1
    assert chain.w3.eth.send_calls == 1 and chain.w3.eth.wait_calls == 2


def test_reverted_receipt_remains_evidence_and_is_not_reappended(monkeypatch, tmp_path):
    chain, function = fake_chain(monkeypatch, tmp_path)
    chain.w3.eth.receipt_status = 0
    ticket = chain._send_transaction(function, "submit_update")
    for _ in range(2):
        with pytest.raises(AuditTransactionError):
            chain.confirm_submission(ticket)
    assert chain.w3.eth.wait_calls == 1
    assert json.loads((tmp_path / "transactions.jsonl").read_text())["status"] == 0


def test_send_timeout_retains_signed_hash_for_reconciliation(monkeypatch, tmp_path):
    chain, function = fake_chain(monkeypatch, tmp_path)
    chain.w3.eth.send_error = TimeoutError("uncertain send acceptance")
    with pytest.raises(TimeoutError):
        chain._send_transaction(function, "submit_update")
    attempt = json.loads((tmp_path / "submission_attempts.jsonl").read_text())
    failure = json.loads((tmp_path / "submission_failures.jsonl").read_text())
    assert attempt["transaction_hash"] == failure["transaction_hash"]
    assert failure["needs_reconciliation"]
    assert chain.w3.eth.send_calls == 1 and chain.w3.eth.wait_calls == 0
