"""Four logical Flower NumPyClients and a synchronous FedAvg coordinator.

The clients execute sequentially in one process and share the device. Flower's
real FedAvg strategy aggregates real local SGD updates. No gRPC/network transfer
is claimed: transfer counters measure serialized NPZ parameter payload sizes.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import statistics
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

# Set before importing torch or initializing a CUDA context.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
os.environ.setdefault("FLWR_TELEMETRY_ENABLED", "0")

import flwr as fl
import numpy as np
import torch
from flwr.common import Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays
from flwr.server.strategy import FedAvg
from torch import nn
from torch.utils.data import DataLoader

from .artifacts import HASH_ALGORITHM, hash_parameters, load_parameters, save_parameters, serialized_parameters
from .data import load_data
from .model import SmallCNN
from common.job_lock import job_lock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = {
    "seed": 42, "partition_seed": 2026, "clients": 4, "rounds": 10,
    "local_epochs": 1, "batch_size": 128, "learning_rate": 0.02, "momentum": 0.9,
    "device": "auto", "cpu_threads": 2, "data_dir": str(PROJECT_ROOT / "data"),
    "train_samples": None, "test_samples": None, "download": True,
    "audit_strategy": "sync",
}
ROUND_FIELDS = [
    "round", "accuracy", "loss", "round_seconds", "train_seconds", "audit_seconds",
    "model_bytes_up", "model_bytes_down", "aggregate_seconds", "evaluate_seconds", "global_hash",
]


class AuditHook(Protocol):
    def begin_round(self, round_id: int): ...
    def submit(self, client_id: int, round_id: int, digest: str): ...
    def submit_async(self, client_id: int, round_id: int, digest: str) -> Any: ...
    def confirm(self, ticket: Any): ...
    def verify(self, client_id: int, round_id: int, digest: str) -> bool: ...
    def commit(self, round_id: int, digest: str): ...


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict):
    # Atomic replacement prevents the UI seeing a half-written JSON document.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def _seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _sync(device: torch.device):
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def model_parameters(model: nn.Module) -> dict[str, np.ndarray]:
    return {key: value.detach().cpu().numpy().copy() for key, value in model.state_dict().items()}


def set_model_parameters(model: nn.Module, parameters: dict[str, np.ndarray]):
    expected = model.state_dict()
    if set(expected) != set(parameters):
        raise ValueError("Model parameter names do not match")
    state = OrderedDict()
    for key, tensor in expected.items():
        array = parameters[key]
        if tuple(tensor.shape) != tuple(array.shape):
            raise ValueError(f"Shape mismatch for {key}")
        state[key] = torch.from_numpy(array.copy()).to(dtype=tensor.dtype)
    model.load_state_dict(state, strict=True)


class LogicalClient(fl.client.NumPyClient):
    def __init__(self, client_id: int, dataset, config: dict, device: torch.device):
        self.client_id, self.dataset, self.config, self.device = client_id, dataset, config, device
        self.names = sorted(SmallCNN().state_dict())

    def fit(self, parameters, config):
        # The round/client-derived seed is independent of ledger latency/order.
        local_seed = self.config["seed"] + int(config["round"]) * 1000 + self.client_id
        _seed(local_seed)
        model = SmallCNN().to(self.device)
        set_model_parameters(model, dict(zip(self.names, parameters, strict=True)))
        model.train()
        generator = torch.Generator().manual_seed(local_seed)
        loader = DataLoader(self.dataset, batch_size=self.config["batch_size"], shuffle=True,
                            generator=generator, num_workers=0)
        optimizer = torch.optim.SGD(model.parameters(), lr=self.config["learning_rate"],
                                    momentum=self.config["momentum"])
        criterion = nn.CrossEntropyLoss()
        total_loss, examples = 0.0, 0
        _sync(self.device)
        started = time.perf_counter()
        for _ in range(self.config["local_epochs"]):
            for images, labels in loader:
                images = images.to(self.device, dtype=torch.float32).div_(127.5).sub_(1.0)
                labels = labels.to(self.device)
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(model(images), labels)
                loss.backward()
                optimizer.step()
                batch = labels.size(0)
                total_loss += loss.detach().item() * batch
                examples += batch
        _sync(self.device)
        train_seconds = time.perf_counter() - started
        result = model_parameters(model)
        del model, optimizer
        return [result[key] for key in self.names], len(self.dataset), {
            "train_loss": total_loss / examples, "train_seconds": train_seconds,
        }


def evaluate(parameters: dict[str, np.ndarray], dataset, config: dict, device: torch.device) -> tuple[float, float]:
    model = SmallCNN().to(device)
    set_model_parameters(model, parameters)
    model.eval()
    criterion = nn.CrossEntropyLoss(reduction="sum")
    loss_sum, correct, count = 0.0, 0, 0
    loader = DataLoader(dataset, batch_size=config["batch_size"], shuffle=False, num_workers=0)
    with torch.inference_mode():
        for images, labels in loader:
            images = images.to(device, dtype=torch.float32).div_(127.5).sub_(1.0)
            labels = labels.to(device)
            output = model(images)
            loss_sum += criterion(output, labels).item()
            correct += int((output.argmax(1) == labels).sum().item())
            count += labels.size(0)
    _sync(device)
    del model
    return loss_sum / count, correct / count


def aggregate_updates(updates: list[tuple[dict[str, np.ndarray], int]], round_id: int = 1) -> dict[str, np.ndarray]:
    """Use Flower's actual sample-count-weighted FedAvg, without in-place mutation."""
    names = sorted(updates[0][0])
    results = []
    for client_id, (parameters, samples) in enumerate(updates):
        fit = FitRes(status=Status(code=Code.OK, message=""),
                     parameters=ndarrays_to_parameters([parameters[key] for key in names]),
                     num_examples=int(samples), metrics={})
        # FedAvg does not read ClientProxy for aggregation; retain the logical
        # client identifier rather than instantiate a nonexistent network proxy.
        results.append((client_id, fit))
    strategy = FedAvg(inplace=False, fit_metrics_aggregation_fn=lambda _: {})
    aggregated, _ = strategy.aggregate_fit(round_id, results, failures=[])
    if aggregated is None:
        raise RuntimeError("Flower returned no aggregated parameters")
    return dict(zip(names, parameters_to_ndarrays(aggregated), strict=True))


