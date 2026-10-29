# Evidence map for the implementation report

This is an agent-checked drafting record, dated 29 October 2026. It does not assert accountable human source verification or submission approval. Source IDs and code hashes are retained in `source_manifest.json`; main figure/data provenance is retained in `figures/source_manifest.json` and `analysis.json`.

| ID | Source / locator | Claims supported and limits |
|---|---|---|
| M01 | `fl/runner.py`: `DEFAULT_CONFIG`, `LogicalClient.fit`, `aggregate_updates`, `run_training`; `fl/model.py`; `fl/data.py` | Four same-process sequential logical clients, actual Flower API, model/optimizer/data partition, seed and timing behavior. Execution success and outcome values require separate run artifacts |
| M02 | `fl/artifacts.py`: `HASH_ALGORITHM`, `hash_parameters`, `serialized_parameters`, `load_parameters` | Canonical model commitment, supported dtypes, file checks, NPZ payload accounting |
| M03 | `network/genesis.json`, `network/manage.py`, `network/runtime.json`, `requirements.lock` | Four native validators, nominal timing inputs, node permissioning, runtime download provenance and exact packages. Configuration does not prove expected runtime behavior |
| M04 | `contracts/FLAudit.sol`, `contracts/package.json`, `contracts/compile.cjs`, `audit/chain.py` | Contract state machine, role model, signing identities, receipt polling and normal transaction categories |
| M05 | `dashboard/data.py`: `query_commitment`; `experiments/run.py`: `ChainHook.verify` | Read-only live commitment lookup, queried block marker and independent readback logs |
| M06 | `experiments/run.py`, `experiments/batch.py`, `experiments/metrics.py`, `experiments/plot_results.py` | Managed baseline/treatment setup, measurement boundaries, CPU/RAM sampling, actual transaction-rate definition, seed schedule and descriptive analysis |
| R01 | `results/full-study-20261029-schedule.json`; six `full-study-20261029-{mode}-s{seed}` configuration/status/summary/round files | Actual scope, mode order, complete-run status, seeds, paired initial/data/final hashes |
| R02 | `report/analysis.json`; `report/analyze_results.py`; `report/figures/source_manifest.json` | Six-run performance statistics, all ten paired aggregate hashes, model payload and ledger growth; exact source hashes retained |
| R03 | Three integrated-run `transactions.jsonl` files; `experiments/metrics.py` | 183 successes, category counts, observed confirmation distributions and application-rate windows |
| R04 | Six `resources.csv` files; `report/resource_summary.csv`; `report/validator_resource_sums.csv` | Raw per-process CPU and RSS; validated four-role sampling-cycle sums, including the 16 ms maximum within-cycle spread |
| R05 | `results/contract-tests-dad4ad1cdc/checks.json` and `transactions.jsonl`; `results/smoke-chain-integration/integrity_test.json` | 19 checks, 9 successful/15 rejected receipts, original-model match and changed-copy mismatch |
| R06 | `results/fault-validation/fault_results.json` | One-stop confirmation, two-stop 15-second observations, restart snapshot, pending and fresh transaction recovery outcomes |
| R07 | `report/ui_validation.json` | Four real-data AppTest pages, full tenth-round model hash and live contract lookup at block 568, subsequent four-node snapshot |
| R08 | `results/delivery_validation.json`; `experiments/validate_delivery.py` | Six-run check, 183 successful receipts, 150 model artifacts checked against decoded events |
| R09 | `results/unit-tests.txt`; `results/reproduction-check/summary.json` and `status.json` | Seven passing unit tests, two warnings; fresh-output short same-environment audit run, excluded from main sample |
| B01 | Report reference 1: author arXiv abstract; journal metadata from Crossref DOI record | BlockFL architecture and latency-motivation statement. No reuse of its numerical results |
| B02–B04 | Report references 2–4: Crossref DOI records | Bibliography and application-domain titles only; no claim that this prototype reproduces their methods |
| B05 | Report reference 5: author arXiv abstract and Crossref DOI record | Survey identifies communication, resource, incentives, security and privacy topics |
| B06 | Report reference 6: USENIX publication record and MIT-hosted paper record | PBFT bibliographic identity and state-machine-replication rationale; actual QBFT behavior uses Besu documentation |
| B07 | Report reference 7: Crossref DOI record, including abstract | CAP impossibility statement in the asynchronous model. Quorum component consequences are this report's bounded design inference |
| B08 | Report reference 8: PMLR publisher paper record | Local-update aggregation / iterative model-averaging motivation |
| B09–B10 | Report references 9–10: official Besu QBFT documentation/tutorial | Validator quorum and stop threshold; four-node configuration |
| B11–B12 | Report references 11–12: official Flower and OpenZeppelin documentation, pinned upstream tags | Framework context and role-control library. Local source governs the implemented API and behavior |
| B13 | Report reference 13: official CIFAR webpage | Dataset dimensions, original train/test counts, archive checksum and requested technical-report citation |
| B14 | Report reference 14: current arXiv record | Procedural-skill attribution; no claim of measured effectiveness of the skill library |

**Review boundary.** IEEE article landing pages were blocked by the publisher's JavaScript/robot check. Bibliographic records were therefore checked against the publisher-deposited Crossref API. For references 1 and 5, author-posted arXiv abstracts were additionally opened. References 2–4 are intentionally used only for their stated application domains, not uninspected results. No full article text or unpublished project material was sent to an external service.

**Empirical bindings.** The completed main tables and four figures use only the fixed six full-data runs. Integrity, contract-negative, failure, UI, and reproduction records have separate IDs and are excluded from performance aggregates. Source hashes were refreshed after integration. Human interpretation and submission review remain pending; no additional training or chain writes were performed during report analysis and UI validation.
