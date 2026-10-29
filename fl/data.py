"""CIFAR-10 partitions, with explicitly recorded sample indices."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset, Subset
from torchvision.datasets import CIFAR10


class CIFARTensors(Dataset):
    """Keep host images uint8; normalization happens per batch on the device."""

    def __init__(self, dataset: CIFAR10):
        self.images = torch.from_numpy(dataset.data).permute(0, 3, 1, 2).contiguous()
        self.targets = torch.tensor(dataset.targets, dtype=torch.long)

    def __len__(self) -> int:
        return len(self.targets)

    def __getitem__(self, index: int):
        return self.images[index], self.targets[index]


def partition_indices(total: int, clients: int, seed: int, sample_limit: int | None = None) -> list[np.ndarray]:
    if clients < 1 or total < clients:
        raise ValueError("Each client must receive at least one example")
    count = total if sample_limit is None else min(total, int(sample_limit))
    if count < clients:
        raise ValueError("train_samples must be at least the client count")
    indices = np.random.default_rng(seed).permutation(total)[:count]
    return list(np.array_split(indices, clients))


def load_data(config: dict, output_dir: Path):
    data_dir = Path(config["data_dir"])
    download = bool(config.get("download", True))
    train_raw = CIFAR10(str(data_dir), train=True, download=download)
    test_raw = CIFAR10(str(data_dir), train=False, download=download)
    train = CIFARTensors(train_raw)
    test = CIFARTensors(test_raw)
    # A distinct fixed split seed controls ownership. Training seed does not
    # accidentally change the organizations' datasets across replications.
    partitions = partition_indices(len(train), config["clients"], config["partition_seed"], config["train_samples"])
    test_count = len(test) if config["test_samples"] is None else min(len(test), config["test_samples"])
    if test_count < 1:
        raise ValueError("test_samples must be positive")
    test_indices = np.random.default_rng(config["partition_seed"] + 1).permutation(len(test))[:test_count]
    index_record = {
        "dataset": "CIFAR-10", "partition": "iid_random_without_replacement",
        "partition_seed": config["partition_seed"],
        "clients": [indices.tolist() for indices in partitions],
        "test_indices": test_indices.tolist(),
        "training_source_size": len(train), "test_source_size": len(test),
        "torchvision_integrity_check": "CIFAR10 class verifies official batch MD5 values",
    }
    encoded = json.dumps(index_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (output_dir / "data_indices.json").write_bytes(encoded)
    index_hash = hashlib.sha256(encoded).hexdigest()
    return [Subset(train, indices.tolist()) for indices in partitions], Subset(test, test_indices.tolist()), index_hash
