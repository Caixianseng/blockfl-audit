"""Build the optimization appendix from six explicitly selected new chain runs.

This script only reads saved experiment evidence and writes files in report/.
It refuses incomplete runs, mismatched pairs and inconsistent model receipts.
It does not train models, manage validators, or send transactions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from web3 import Web3

from fl.artifacts import verify_artifact

PREFIX = "audit-optimization-20261029-final"
SEEDS = (42, 43, 44)
STRATEGIES = ("sync", "pipelined")
COLORS = {"sync": "#087f81", "pipelined": "#4f5dcb"}
LABELS = {"sync": "Sequential receipt waits", "pipelined": "Pipelined commitments"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def describe(values):
    values = [float(value) for value in values]
    if not values:
        raise ValueError("No observations for the requested statistic")
    return {"n": len(values), "mean": statistics.mean(values),
            "sample_sd": statistics.stdev(values) if len(values) > 1 else None,
            "min": min(values), "max": max(values)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_record(path):
    path = Path(path)
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_run(path, seed, strategy):
    required = ("config.json", "status.json", "summary.json", "rounds.csv", "manifest.jsonl",
                "transactions.jsonl", "verifications.jsonl", "audit_context.json", "submitted_transactions.jsonl",
                "data_indices.json", "global_000.npz")
    missing = [name for name in required if not (path / name).is_file()]
    require(not missing, f"Incomplete optimization run {path.name}: missing {missing}")
    cfg, summary, status = (read_json(path / name) for name in ("config.json", "summary.json", "status.json"))
    require(status.get("state") == "completed", f"Run not complete: {path.name}")
    require(summary.get("mode") == "blockchain", f"Expected blockchain mode: {path.name}")
    require(summary.get("seed") == seed and summary.get("audit_strategy") == strategy,
            f"Seed/strategy mismatch: {path.name}")
    require(summary.get("rounds") == 10 and summary.get("train_samples") == 50000 and summary.get("test_samples") == 10000,
            f"The main optimization analysis requires ten full-data rounds: {path.name}")
    require(hashlib.sha256((path/'data_indices.json').read_bytes()).hexdigest()==summary['data_indices_sha256'],
            f"Saved data-index file does not match its recorded hash: {path.name}")
    require(verify_artifact(path/'global_000.npz',summary['initial_hash']),f"Initial model commitment mismatch: {path.name}")
    rounds = pd.read_csv(path / "rounds.csv")
    require(list(rounds["round"]) == list(range(1, 11)), f"Unexpected round sequence: {path.name}")
    manifest = read_jsonl(path / "manifest.jsonl")
    transactions = read_jsonl(path / "transactions.jsonl")
    require(len(transactions) == 61 and all(tx.get("status") == 1 for tx in transactions),
            f"Expected 61 successful audit transactions: {path.name}")
    expected_counts = {"create_run": 1, "begin_round": 10, "submit_update": 40, "commit_aggregate": 10}
    require(dict(Counter(tx["operation"] for tx in transactions)) == expected_counts,
            f"Unexpected transaction categories: {path.name}")
    require(len({tx["transaction_hash"] for tx in transactions}) == 61, f"Duplicate transaction evidence: {path.name}")
    context = read_json(path / "audit_context.json")
    run_key = Web3.to_hex(Web3.keccak(text=context["run_id"])).lower()
    event_hashes = {}
    senders = {}
    for tx in transactions:
        receipt = tx["receipt"]
        require(receipt["status"] == 1 and receipt["transactionHash"].lower() == tx["transaction_hash"].lower(),
                f"Receipt identity/status mismatch in {path.name}")
        require(tx["contract_address"].lower() == context["contract_address"].lower(), f"Wrong contract: {path.name}")
        for event in tx["events"]:
            args = event["args"]
            require(args["runId"].lower() == run_key, f"Event belongs to a different run: {path.name}")
            if event["event"] == "UpdateCommitted":
                client = tx["client_id"]
                require(args["participant"].lower() == receipt["from"].lower(), f"Update identity mismatch: {path.name}")
                sender = args["participant"].lower()
                require(client not in senders or senders[client] == sender, f"Client identity changed: {path.name}")
                senders[client] = sender
                key = ("client_update", args["round"], client)
                require(key not in event_hashes, f"Duplicate update event: {path.name}")
                event_hashes[key] = args["updateHash"].removeprefix("0x").lower()
            elif event["event"] == "AggregateCommitted":
                key = ("global_model", args["round"], None)
                require(key not in event_hashes, f"Duplicate aggregate event: {path.name}")
                event_hashes[key] = args["aggregateHash"].removeprefix("0x").lower()
    require(len(senders) == len(set(senders.values())) == 4, f"Expected four distinct client identities: {path.name}")
    artifact_sources = []
    seen = set()
    for item in manifest:
        if item["round"] == 0:
            continue
        key = (item["kind"], item["round"], item.get("client_id"))
        require(key not in seen, f"Duplicate manifest entry: {path.name}/{key}")
        seen.add(key)
        require(key in event_hashes, f"Model missing decoded commitment event: {path.name}/{key}")
        expected = event_hashes[key]
        artifact = (path / item["path"]).resolve()
        require(artifact.is_relative_to(path.resolve()), f"Unsafe artifact path: {path.name}")
        require(item["hash"].lower() == expected and verify_artifact(artifact, expected),
                f"Model does not match decoded receipt event: {artifact}")
        artifact_sources.append({**source_record(artifact), "commitment": expected,
                                 "kind": item["kind"], "round": item["round"], "client_id": item.get("client_id")})
    require(len(seen) == len(event_hashes) == 50, f"Expected 40 local updates and ten aggregates: {path.name}")
    readbacks = read_jsonl(path / "verifications.jsonl")
    require(len(readbacks) == 40 and all(record.get("matched") for record in readbacks),
            f"Missing successful live readbacks: {path.name}")
    for record in readbacks:
        expected = event_hashes[("client_update", record["round"], record["client_id"])]
        require(record["chain_hash"].removeprefix("0x").lower() == expected == record["local_hash"].removeprefix("0x").lower(),
                f"Readback/event mismatch: {path.name}")
    require(summary["confirmed_audit_transactions"] == 61, f"Incorrect summarized transaction count: {path.name}")
    return {"name": path.name, "path": path, "config": cfg, "summary": summary,
            "rounds": rounds, "transactions": transactions, "artifacts": artifact_sources,
            "sources": [source_record(path / name) for name in required]}


def figures(runs, folder):
    folder.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": .22, "figure.facecolor": "white"})
    outputs = []
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.5), layout="constrained")
    for ax, field, title, ylabel in zip(axes, ("total_seconds", "mean_round_seconds"),
                                       ("Timed training loop", "Mean global round"), ("Seconds per run", "Seconds per round")):
        for x, strategy in enumerate(STRATEGIES):
            values = [run["summary"][field] for run in runs if run["summary"]["audit_strategy"] == strategy]
            ax.bar(x, statistics.mean(values), color=COLORS[strategy], alpha=.78, width=.62)
            ax.errorbar(x, statistics.mean(values), yerr=statistics.stdev(values), capsize=5, color="#183542")
            for offset, value in zip((-.10, 0, .10), values):
                ax.scatter(x + offset, value, color="#183542", zorder=3, s=30)
        ax.set_xticks((0, 1), ("Sequential", "Pipelined"))
        ax.set_ylim(bottom=0)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
    fig.suptitle("New seed-paired audit comparison · full CIFAR-10\nDots = complete runs; error bars = sample SD (n = 3 per strategy)")
    for ext in ("png", "pdf"):
        out = folder / f"audit-performance.{ext}"
        fig.savefig(out, dpi=220, bbox_inches="tight")
        outputs.append(source_record(out))
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.5), layout="constrained")
    for strategy in STRATEGIES:
        members = [run for run in runs if run["summary"]["audit_strategy"] == strategy]
        for ax, field, ylabel in zip(axes, ("accuracy", "loss"), ("Test accuracy", "Test loss")):
            values = np.array([run["rounds"][field].to_numpy() for run in members])
            mean, sd = values.mean(axis=0), values.std(axis=0, ddof=1)
            x = np.arange(1, 11)
            ax.plot(x, mean, color=COLORS[strategy], marker="o", markersize=4, label=LABELS[strategy])
            ax.fill_between(x, mean-sd, mean+sd, color=COLORS[strategy], alpha=.12)
            ax.set_xlabel("Global round")
            ax.set_ylabel(ylabel)
    axes[0].set_ylim(0, 1)
    axes[1].legend(loc="best", fontsize=9)
    fig.suptitle("Model results · mean ± sample SD across three seeds\nPaired global model hashes are checked at every round")
    for ext in ("png", "pdf"):
        out = folder / f"audit-learning-curves.{ext}"
        fig.savefig(out, dpi=220, bbox_inches="tight")
        outputs.append(source_record(out))
    plt.close(fig)
    return outputs


def mean_sd(stat, digits=2):
    return f"{stat['mean']:.{digits}f} ± {stat['sample_sd']:.{digits}f}"


def build_markdown(analysis):
    groups, pairs = analysis["strategy_statistics"], analysis["pairs"]
    rows = []
    for strategy in STRATEGIES:
        group = groups[strategy]
        accuracy = group["final_accuracy"]
        rows.append(f"| {LABELS[strategy]} | 3 | {accuracy['mean']*100:.2f} ± {accuracy['sample_sd']*100:.2f} | {mean_sd(group['total_seconds'])} | {mean_sd(group['mean_round_seconds'],3)} | {mean_sd(group['application_audit_tps'],3)} |")
    pair_rows = [f"| {p['seed']} | {p['sync_seconds']:.3f} | {p['pipelined_seconds']:.3f} | {p['saved_seconds']:.3f} | {p['saved_percent']:.2f} |" for p in pairs]
    conflicts = analysis.get("reliability_evidence", {}).get("conflict_validation", {})
    conflict_statement = (f"The recorded mutual-exclusion integration check passed for {len(conflicts.get('checks',[]))} mutation entry points, with the validator network still ready. See `../results/optimization-conflict-checks/validation.json`." if conflicts.get("passed") else "A completed CLI mutual-exclusion validation record was not available when this appendix was generated; it is not claimed as passed here.")
    unit = analysis.get("reliability_evidence", {}).get("unit_test_result")
    unit_statement = f"The retained unit-test log reports **{unit}** (`../results/optimization-unit-tests.txt`)." if unit else "The final aggregate unit-test result has not been extracted from a retained log."
    contract=analysis.get('reliability_evidence',{}).get('contract_regression',{})
    contract_statement=(f"The retained real-chain contract regression completed with {len(contract['checks'])} successful checks (`../results/contract-tests-f665295258/checks.json`); its negative transactions are outside this performance cohort." if contract.get('completed') and all(c.get('passed') for c in contract.get('checks',[])) else "")
    preflight=analysis.get('preflight_attempt',{})
    preflight_statement=(f"A separate initial engineering preflight attempt was retained at `../results/audit-optimization-20261029-pipelined-s42/` and failed the readiness gate with `{preflight['status'].get('error','unknown error')}`. It did not reach the training-result stage and is excluded from the six completed runs rather than silently merged or replaced in their statistics. The new cohort uses its own explicit `-final` schedule." if preflight else "")
    ui=analysis.get('ui_validation',{})
    ui_statement=(f"Read-only Streamlit AppTest checks passed for all four pages, the pipelined selector and the explicit six-run study filter. The complete tenth-round pipelined model also matched a live `eth_call` commitment at block {ui['chain_readback']['block_number']}. The evidence is `optimization_ui_validation.json`; no training launch or signed transaction was requested by that check." if ui.get('checks') and all(ui['checks'].values()) else "A completed read-only optimization UI validation record was not available at generation time; no UI pass is claimed here.")
    order = " → ".join(f"s{job['seed']}:{job['strategy']}" for job in analysis["schedule"]["jobs"])
    return f"""# Optimization appendix: readiness, task coordination and pipelined audit submissions

