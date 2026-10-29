"""Create figures only from completed, recorded experiment runs.

Usage: python -m experiments.plot_results --scope all
Each configuration group and each dataset scope has independent figures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from dashboard.data import RESULTS, comparison_key, list_runs, run_scope, valid_pair

COLORS = {"baseline": "#0b8d83", "blockchain": "#465bc7"}
LABELS = {"baseline": "FedAvg", "blockchain": "FedAvg + blockchain audit"}


def configure_style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "axes.labelcolor": "#243746", "axes.titleweight": "semibold", "axes.grid": True, "grid.alpha": 0.16, "grid.color": "#6c8799", "figure.facecolor": "white", "savefig.facecolor": "white", "legend.frameon": False})


def save_figure(fig, output: Path, stem: str) -> list[str]:
    paths = []
    for extension in ("png", "pdf"):
        path = output / f"{stem}.{extension}"
        fig.savefig(path, dpi=240, bbox_inches="tight")
        paths.append(str(path.relative_to(output)))
    plt.close(fig)
    return paths


def learning_curves(runs, output, prefix):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.7), constrained_layout=True)
    for mode in ("baseline", "blockchain"):
        frames = []
        for run in runs:
            if run["mode"] != mode:
                continue
            frame = pd.DataFrame(run["rounds"])
            if not frame.empty and "round" in frame:
                frame["run"] = run["name"]
                frames.append(frame)
        if not frames:
            continue
        frame = pd.concat(frames, ignore_index=True)
        for ax, metric, label in zip(axes, ("accuracy", "loss"), ("Test accuracy", "Test loss")):
            if metric not in frame:
                continue
            frame[metric] = pd.to_numeric(frame[metric], errors="coerce")
            grouped = frame.groupby("round")[metric].agg(["mean", "std", "count"])
            ax.plot(grouped.index, grouped["mean"], marker="o", markersize=3, linewidth=2, color=COLORS[mode], label=f"{LABELS[mode]} (runs={len(frames)})")
            repeated = grouped["count"] > 1
            if repeated.any():
                ax.fill_between(grouped.index, grouped["mean"] - grouped["std"], grouped["mean"] + grouped["std"], where=repeated, color=COLORS[mode], alpha=0.12)
            ax.set(xlabel="Global round", ylabel=label)
            ax.legend(fontsize=8)
    axes[0].set_ylim(0, 1)
    fig.suptitle(f"Learning curves · {prefix}\nMean ± sample SD across independent runs", fontsize=11)
    return save_figure(fig, output, f"{prefix}-learning-curves")


def performance(runs, output, prefix):
    metrics = [("total_seconds", "Total run time (s)"), ("mean_round_seconds", "Mean round time (s)")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(10.5, 3.8), constrained_layout=True)
    for ax, (metric, title) in zip(axes, metrics):
        for pos, mode in enumerate(("baseline", "blockchain")):
            values = pd.to_numeric(pd.Series([r["summary"].get(metric) for r in runs if r["mode"] == mode], dtype="object"), errors="coerce").dropna().to_numpy(dtype=float)
            if not len(values):
                continue
            mean = values.mean()
            deviation = values.std(ddof=1) if len(values) > 1 else None
            ax.bar(pos, mean, width=0.52, yerr=deviation, capsize=4, color=COLORS[mode], alpha=.8)
            offsets = np.linspace(-.11, .11, len(values)) if len(values) > 1 else np.array([0])
            ax.scatter(pos + offsets, values, s=28, color="#1c3340", zorder=3)
        ax.set_xticks([0, 1], ["FedAvg", "With audit"])
        ax.set(ylabel=title, ylim=(0, None))
    fig.suptitle(f"Measured overhead · {prefix}\nPoints = independent runs; error bars = sample SD", fontsize=11)
    return save_figure(fig, output, f"{prefix}-performance")


def transaction_latency(runs, output, prefix):
    frames = []
    for run in runs:
        if run["mode"] != "blockchain":
            continue
        frame = pd.DataFrame(run["transactions"])
        if frame.empty or "latency_seconds" not in frame:
            continue
        if "status" in frame:
            frame = frame[pd.to_numeric(frame["status"], errors="coerce") == 1]
        frame["latency_seconds"] = pd.to_numeric(frame["latency_seconds"], errors="coerce")
        frame["operation"] = frame.get("operation", frame.get("type", "audit"))
        frames.append(frame)
    if not frames:
        return []
    frame = pd.concat(frames, ignore_index=True).dropna(subset=["latency_seconds"])
    if frame.empty:
        return []
    groups = [(str(op), part["latency_seconds"].to_numpy()) for op, part in frame.groupby("operation")]
    fig, ax = plt.subplots(figsize=(8.5, 4), constrained_layout=True)
    ax.boxplot([values for _, values in groups], labels=[op for op, _ in groups], showfliers=True, patch_artist=True, boxprops={"facecolor": "#c8d2f2", "edgecolor": "#465bc7"}, medianprops={"color": "#213450"})
    ax.tick_params(axis="x", labelrotation=18)
    ax.set(ylabel="Submission-to-successful-receipt delay (s)", title=f"Confirmed audit transactions · {prefix}")
    ax.text(0, -.26, "Transactions are observations within runs, not independent training replicates.", transform=ax.transAxes, fontsize=8, color="#536d7a")
    return save_figure(fig, output, f"{prefix}-transaction-latency")


def resources(runs, output, prefix):
    frames = []
    for run in runs:
        source = run["path"] / "resources.csv"
        if not source.is_file():
            continue
        frame = pd.read_csv(source)
        if "role" not in frame:
            continue
        frame["run"] = run["name"]
        frame["mode"] = run["mode"]
        frames.append(frame)
    if not frames:
        return []
    frame = pd.concat(frames, ignore_index=True)
    available = [("cpu_percent", "CPU usage (%)"), ("rss_bytes", "Resident memory (MiB)")]
    available = [(key, label) for key, label in available if key in frame]
    if not available:
        return []
    fig, axes = plt.subplots(1, len(available), figsize=(5.2 * len(available), 3.8), constrained_layout=True, squeeze=False)
    for ax, (metric, label) in zip(axes.ravel(), available):
        numeric = pd.to_numeric(frame[metric], errors="coerce")
        frame[metric] = numeric / (1024 * 1024) if metric == "rss_bytes" else numeric
        # Aggregate samples to run-level peaks before comparing runs.
        run_peaks = frame.groupby(["mode", "role", "run"])[metric].max().reset_index()
        series = list(run_peaks.groupby(["mode", "role"]))
        labels = []
        for pos, ((mode, role), group) in enumerate(series):
            values = group[metric].dropna().to_numpy()
            if not len(values):
                continue
            ax.bar(pos, values.mean(), color=COLORS.get(mode, "#78909c"), alpha=.75)
            ax.scatter(np.repeat(pos, len(values)), values, color="#1c3340", s=22, zorder=3)
            labels.append(f"{mode}\n{role}")
        ax.set_xticks(range(len(labels)), labels, fontsize=8)
        ax.set(ylabel=f"Peak {label}", ylim=(0, None))
    fig.suptitle(f"Recorded resource samples · {prefix}\nRun-level peaks; sampling may miss shorter peaks", fontsize=11)
    return save_figure(fig, output, f"{prefix}-resources")


def generate_figures(output: Path, scope: str = "all") -> dict:
    configure_style()
    output.mkdir(parents=True, exist_ok=True)
    runs = [r for r in list_runs() if r["status"].get("state") == "completed" and r["summary"] and r["rounds"]]
    runs = [r for r in runs if scope == "all" or run_scope(r) == scope]
    if not runs:
        raise ValueError("No completed runs with summary.json and rounds.csv matched the requested scope")
    sources, warnings = [], []
    for run in runs:
        warnings.extend(f"{run['name']}: {message}" for message in run["errors"])
        for name in ("config.json", "summary.json", "rounds.csv", "transactions.jsonl", "receipts.jsonl", "audit/transactions.jsonl", "resources.csv"):
            path = run["path"] / name
            if path.is_file():
                sources.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    groups = {}
    for run in runs:
        groups.setdefault(f"{run_scope(run)}-{comparison_key(run)}", []).append(run)
    artifacts, table, paired_rows = [], [], []
    for prefix, members in sorted(groups.items()):
        artifacts += learning_curves(members, output, prefix)
        artifacts += performance(members, output, prefix)
        artifacts += transaction_latency(members, output, prefix)
        artifacts += resources(members, output, prefix)
        for run in members:
            summary = run["summary"]
            table.append({"configuration_group": prefix, "run": run["name"], "mode": run["mode"], "seed": summary.get("seed", run["config"].get("seed")), "final_accuracy": summary.get("final_accuracy"), "final_loss": summary.get("final_loss"), "total_seconds": summary.get("total_seconds"), "mean_round_seconds": summary.get("mean_round_seconds"), "train_samples": summary.get("train_samples"), "test_samples": summary.get("test_samples"), "deployment": summary.get("deployment")})
        frame = pd.DataFrame([row for row in table if row["configuration_group"] == prefix])
        if frame.groupby(["seed", "mode"]).size().gt(1).any():
            warnings.append(f"{prefix}: duplicate seed/mode runs; paired differences omitted rather than choosing a run")
        else:
            pivot = frame.pivot(index="seed", columns="mode", values="total_seconds")
            if {"baseline", "blockchain"}.issubset(pivot.columns):
                for seed, values in pivot.dropna().iterrows():
                    pair = [r for r in members if r["summary"].get("seed", r["config"].get("seed")) == seed]
                    ok, reason = valid_pair(pair[0], pair[1])
                    if not ok:
                        warnings.append(f"{prefix}, seed {seed}: paired difference omitted ({reason})")
                        continue
                    paired_rows.append({"configuration_group": prefix, "seed": seed, "baseline_seconds": values["baseline"], "blockchain_seconds": values["blockchain"], "added_seconds": values["blockchain"] - values["baseline"]})
    pd.DataFrame(table).to_csv(output / "summary_table.csv", index=False)
    pd.DataFrame(paired_rows, columns=["configuration_group", "seed", "baseline_seconds", "blockchain_seconds", "added_seconds"]).to_csv(output / "paired_differences.csv", index=False)
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": scope, "run_count": len(runs), "configuration_groups": list(groups), "figures": artifacts, "sources": sources, "warnings": warnings, "statistics": "Means and sample standard deviations across complete runs; no significance test"}
    (output / "source_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["all", "full", "subset"], default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "report" / "figures")
    args = parser.parse_args()
    manifest = generate_figures(args.output.resolve(), args.scope)
    print(json.dumps({"run_count": manifest["run_count"], "figures": manifest["figures"], "warnings": manifest["warnings"]}, indent=2))


if __name__ == "__main__":
    main()
