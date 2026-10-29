"""Explicit live Besu validation, with status-zero as well as status-one receipts.

Run from the project root:
  BLOCKFL_LIVE_TESTS=1 python -m unittest discover -s tests -p test_contract_live.py -v
PowerShell uses: $env:BLOCKFL_LIVE_TESTS='1'

These tests mutate only the local demo contract and create fresh UUID run labels.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@unittest.skipUnless(os.environ.get("BLOCKFL_LIVE_TESTS") == "1", "requires explicitly enabled local Besu network")
class LiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from audit.chain import AuditChain

        cls.out = ROOT / "results" / f"contract-tests-{uuid.uuid4().hex[:10]}"
        cls.chain = AuditChain(
            rpc_url=os.environ.get("BLOCKFL_RPC", "http://127.0.0.1:8545"),
            deployment_path=os.environ.get("BLOCKFL_DEPLOYMENT", str(ROOT / "network/deployment.json")),
            accounts_path=os.environ.get("BLOCKFL_ACCOUNTS", str(ROOT / "network/accounts.json")),
            evidence_dir=cls.out,
        )
        cls.checks = []
        cls.completed = False

    def rejected(self, label, action):
        from audit.chain import AuditTransactionError

        with self.assertRaises(AuditTransactionError) as caught:
            action()
        self.assertEqual(caught.exception.record["status"], 0)
        self.checks.append({"check": label, "passed": True, "transaction_hash": caught.exception.record["transaction_hash"]})

    def test_01_happy_path_and_rejections(self):
        from web3 import Web3

        chain = self.chain
        label = f"contract-validation-{uuid.uuid4().hex}"
        key = chain.run_key(label)
        digest = Web3.to_hex(Web3.keccak(text="deterministic test model"))
        changed = Web3.to_hex(Web3.keccak(text="changed test model"))
        chain.start_run(label)
        chain.begin_round(label, 1)
        self.assertEqual(chain.get_run(label)["current_round"], 1)
        self.rejected("reopening an unfinished round", lambda: chain.begin_round(label, 1))
        self.rejected("unauthorized run creation", lambda: chain._transact(chain.contract.functions.createRun(chain.run_key(label + "-unauthorized"), [a["address"] for a in chain.accounts[1:5]]), "test_create_run", signer=1, run_id=label + "-unauthorized"))
        self.rejected("unauthorized participant", lambda: chain.submit_update(label, 1, 0, digest))
        self.rejected("wrong round", lambda: chain.submit_update(label, 2, 1, digest))
        self.rejected("zero commitment", lambda: chain.submit_update(label, 1, 1, "00" * 32))
        self.rejected("premature aggregation", lambda: chain.commit_aggregate(label, 1, digest))
        self.rejected("unauthorized round administration", lambda: chain._transact(chain.contract.functions.beginRound(key, 2), "test_begin_round", signer=1, run_id=label, round=2))
        chain.submit_update(label, 1, 1, digest)
        self.rejected("duplicate update", lambda: chain.submit_update(label, 1, 1, changed))
        self.assertEqual(chain.get_update_hash(label, 1, 1), digest)
        self.rejected("unauthorized aggregation", lambda: chain._transact(chain.contract.functions.commitAggregate(key, 1, chain.hash_bytes(digest)), "test_commit_aggregate", signer=1, run_id=label, round=1))
        for index in (2, 3, 4):
            chain.submit_update(label, 1, index, digest)
        self.assertEqual(chain.contract.functions.submissionCounts(key, 1).call(), 4)
        self.rejected("zero aggregate commitment", lambda: chain.commit_aggregate(label, 1, "00" * 32))
        chain.commit_aggregate(label, 1, digest)
        self.assertEqual(chain.get_aggregate_hash(label, 1), digest)
        self.assertFalse(chain.get_run(label)["round_open"])
        self.checks.append({"check": "four updates and aggregate commitment", "passed": True, "run_id": label})
        self.rejected("aggregate overwrite", lambda: chain.commit_aggregate(label, 1, changed))
        self.rejected("closed-round submission", lambda: chain.submit_update(label, 1, 2, changed))
        self.rejected("skipped round", lambda: chain.begin_round(label, 3))
        self.rejected("run overwrite", lambda: chain.start_run(label))
        chain.begin_round(label, 2)
        self.assertEqual(chain.get_update_hash(label, 2, 1), "0x" + "00" * 32)
        self.assertEqual(chain.get_update_hash(label, 1, 1), digest)
        self.assertEqual(chain.get_aggregate_hash(label, 1), digest)
        self.checks.append({"check": "round isolation and immutable previous commitments", "passed": True})
        other = f"contract-isolation-{uuid.uuid4().hex}"
        chain.start_run(other)
        self.assertEqual(chain.get_run(other)["current_round"], 0)
        self.assertEqual(chain.get_update_hash(other, 1, 1), "0x" + "00" * 32)
        self.checks.append({"check": "run isolation", "passed": True})
        invalid = f"contract-invalid-members-{uuid.uuid4().hex}"
        self.rejected("duplicate participant list", lambda: chain.start_run(invalid, [1, 1, 2, 3]))
        self.assertEqual(chain.get_run(invalid)["aggregator"], "0x" + "00" * 20)
        self.checks.append({"check": "rejected creation rolled back all run state", "passed": True})
        type(self).completed = True

    @classmethod
    def tearDownClass(cls):
        (cls.out / "checks.json").write_text(json.dumps({"completed": cls.completed, "checks": cls.checks, "chain_id": cls.chain.w3.eth.chain_id, "contract_address": cls.chain.contract.address}, indent=2) + "\n", encoding="utf-8")
        print(f"Contract evidence: {cls.out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
