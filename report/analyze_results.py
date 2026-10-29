"""Rebuild the reported descriptive analysis from the six fixed main runs.

Run from the project root: python report/analyze_results.py
This script reads experiment evidence and writes report artifacts only.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from dashboard.data import list_runs, valid_pair
from experiments import plot_results

PREFIX = "full-study-20261029-"


def describe(values):
    values = [float(value) for value in values]
    return {"n": len(values), "mean": statistics.mean(values), "sample_sd": statistics.stdev(values) if len(values) > 1 else None, "min": min(values), "max": max(values)}


def main():
    runs = sorted((run for run in list_runs() if run["name"].startswith(PREFIX)), key=lambda run: (run["summary"].get("seed", 0), run["mode"]))
    assert len(runs) == 6 and all(run["status"].get("state") == "completed" for run in runs), "The fixed six completed main runs are required"
    assert all(run["summary"].get("rounds") == 10 and run["summary"].get("train_samples") == 50000 and run["summary"].get("test_samples") == 10000 for run in runs)
    report = ROOT / "report"
    metrics = ["final_accuracy", "final_loss", "total_seconds", "mean_round_seconds", "workflow_wall_seconds", "total_audit_seconds", "total_model_bytes_up", "total_model_bytes_down", "ledger_growth_bytes_all_nodes"]
    grouped = {}
    for mode in ("baseline", "blockchain"):
        members = [run for run in runs if run["mode"] == mode]
        grouped[mode] = {key: describe(run["summary"][key] for run in members) for key in metrics}
        if mode == "blockchain":
            for key in ("application_audit_tps", "application_audit_window_seconds", "mean_confirmation_seconds"):
                grouped[mode][key] = describe(run["summary"][key] for run in members)
    pairs = []
    for seed in (42, 43, 44):
        baseline = next(run for run in runs if run["mode"] == "baseline" and run["summary"]["seed"] == seed)
        blockchain = next(run for run in runs if run["mode"] == "blockchain" and run["summary"]["seed"] == seed)
        matched, reason = valid_pair(baseline, blockchain)
        assert matched, reason
        all_round_hashes_equal = [row["global_hash"] for row in baseline["rounds"]] == [row["global_hash"] for row in blockchain["rounds"]]
        assert all_round_hashes_equal
        a, b = baseline["summary"], blockchain["summary"]
        pairs.append({"seed": seed, "final_accuracy": a["final_accuracy"], "baseline_seconds": a["total_seconds"], "blockchain_seconds": b["total_seconds"], "added_seconds": b["total_seconds"] - a["total_seconds"], "ratio": b["total_seconds"] / a["total_seconds"], "initial_hash_equal": a["initial_hash"] == b["initial_hash"], "data_indices_equal": a["data_indices_sha256"] == b["data_indices_sha256"], "all_ten_global_hashes_equal": all_round_hashes_equal})
    transactions = [tx for run in runs for tx in run["transactions"]]
    assert len(transactions) == 183 and all(tx["status"] == 1 for tx in transactions)
    tx_frame = pd.DataFrame(transactions)
    tx_metrics = {operation: {"count": len(part), **describe(part["confirmation_seconds"])} for operation, part in tx_frame.groupby("operation")}
    pooled = describe(tx_frame["confirmation_seconds"])
    pooled["median"] = float(tx_frame["confirmation_seconds"].median())
    pooled["p95"] = float(tx_frame["confirmation_seconds"].quantile(.95))
    per_process, validator_sums = [], []
    for run in runs:
        frame = pd.read_csv(run["path"] / "resources.csv")
        for role, part in frame.groupby("role"):
            per_process.append({"run": run["name"], "mode": run["mode"], "seed": run["summary"]["seed"], "role": role, "samples": len(part), "mean_cpu_percent": float(part["cpu_percent"].mean()), "peak_cpu_percent": float(part["cpu_percent"].max()), "mean_rss_mib": float(part["rss_bytes"].mean() / 1048576), "peak_rss_mib": float(part["rss_bytes"].max() / 1048576)})
        validators = frame[frame["role"].str.startswith("validator_")]
        if not validators.empty:
            # Sampling writes validators in a fixed four-role sequence. One
            # early run has a 16 ms timestamp spread within a cycle; group by
            # that validated sequence rather than splitting its timestamps.
            validators = validators.reset_index(drop=True)
            validators["sample_cycle"] = validators.index // 4
            cycles = validators.groupby("sample_cycle")
            expected_roles = {f"validator_{i}" for i in range(1, 5)}
            assert all(set(part["role"]) == expected_roles and len(part) == 4 for _, part in cycles), "Incomplete validator resource sample"
            max_spread = float(cycles["elapsed_seconds"].agg(lambda times: times.max() - times.min()).max())
            assert max_spread < .05, "Validator samples within a cycle are too far apart to sum"
            sums = cycles[["cpu_percent", "rss_bytes"]].sum()
            validator_sums.append({"run": run["name"], "sampling_cycles": len(sums), "maximum_within_cycle_timestamp_spread_seconds": max_spread, "mean_cpu_percent_sum": float(sums["cpu_percent"].mean()), "peak_cpu_percent_sum": float(sums["cpu_percent"].max()), "mean_rss_sum_mib": float(sums["rss_bytes"].mean() / 1048576), "peak_rss_sum_mib": float(sums["rss_bytes"].max() / 1048576)})
    resource_groups = {}
    for mode in ("baseline", "blockchain"):
        entries = [entry for entry in per_process if entry["mode"] == mode and entry["role"] == "training"]
        resource_groups[f"{mode}_training"] = {key: describe(entry[key] for entry in entries) for key in ("mean_cpu_percent", "peak_cpu_percent", "mean_rss_mib", "peak_rss_mib")}
    resource_groups["blockchain_all_validators"] = {key: describe(entry[key] for entry in validator_sums) for key in ("mean_cpu_percent_sum", "peak_cpu_percent_sum", "mean_rss_sum_mib", "peak_rss_sum_mib")}
    source_files = []
    for run in runs:
        for name in ("config.json", "summary.json", "rounds.csv", "resources.csv", "transactions.jsonl", "verifications.jsonl"):
            source = run["path"] / name
            if source.exists():
                source_files.append({"path": str(source.relative_to(ROOT)), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    analysis = {"generated_at": datetime.now(timezone.utc).isoformat(), "run_prefix": PREFIX, "run_count": 6, "runs": [{"name": run["name"], **run["summary"]} for run in runs], "mode_statistics": grouped, "pairs": pairs, "paired_added_seconds": describe(pair["added_seconds"] for pair in pairs), "ratio_of_mean_training_loop_seconds": grouped["blockchain"]["total_seconds"]["mean"] / grouped["baseline"]["total_seconds"]["mean"], "transaction_categories": tx_metrics, "pooled_transaction_latency": pooled, "per_process_resources": per_process, "validator_resource_sums": validator_sums, "resource_statistics": resource_groups, "source_files": source_files, "statistical_scope": "Descriptive means and sample SD across three whole runs per mode. Pooled transaction latency is not an independent-run uncertainty estimate."}
    (report / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    pd.DataFrame(per_process).to_csv(report / "resource_summary.csv", index=False)
    pd.DataFrame(validator_sums).to_csv(report / "validator_resource_sums.csv", index=False)
    original = plot_results.list_runs
    try:
        plot_results.list_runs = lambda: runs
        manifest = plot_results.generate_figures(report / "figures", scope="full")
    finally:
        plot_results.list_runs = original
    manifest["selection"] = {"run_prefix": PREFIX, "required_seeds": [42, 43, 44], "required_modes": ["baseline", "blockchain"], "scope": "Only the six completed full-data ten-round runs"}
    (report / "figures" / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"run_count": 6, "figures": manifest["figures"], "paired_added_seconds": analysis["paired_added_seconds"], "ratio": analysis["ratio_of_mean_training_loop_seconds"], "resource_statistics": resource_groups, "warnings": manifest["warnings"]}, indent=2))


if __name__ == "__main__":
    main()
