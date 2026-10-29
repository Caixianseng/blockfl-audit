"""Deterministic model commitments, independent of ZIP timestamps.

Commitment format ``fl-weights-sha256-v1`` hashes a domain separator followed by
length-prefixed UTF-8 parameter names, canonical dtype/shape metadata and little
endian C-contiguous tensor bytes, in sorted parameter-name order. SHA-256 of an
NPZ container is a separate file checksum; it is not this model commitment.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np

HASH_ALGORITHM = "fl-weights-sha256-v1"


def _canonical_array(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.hasobject or array.dtype.kind not in "biufc":
        raise ValueError(f"Unsupported model parameter dtype: {array.dtype}")
    if not np.isfinite(array).all():
        raise ValueError("Model parameters must contain only finite values")
    return np.ascontiguousarray(array.astype(array.dtype.newbyteorder("<"), copy=False))


def hash_parameters(parameters: Mapping[str, np.ndarray]) -> str:
    if not parameters:
        raise ValueError("Cannot commit an empty parameter dictionary")
    digest = hashlib.sha256((HASH_ALGORITHM + "\0").encode("ascii"))
    for name in sorted(parameters):
        if not isinstance(name, str) or not name:
            raise ValueError("Parameter names must be non-empty strings")
        array = _canonical_array(parameters[name])
        metadata = json.dumps(
            {"name": name, "dtype": array.dtype.str, "shape": list(array.shape)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        raw = array.tobytes(order="C")
        digest.update(struct.pack("<Q", len(metadata)))
        digest.update(metadata)
        digest.update(struct.pack("<Q", len(raw)))
        digest.update(raw)
    return digest.hexdigest()


def serialized_parameters(parameters: Mapping[str, np.ndarray]) -> bytes:
    """Return the uncompressed NPZ application payload used for byte accounting."""
    buffer = io.BytesIO()
    np.savez(buffer, **{key: _canonical_array(parameters[key]) for key in sorted(parameters)})
    return buffer.getvalue()


def save_parameters(path: str | Path, parameters: Mapping[str, np.ndarray]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    commitment = hash_parameters(parameters)
    path.write_bytes(serialized_parameters(parameters))
    return commitment


def load_parameters(path: str | Path) -> dict[str, np.ndarray]:
    with np.load(Path(path), allow_pickle=False) as saved:
        parameters = {key: saved[key].copy() for key in saved.files}
    # This also rejects object/unsupported dtypes and non-finite parameters.
    hash_parameters(parameters)
    return parameters


def verify_artifact(path: str | Path, expected_hash: str) -> bool:
    """A malformed or changed artifact fails verification without pickle loading."""
    try:
        return hash_parameters(load_parameters(path)) == expected_hash.removeprefix("0x").lower()
    except (OSError, ValueError, TypeError, KeyError, EOFError, zipfile.BadZipFile):
        return False