**Status:** Measured implementation draft, {datetime.now(timezone.utc).date().isoformat()}. This appendix describes the new optimization cohort. The original `report.md`, `analysis.json`, source manifest and `figures/` remain the v1 record. Numerical results below use only the six explicitly selected new full-data runs; old v1 timings and smoke runs are excluded from the new comparison.

## Implemented changes

Network readiness now checks four owned validator processes before accepting RPC observations. It checks chain ID, agreement on the genesis hash and the expected QBFT validator set, then requires all four observed block heights to advance beyond a fresh baseline. A responding endpoint with a stored height alone cannot pass this check. The checks are in `network/manage.py` and their regression cases in `tests/test_network_ready.py`. This establishes the implemented local readiness conditions; it does not constitute a proof of Byzantine behavior under arbitrary faults.

Mutating entry points share an OS file lock, including training, batches, network management, contract writes and fault experiments. The JSON sidecar communicates owner information; it does not determine lock ownership. The scheduler retains its lease for the entire comparison and delegates only to its verified descendant processes. The UI displays busy state and relies on the child process's authoritative lock acquisition. The lock is released by the OS after owner termination. The process-tree cleanup accounts for the Windows virtual-environment redirector. See `common/job_lock.py` and `common/processes.py`.

The default `sync` audit strategy is retained. Under `pipelined`, a client trains, signs and sends its own update, then the coordinator can train the next client without waiting for that receipt. After all four sends, all four successful receipts are required; all four actual model files are reloaded and independently checked against live contract readbacks before FedAvg is allowed. Aggregation is then committed as before. GPU training itself remains sequential. A failed send, receipt, file check or chain readback prevents aggregation. Prepared signed transaction hashes and submission/confirmation failures are retained for diagnosis; this iteration does not implement full automatic interrupted-run recovery.

