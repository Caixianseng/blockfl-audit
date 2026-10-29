import numpy as np
import pytest
import torch
from torch.utils.data import TensorDataset

from fl.artifacts import hash_parameters, load_parameters, save_parameters, verify_artifact
from fl.data import partition_indices
from fl.runner import aggregate_updates, run_training


def test_commitment_tracks_names_shapes_dtype_and_values():
    original = {"weight": np.array([[1, 2]], dtype=np.float32), "bias": np.array([0], dtype=np.float32)}
    digest = hash_parameters(original)
    assert hash_parameters(dict(reversed(list(original.items())))) == digest
    assert hash_parameters({"weight": original["weight"].astype(">f4"), "bias": original["bias"]}) == digest
    for altered in (
        {**original, "weight": original["weight"].reshape(2, 1)},
        {**original, "weight": original["weight"].astype(np.float64)},
        {**original, "weight": np.array([[1, 3]], dtype=np.float32)},
        {"other": original["weight"], "bias": original["bias"]},
    ):
        assert hash_parameters(altered) != digest


def test_artifact_round_trip_and_tamper(tmp_path):
    path = tmp_path / "weights.npz"
    parameters = {"weight": np.arange(6, dtype=np.float32).reshape(2, 3)}
    expected = save_parameters(path, parameters)
    assert verify_artifact(path, expected)
    changed = load_parameters(path)
    changed["weight"][0, 0] += 1
    save_parameters(path, changed)
    assert not verify_artifact(path, expected)
    path.write_bytes(b"invalid archive")
    assert not verify_artifact(path, expected)


def test_partitions_are_fixed_disjoint_and_complete():
    parts = partition_indices(50000, 4, 2026)
    assert [len(part) for part in parts] == [12500] * 4
    assert len(np.unique(np.concatenate(parts))) == 50000
    repeated = partition_indices(50000, 4, 2026)
    assert all(np.array_equal(a, b) for a, b in zip(parts, repeated))


def test_flower_aggregation_uses_sample_weights_without_mutation():
    first = {"weight": np.array([1, 3], dtype=np.float32)}
    second = {"weight": np.array([5, 7], dtype=np.float32)}
    actual = aggregate_updates([(first, 1), (second, 3)])
    np.testing.assert_allclose(actual["weight"], [4, 6])
    np.testing.assert_array_equal(first["weight"], [1, 3])
    np.testing.assert_array_equal(second["weight"], [5, 7])


def _fixture_data(config, output_dir):
    """Small synthetic unit-test tensors; never used as experiment evidence."""
    generator = torch.Generator().manual_seed(99)
    images = torch.randint(0, 256, (32, 3, 32, 32), dtype=torch.uint8, generator=generator)
    labels = torch.arange(32) % 10
    clients = [TensorDataset(images[i * 8:(i + 1) * 8], labels[i * 8:(i + 1) * 8]) for i in range(4)]
    return clients, TensorDataset(images[:8], labels[:8]), "synthetic-unit-test-only"


class _TrackingAudit:
    def __init__(self, reject=False):
        self.commits = {}
        self.updates = {}
        self.reject = reject

    def begin_round(self, round_id):
        assert round_id == len(self.commits) + 1

    def submit(self, client_id, round_id, digest):
        assert (round_id, client_id) not in self.updates
        self.updates[round_id, client_id] = digest

    def verify(self, client_id, round_id, digest):
        return not self.reject and self.updates.get((round_id, client_id)) == digest

    def commit(self, round_id, digest):
        assert all((round_id, i) in self.updates for i in range(4))
        self.commits[round_id] = digest


def test_pair_has_identical_weights_with_audit_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr("fl.runner.load_data", _fixture_data)
    config = {"rounds": 2, "batch_size": 8, "device": "cpu", "download": False}
    baseline = run_training(config, tmp_path / "baseline")
    audit = _TrackingAudit()
    blockchain = run_training(config, tmp_path / "blockchain", audit)
    assert baseline["initial_hash"] == blockchain["initial_hash"]
    assert baseline["final_hash"] == blockchain["final_hash"]
    assert baseline["final_accuracy"] == blockchain["final_accuracy"]
    assert len(audit.commits) == 2


def test_rejected_chain_commitment_stops_aggregation(monkeypatch, tmp_path):
    monkeypatch.setattr("fl.runner.load_data", _fixture_data)
    audit = _TrackingAudit(reject=True)
    output = tmp_path / "rejected"
    with pytest.raises(RuntimeError, match="Chain commitment mismatch"):
        run_training({"rounds": 1, "batch_size": 8, "device": "cpu"}, output, audit)
    assert not audit.commits
    assert not (output / "global_001.npz").exists()
    assert not (output / "summary.json").exists()