def _validate_config(config: dict) -> dict:
    result = {**DEFAULT_CONFIG, **config}
    for key in ("clients", "rounds", "local_epochs", "batch_size", "cpu_threads"):
        result[key] = int(result[key])
        if result[key] < 1:
            raise ValueError(f"{key} must be positive")
    if result["clients"] != 4:
        raise ValueError("This demonstrator requires exactly four logical clients")
    if result["audit_strategy"] not in ("sync", "pipelined"):
        raise ValueError("audit_strategy must be sync or pipelined")
    for key in ("train_samples", "test_samples"):
        if result[key] is not None:
            result[key] = int(result[key])
            if result[key] < 1:
                raise ValueError(f"{key} must be positive or null")
    result["seed"], result["partition_seed"] = int(result["seed"]), int(result["partition_seed"])
    if not 0 < float(result["learning_rate"]) or not 0 <= float(result["momentum"]) < 1:
        raise ValueError("learning_rate must be positive and momentum in [0,1)")
    data_path = Path(result["data_dir"])
    result["data_dir"] = str(data_path if data_path.is_absolute() else PROJECT_ROOT / data_path)
    return result


@job_lock('training')
def run_training(config: dict, output_dir: str | Path, audit: AuditHook | None = None) -> dict:
    """Run real SGD/FedAvg, writing evidence incrementally; refuse to overwrite runs."""
    config = _validate_config(config)
    pipeline = audit is not None and config["audit_strategy"] == "pipelined"
    if pipeline and not all(callable(getattr(audit, name, None)) for name in ("submit_async", "confirm")):
        raise TypeError("Pipelined audit requires submit_async and confirm methods")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "config.json").exists() or (output_dir / "rounds.csv").exists():
        raise FileExistsError(f"Run already exists: {output_dir}")
    mode = "blockchain" if audit is not None else "baseline"
    config.update({"mode": mode, "hash_algorithm": HASH_ALGORITHM,
                   "effective_audit_strategy": config["audit_strategy"] if audit is not None else "disabled",
                   "deployment": "in_process_logical_clients",
                   "transfer_metric": "uncompressed_npz_parameter_payload_bytes_no_network_protocol_overhead",
                   "started_at": _utc(), "versions": {"torch": torch.__version__, "flower": fl.__version__,
                   "numpy": np.__version__, "python": sys.version.split()[0]}})
    _write_json(output_dir / "config.json", config)
    logger = logging.getLogger(f"blockfl.training.{output_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(output_dir / "training.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(console)
    status = {"state": "preparing", "mode": mode, "round": 0, "total_rounds": config["rounds"],
              "seed": config["seed"], "clients": [{"client_id": i, "state": "waiting"} for i in range(4)]}

    def write_status():
        status["updated_at"] = _utc()
        _write_json(output_dir / "status.json", status)

    write_status()
    try:
        torch.set_num_threads(config["cpu_threads"])
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        requested = config["device"]
        device = torch.device("cuda" if requested == "auto" and torch.cuda.is_available() else
                              "cpu" if requested == "auto" else requested)
        if device.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but not available; use device=cpu explicitly")
        config["resolved_device"] = str(device)
        logger.info("Preparing CIFAR-10 (%s), four logical clients, device=%s", mode, device)
        prepare_start = time.perf_counter()
        train_sets, test_set, data_hash = load_data(config, output_dir)
        config["actual_train_samples"] = sum(map(len, train_sets))
        config["actual_test_samples"] = len(test_set)
        config["data_indices_sha256"] = data_hash
        data_source_path = Path(config["data_dir"]) / "cifar10-source.json"
        config["dataset_source"] = json.loads(data_source_path.read_text(encoding="utf-8")) if data_source_path.exists() else {
            "loader": "torchvision.datasets.CIFAR10", "integrity": "official batch MD5 checks",
        }
        config["data_prepare_seconds"] = time.perf_counter() - prepare_start
        _seed(config["seed"])
        global_parameters = model_parameters(SmallCNN())
        names = sorted(global_parameters)
        initial_hash = save_parameters(output_dir / "global_000.npz", global_parameters)
        clients = [LogicalClient(i, dataset, config, device) for i, dataset in enumerate(train_sets)]
        # One actual device operation validates the selected runtime and warms up
        # CUDA before the common A/B training measurement boundary.
        warm = torch.ones((1,), device=device)
        _ = warm + warm
        _sync(device)
        del warm
        _write_json(output_dir / "config.json", config)
        logger.info("Data ready: train=%s test=%s partition_hash=%s initial_hash=%s",
                    config["actual_train_samples"], len(test_set), data_hash, initial_hash)
        rows = []
        total_start = time.perf_counter()
        with (output_dir / "rounds.csv").open("w", newline="", encoding="utf-8") as rounds_file, \
             (output_dir / "clients.csv").open("w", newline="", encoding="utf-8") as clients_file, \
             (output_dir / "manifest.jsonl").open("w", encoding="utf-8") as manifest:
            rounds_csv = csv.DictWriter(rounds_file, fieldnames=ROUND_FIELDS)
            rounds_csv.writeheader()
            client_fields = ["round", "client_id", "num_examples", "train_loss", "train_seconds", "hash", "path", "payload_bytes"]
            clients_csv = csv.DictWriter(clients_file, fieldnames=client_fields)
            clients_csv.writeheader()
            manifest.write(json.dumps({"kind": "global_model", "round": 0, "path": "global_000.npz", "hash": initial_hash}) + "\n")
            manifest.flush()
            for round_id in range(1, config["rounds"] + 1):
                round_start = time.perf_counter()
                status.update({"state": "running", "round": round_id})
                for client_status in status["clients"]:
                    client_status["state"] = "waiting"
                write_status()
                audit_seconds = 0.0
                if audit is not None:
                    started = time.perf_counter()
                    audit.begin_round(round_id)
                    audit_seconds += time.perf_counter() - started
                updates, train_seconds, up_bytes = [], 0.0, 0
                pending_updates = []
                down_bytes = len(serialized_parameters(global_parameters)) * len(clients)

                def accept_update(item):
                    """Reload the artifact after receipt confirmation, then verify it."""
                    nonlocal audit_seconds
                    client_id = item["client_id"]
                    loaded = load_parameters(output_dir / item["path"])
                    actual_hash = hash_parameters(loaded)
                    if actual_hash != item["hash"]:
                        raise RuntimeError(f"Artifact changed before aggregation: {item['path']}")
                    if audit is not None:
                        started = time.perf_counter()
                        committed = audit.verify(client_id, round_id, actual_hash)
                        audit_seconds += time.perf_counter() - started
                        if not committed:
                            raise RuntimeError(f"Chain commitment mismatch for round {round_id}, client {client_id}")
                    updates.append((loaded, item["num_examples"]))
                    clients_csv.writerow({key: value for key, value in item.items() if key != "ticket"})
                    clients_file.flush()
                    manifest.write(json.dumps({"kind": "client_update", "round": round_id, "client_id": client_id,
                                               "path": item["path"], "hash": item["hash"]}) + "\n")
                    manifest.flush()
                    status["clients"][client_id]["state"] = "verified"
                    write_status()
                    logger.info("Round %s/%s client %s: samples=%s loss=%.5f training=%.3fs hash=%s",
                                round_id, config["rounds"], client_id, item["num_examples"],
                                item["train_loss"], item["train_seconds"], item["hash"])

                for client_id, client in enumerate(clients):
                    status["clients"][client_id]["state"] = "training"
                    write_status()
                    arrays, samples, metrics = client.fit([global_parameters[key].copy() for key in names], {"round": round_id})
                    update = dict(zip(names, arrays, strict=True))
                    relative = f"updates/round_{round_id:03d}_client_{client_id}.npz"
                    artifact = output_dir / relative
                    digest = save_parameters(artifact, update)
                    payload_bytes = artifact.stat().st_size
                    up_bytes += payload_bytes
                    train_seconds += metrics["train_seconds"]
                    status["clients"][client_id]["state"] = "committing" if audit else "verifying"
                    write_status()
                    item = {"round": round_id, "client_id": client_id, "num_examples": samples,
                            **metrics, "hash": digest, "path": relative, "payload_bytes": payload_bytes}
                    if audit is not None:
                        started = time.perf_counter()
                        if pipeline:
                            item["ticket"] = audit.submit_async(client_id, round_id, digest)
                        else:
                            audit.submit(client_id, round_id, digest)
                        audit_seconds += time.perf_counter() - started
                    if pipeline:
                        pending_updates.append(item)
                        status["clients"][client_id].update({"state": "pending_confirmation", "hash": digest, "path": relative})
                        if isinstance(item["ticket"], dict) and "transaction_hash" in item["ticket"]:
                            status["clients"][client_id]["transaction_hash"] = item["ticket"]["transaction_hash"]
                        write_status()
                    else:
                        accept_update(item)
                if pipeline:
                    # All four identities have sent their updates. No FedAvg or
                    # aggregate commitment is allowed until every receipt and
                    # every independently reloaded artifact has been verified.
                    for item in pending_updates:
                        started = time.perf_counter()
                        audit.confirm(item["ticket"])
                        audit_seconds += time.perf_counter() - started
                        status["clients"][item["client_id"]]["state"] = "confirmed"
                        write_status()
                    for item in pending_updates:
                        accept_update(item)
                started = time.perf_counter()
                global_parameters = aggregate_updates(updates, round_id)
                aggregate_seconds = time.perf_counter() - started
                global_relative = f"global_{round_id:03d}.npz"
                global_hash = save_parameters(output_dir / global_relative, global_parameters)
                if audit is not None:
                    started = time.perf_counter()
                    audit.commit(round_id, global_hash)
                    audit_seconds += time.perf_counter() - started
                started = time.perf_counter()
                loss, accuracy = evaluate(global_parameters, test_set, config, device)
                evaluate_seconds = time.perf_counter() - started
                row = {"round": round_id, "accuracy": accuracy, "loss": loss,
                       "round_seconds": time.perf_counter() - round_start, "train_seconds": train_seconds,
                       "audit_seconds": audit_seconds, "model_bytes_up": up_bytes, "model_bytes_down": down_bytes,
                       "aggregate_seconds": aggregate_seconds, "evaluate_seconds": evaluate_seconds, "global_hash": global_hash}
                rows.append(row)
                rounds_csv.writerow(row)
                rounds_file.flush()
                manifest.write(json.dumps({"kind": "global_model", "round": round_id, "path": global_relative, "hash": global_hash}) + "\n")
                manifest.flush()
                status["accuracy"], status["loss"] = accuracy, loss
                write_status()
                logger.info("Round %s complete: accuracy=%.4f loss=%.5f total=%.3fs audit=%.3fs",
                            round_id, accuracy, loss, row["round_seconds"], audit_seconds)
        summary = {"mode": mode, "seed": config["seed"], "rounds": config["rounds"],
                   "audit_strategy": config["audit_strategy"], "effective_audit_strategy": config["effective_audit_strategy"],
                   "client_count": len(clients), "train_samples": config["actual_train_samples"], "test_samples": len(test_set),
                   "final_accuracy": rows[-1]["accuracy"], "final_loss": rows[-1]["loss"],
                   "total_seconds": time.perf_counter() - total_start,
                   "mean_round_seconds": statistics.mean(row["round_seconds"] for row in rows),
                   "total_audit_seconds": sum(row["audit_seconds"] for row in rows),
                   "total_model_bytes_up": sum(row["model_bytes_up"] for row in rows),
                   "total_model_bytes_down": sum(row["model_bytes_down"] for row in rows),
                   "initial_hash": initial_hash, "final_hash": rows[-1]["global_hash"],
                   "data_indices_sha256": data_hash, "resolved_device": str(device),
                   "deployment": config["deployment"], "transfer_metric": config["transfer_metric"],
                   "finished_at": _utc()}
        _write_json(output_dir / "summary.json", summary)
        status["state"] = "completed"
        write_status()
        logger.info("Completed: %s", json.dumps(summary))
        return summary
    except BaseException as exc:
        status.update({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
        write_status()
        logger.exception("Training failed")
        raise
    finally:
        for attached in (handler, console):
            logger.removeHandler(attached)
            attached.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--audit-strategy", choices=("sync", "pipelined"))
    arguments = parser.parse_args()
    config = json.loads(arguments.config.read_text(encoding="utf-8")) if arguments.config else {}
    for key in ("seed", "rounds", "audit_strategy"):
        if getattr(arguments, key) is not None:
            config[key] = getattr(arguments, key)
    run_training(config, arguments.output)


if __name__ == "__main__":
    main()
