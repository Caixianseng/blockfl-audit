**FLAudit contract**

Compile from this directory with Node.js and npm:

```powershell
npm ci --no-audit --no-fund --cache './.npm-cache'
npm run compile
```

The lockfile pins OpenZeppelin Contracts 5.4.0 (MIT) and solc-js 0.8.24.
Compilation targets the Paris EVM to avoid accidental reliance on newer opcodes
than the configured private chain supports. `artifacts/FLAudit.json` contains
the ABI, deployment bytecode, runtime bytecode and compiler identity.

From the project root, after the four-node Besu network is running and the
Python dependencies are installed:

```powershell
.venv/Scripts/python.exe -m audit.chain deploy
```

`network/accounts.json` must contain a JSON array (or an `accounts` array in an
object) of at least five funded local-demo signing accounts. Each entry is an
`address` and `private_key`. Account 0 is the administrator and aggregator;
accounts 1–4 are the clients. Treat this as demo-only key material; never use
the accounts on a public chain. No keys are written to receipt evidence.

The chain must permit zero gas price and a transaction gas limit of 8,000,000
for deployment. Regular audit writes use a 3,000,000 gas cap; actual gas used
is recorded in each receipt. A zero gas price still consumes EVM gas and does
not mean consensus, CPU or storage is free.

Python integration:

```python
from audit.chain import AuditChain

chain = AuditChain(evidence_dir="results/my-run")
chain.start_run("my-run")
chain.begin_round("my-run", 1)
for client_index in range(1, 5):
    # Compute this digest from the actual update artifact.
    digest = actual_update_digest[client_index]
    chain.submit_update("my-run", 1, client_index, digest)
    assert chain.get_update_hash("my-run", 1, client_index) == "0x" + digest.removeprefix("0x")
chain.commit_aggregate("my-run", 1, actual_aggregate_digest)
```

Run labels are keccak256-hashed UTF-8 labels; model commitments are caller-
provided 32-byte digests. Model digest serialization must be documented by the
training module. A string that looks like a hex run ID is still treated as a
label. `get_update_hash` and `get_aggregate_hash` read contract state via RPC.

The contract enforces four approved and distinct participants per run,
aggregator identity and role, consecutive rounds, one nonzero update per
participant per round, and all four updates before the nonzero aggregate
commitment. Previous run/round commitments cannot be overwritten. Contract
access control does not validate model quality or aggregation correctness.
The administrator can grant/revoke roles, and loss/revocation of an active
participant can halt a round. This prototype intentionally has no dropout
or emergency-round-recovery policy.

`submitted_transactions.jsonl` records accepted transactions before waiting.
`transactions.jsonl` records confirmed status-one or status-zero receipts,
contract events, identities, rounds, gas, and wall-clock submission-to-receipt
latency. Timeout cases remain in the submitted log and are not automatically
resent. Investigate their chain state before retrying. Status-zero writes raise
`AuditTransactionError` while preserving their evidence.

Enable the live tests explicitly:

```powershell
$env:BLOCKFL_LIVE_TESTS='1'
.venv/Scripts/python.exe -m unittest discover -s tests -p test_contract_live.py -v
```

The tests use unique run labels and retain evidence in their own result
directory. They require a running local Besu network and an already-deployed
contract; they do not substitute a different EVM network for QBFT.

References: [OpenZeppelin Access Control](https://docs.openzeppelin.com/contracts/5.x/access-control)
and [Web3.py signed transactions](https://web3py.readthedocs.io/en/stable/transactions.html).