## New comparison design and validation

The cohort is `{analysis['run_prefix']}` with exactly `sync` and `pipelined` for seeds 42, 43 and 44. Each run uses all 50,000 CIFAR-10 training images and 10,000 test images, ten rounds, four logical clients, and one local epoch. The paired training conditions, initialization and data-index hashes agree. The fixed scheduler seed is {analysis['schedule']['schedule_seed']}; the saved order is {order}. A complete run is the replicate. The analysis is descriptive, with sample SD across three runs per strategy; no significance or broad performance guarantee is asserted.

{preflight_statement}

The analysis independently checked **{analysis['validation']['model_artifacts']} saved models** against successful decoded `UpdateCommitted` and `AggregateCommitted` events and deterministic file commitments. The **{analysis['validation']['transactions']} receipts** comprise 61 successful application transactions per run: one run creation, ten round openings, 40 update commitments and ten aggregate commitments. Transaction counts, identities, run keys, receipt hashes and local/live readback agreement are checked in `analyze_optimization.py`. All ten corresponding aggregate-model hashes, initial hashes and data-index hashes match for every seed pair. These checks validate the saved evidence and deterministic outcome of the selected runs, not the honesty of arbitrary training or the correctness of every future aggregation.

{unit_statement} {conflict_statement} {contract_statement}

