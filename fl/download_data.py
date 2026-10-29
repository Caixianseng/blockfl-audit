"""Download the official CIFAR-10 archive with bounded HTTP-range concurrency.

Every segment must match the requested Content-Range, and the complete archive
must match torchvision's upstream MD5 before extraction. This is only a faster
transport for the same dataset, not a replacement dataset.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import tarfile
import urllib.request

URL = "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz"
SOURCES = {
    "official": URL,
    # Listed by PaddlePaddle's own dataset implementation, with the same MD5:
    # https://github.com/PaddlePaddle/Paddle/blob/develop/python/paddle/dataset/cifar.py
    "paddle": "https://dataset.bj.bcebos.com/cifar/cifar-10-python.tar.gz",
}
SIZE = 170498071
MD5 = "c58f30108f718f92721af3b95e74349a"


def download(data_dir: Path, workers: int = 8, source: str = "official"):
    source_name = source
    data_dir.mkdir(parents=True, exist_ok=True)
    parts = data_dir / ".cifar10-range-parts"
    parts.mkdir(exist_ok=True)
    url = SOURCES[source]
    chunk = 4 * 1024 * 1024
    count = math.ceil(SIZE / chunk)

    def fetch(index):
        start, stop = index * chunk, min(SIZE, (index + 1) * chunk) - 1
        path = parts / f"{index:03d}.bin"
        expected = stop - start + 1
        if path.exists() and path.stat().st_size == expected:
            return index, expected
        partial = path.with_suffix(".partial")
        request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{stop}", "User-Agent": "blockfl-audit/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{stop}/{SIZE}":
                raise RuntimeError("Server did not honor the exact requested CIFAR-10 byte range")
            with partial.open("wb") as target:
                while block := response.read(64 * 1024):
                    target.write(block)
        if partial.stat().st_size != expected:
            raise RuntimeError(f"Incomplete CIFAR-10 segment {index}")
        partial.replace(path)
        return index, expected

    complete = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        for future in as_completed([pool.submit(fetch, index) for index in range(count)]):
            index, received = future.result()
            complete += received
            print(f"CIFAR-10 segment {index + 1}/{count}: {complete / SIZE:.1%} complete", flush=True)
    archive = data_dir / "cifar-10-python.verified.tar.gz"
    checksum = hashlib.md5(usedforsecurity=False)
    with archive.open("wb") as output:
        for index in range(count):
            with (parts / f"{index:03d}.bin").open("rb") as source:
                while block := source.read(1024 * 1024):
                    checksum.update(block)
                    output.write(block)
    if checksum.hexdigest() != MD5:
        raise RuntimeError("Assembled CIFAR-10 archive failed the upstream MD5 check; extraction refused")
    print("Official CIFAR-10 archive MD5 verified; extracting", flush=True)
    (data_dir / "cifar10-source.json").write_text(json.dumps({
        "dataset": "CIFAR-10 original Python archive", "download_source": url,
        "original_source": URL, "archive_bytes": SIZE, "verified_archive_md5": MD5,
        "mirror_reference": "https://github.com/PaddlePaddle/Paddle/blob/develop/python/paddle/dataset/cifar.py" if source_name == "paddle" else None,
    }, indent=2), encoding="utf-8")
    with tarfile.open(archive, "r:gz") as source:
        source.extractall(data_dir, filter="data")
    # torchvision also verifies individual official batch hashes on load.
    from torchvision.datasets import CIFAR10
    train = CIFAR10(str(data_dir), train=True, download=False)
    test = CIFAR10(str(data_dir), train=False, download=False)
    print(f"CIFAR-10 verified: {len(train)} training / {len(test)} test images", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--source", choices=sorted(SOURCES), default="official")
    args = parser.parse_args()
    download(args.data_dir, args.workers, args.source)