{ui_statement}

## Measured results

| Strategy | Runs | Final accuracy, % (mean ± SD) | Training-loop seconds (mean ± SD) | Mean round seconds (mean ± SD) | Application audit tx/s (mean ± SD) |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

| Seed | Sync loop seconds | Pipelined loop seconds | Saved seconds | Saved % |
|---|---:|---:|---:|---:|
{chr(10).join(pair_rows)}

Mean paired saving was **{mean_sd(analysis['paired_saved_seconds'])} seconds**. The ratio of mean pipelined to mean synchronous loop time was **{analysis['ratio_of_mean_loop_times']:.4f}**, or **{analysis['reduction_of_mean_loop_time_percent']:.2f}% less timed loop duration** in this local experiment. The corresponding final model hashes matched, so the measured timing difference was obtained without changing those paired model parameters. This is a measured result for the retained conditions, not an a priori guarantee for a different network, model or machine.

![New sync/pipelined timing comparison](optimization_figures/audit-performance.png)

*Figure 1. Only the new six full-data runs. Each point is one complete run. Error bars are sample SD across three seeds per strategy. The timed training loop excludes network startup, initial dataset preparation and run creation; these remain separately recorded in the per-run summaries.*

![New paired learning curves](optimization_figures/audit-learning-curves.png)

*Figure 2. Mean and sample SD of test accuracy and loss. The paired curves overlap when the model hashes match. This does not establish statistical equivalence for untested configurations.*

## Timing boundaries and limitations

`confirmation_seconds` is explicitly a **send-to-first-observed-receipt interval**, including local training and scheduler delay in pipelined mode. A transaction may have finalized before the coordinator asks for its receipt. Therefore that field is not the exact physical finality delay, and comparing it as if it measured a change in consensus speed would be misleading. It is retained as an application-observation metric. Application tx/s uses the first submission to the last observed receipt window, including run creation and preparation gaps; it is not saturated blockchain TPS.

The complete workflow wall time includes orchestration and preparation; its mean ± SD was {mean_sd(groups['sync']['workflow_wall_seconds'])} seconds for sync and {mean_sd(groups['pipelined']['workflow_wall_seconds'])} seconds for pipelined. The two strategies each retain the same 61 audit transactions per run and the same model-payload byte counts. This optimization overlaps waiting with useful local work and permits multiple pending identities; it does not reduce the audit contract's required participants or suppress successful receipt/hash verification.

The four FL clients still share one process and GPU. Validators are native local processes. The host is not an isolated performance-test machine, and the shared ledger accumulates history across runs; empty blocks, database maintenance and other host activity can affect timing and storage. Three paired seeds give a descriptive variability estimate. Independent FL processes, non-IID/client-dropout experiments and full transaction/run recovery remain future work, outside this iteration's three approved changes.

After the timing cohort, the public `run_training` function received the same outer job guard already used by the experiment entry point. This guard executes before the training-loop clocks. No model algorithm or timed training loop was changed between the six selected runs. The provenance ledger records the final packaged source, including that additional entry-point protection.

## Rebuild and provenance

From the project root, run `.venv/Scripts/python.exe report/analyze_optimization.py`. It refuses missing, incomplete or inconsistent evidence and writes only this optimization appendix, `optimization_analysis.json`, `optimization_provenance.json`, CSV tables and `optimization_figures/`. It never trains or sends a signed transaction. The provenance ledger includes the explicit selection, input hashes, model file hashes and analysis-source hash. Figure exports are PNG and PDF. The original v1 report and figures are unchanged.

Scientific interpretation and final submission remain subject to the named authors' review. Development-assistance procedures are documented in the original report; this appendix adds no new claim of human source verification.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=PREFIX)
    args = parser.parse_args()
    report = ROOT / "report"
    required_paths = [ROOT/"results"/f"{args.prefix}-{strategy}-s{seed}" for seed in SEEDS for strategy in STRATEGIES]
    missing = [path.name for path in required_paths if not (path/"summary.json").is_file()]
    require(not missing, f"Optimization analysis waits for all six complete runs; missing summaries: {missing}")
    runs = [validate_run(ROOT/"results"/f"{args.prefix}-{strategy}-s{seed}", seed, strategy) for seed in SEEDS for strategy in STRATEGIES]
    schedule_path = ROOT/"results"/f"{args.prefix}-schedule.json"
    schedule = read_json(schedule_path)
    require(len(schedule["jobs"]) == 6 and {(j["seed"],j["strategy"]) for j in schedule["jobs"]} == {(seed,strategy) for seed in SEEDS for strategy in STRATEGIES}, "Unexpected optimization schedule")
    pairs = []
    training_fields = ("partition_seed", "clients", "rounds", "local_epochs", "batch_size", "learning_rate", "momentum", "device", "cpu_threads", "actual_train_samples", "actual_test_samples")
    for seed in SEEDS:
        a,b=(next(run for run in runs if run["summary"]["seed"]==seed and run["summary"]["audit_strategy"]==strategy) for strategy in STRATEGIES)
        for field in training_fields:
            require(a["config"].get(field)==b["config"].get(field), f"Paired training configuration differs for seed {seed}: {field}")
        for field in ("initial_hash", "final_hash", "data_indices_sha256", "total_model_bytes_up", "total_model_bytes_down"):
            require(a["summary"][field]==b["summary"][field],f"Paired run differs for seed {seed}: {field}")
        require(list(a["rounds"]["global_hash"])==list(b["rounds"]["global_hash"]), f"Paired model differs during seed {seed}")
        at,bt=a["summary"]["total_seconds"],b["summary"]["total_seconds"]
        pairs.append({"seed":seed,"sync_seconds":at,"pipelined_seconds":bt,"saved_seconds":at-bt,
                      "saved_percent":100*(1-bt/at),"initial_data_final_hashes_equal":True,"all_ten_global_hashes_equal":True})
    metrics=("final_accuracy","final_loss","total_seconds","mean_round_seconds","total_audit_seconds","workflow_wall_seconds",
             "application_audit_tps","application_audit_window_seconds","mean_confirmation_seconds","ledger_growth_bytes_all_nodes",
             "total_model_bytes_up","total_model_bytes_down")
    groups={strategy:{field:describe(run["summary"][field] for run in runs if run["summary"]["audit_strategy"]==strategy) for field in metrics} for strategy in STRATEGIES}
    reliability={}
    conflict_path=ROOT/"results/optimization-conflict-checks/validation.json"
    if conflict_path.exists():reliability["conflict_validation"]=read_json(conflict_path)
    unit_path=ROOT/"results/optimization-unit-tests.txt"
    if unit_path.exists():
        matches=re.findall(r"\d+ passed[^\r\n]*",unit_path.read_text(encoding="utf-8-sig",errors="replace"))
        reliability["unit_test_result"]=matches[-1] if matches else None
    contract_path=ROOT/'results/contract-tests-f665295258/checks.json'
    if contract_path.exists():reliability['contract_regression']=read_json(contract_path)
    preflight_path=ROOT/'results/audit-optimization-20261029-pipelined-s42/status.json'
    preflight={}
    if preflight_path.exists():
        preflight={'status':read_json(preflight_path),'source':source_record(preflight_path)}
    ui_path=report/'optimization_ui_validation.json'
    ui_validation=read_json(ui_path) if ui_path.exists() else {}
    if ui_validation:
        require(ui_validation.get('source_run','').startswith(args.prefix+'-'), 'UI validation belongs to a different optimization study')
        require(ui_validation.get('app_source_sha256')==source_record(ROOT/'dashboard/app.py')['sha256'], 'UI source changed after its validation; repeat the affected UI check')
        require(ui_validation.get('data_source_sha256')==source_record(ROOT/'dashboard/data.py')['sha256'], 'Dashboard data source changed after its validation; repeat the affected UI check')
    ratio=groups['pipelined']['total_seconds']['mean']/groups['sync']['total_seconds']['mean']
    analysis={"generated_at":datetime.now(timezone.utc).isoformat(),"run_prefix":args.prefix,
              "runs":[{"name":run['name'],**run['summary']} for run in runs],"strategy_statistics":groups,"pairs":pairs,
              "paired_saved_seconds":describe(p['saved_seconds'] for p in pairs),"ratio_of_mean_loop_times":ratio,
              "reduction_of_mean_loop_time_percent":100*(1-ratio),"schedule":schedule,
              "validation":{"passed":True,"transactions":sum(len(run['transactions']) for run in runs),
                            "model_artifacts":sum(len(run['artifacts']) for run in runs)},"reliability_evidence":reliability,
              "ui_validation":ui_validation,
              "preflight_attempt":preflight,
              "latency_scope":"send to first observed receipt, including scheduler/training delay; not physical finality"}
    figure_records=figures(runs,report/'optimization_figures')
    (report/'optimization_analysis.json').write_text(json.dumps(analysis,indent=2),encoding='utf-8')
    pd.DataFrame(pairs).to_csv(report/'optimization_figures/paired_times.csv',index=False)
    pd.DataFrame([{'name':run['name'],**run['summary']} for run in runs]).to_csv(report/'optimization_figures/run_summary.csv',index=False)
    (report/'optimization.md').write_text(build_markdown(analysis),encoding='utf-8')
    sources=[source for run in runs for source in run['sources']]+[source_record(schedule_path)]
    for path in (conflict_path,unit_path,ui_path,contract_path,preflight_path):
        if path.exists():sources.append(source_record(path))
    code_paths=('report/analyze_optimization.py','fl/runner.py','fl/artifacts.py','audit/chain.py','network/manage.py',
                'common/job_lock.py','common/processes.py','experiments/run.py','experiments/compare_audit.py','experiments/metrics.py',
                'dashboard/app.py','dashboard/data.py','report/validate_optimization_ui.py','tests/test_network_ready.py','tests/test_job_lock.py','tests/test_pipeline_audit.py')
    generated_paths=[report/'optimization.md',report/'optimization_analysis.json',report/'optimization_figures/paired_times.csv',report/'optimization_figures/run_summary.csv']
    provenance={'generated_at':datetime.now(timezone.utc).isoformat(),'selection':{'prefix':args.prefix,'seeds':SEEDS,'strategies':STRATEGIES,
                'excluded':'All historical v1, smoke, reproduction and unrelated runs'},'input_files':sources,
                'model_artifacts':[source for run in runs for source in run['artifacts']],
                'code_files':[source_record(ROOT/p) for p in code_paths],
                'generated_files':[source_record(p) for p in generated_paths]+figure_records,
                'verification_scope':'Agent-local deterministic checks; captured genuine receipts/events and saved live readbacks; no claim of human verification.'}
    (report/'optimization_provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    print(json.dumps({'validation':analysis['validation'],'paired_saved_seconds':analysis['paired_saved_seconds'],
                      'reduction_percent':analysis['reduction_of_mean_loop_time_percent'],'figures':figure_records},indent=2))


if __name__=='__main__':main()
