"""Download the official archive with resumable ranges and SHA-256 verification."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

LOCAL = Path(__file__).resolve().parent / ".local"
REVISION = "b29220c3ccceecd9188ab42f1688e4e6bf5d7a7d"
URL = f"https://huggingface.co/datasets/autodesk/neuralCAD-Edit/resolve/{REVISION}/edit_192_external.zip"
SIZE = 6029961729
SHA256 = "53c8ae0f7de19d280de48ceae770a3332cc2a3a675eafb5e1a7582ca5c527566"
ARCHIVE = LOCAL / "downloads/edit_192_external.zip"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(workers=12):
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists() and digest(ARCHIVE) == SHA256:
        print("Official archive already verified", flush=True)
        return ARCHIVE
    chunks = LOCAL / "downloads/chunks"
    chunks.mkdir(exist_ok=True)
    chunk_size = 32 * 1024 * 1024
    ranges = [(start, min(start + chunk_size, SIZE) - 1) for start in range(0, SIZE, chunk_size)]

    def fetch(pair):
        start, end = pair
        target = chunks / f"{start:012d}.part"
        expected = end - start + 1
        if target.exists() and target.stat().st_size == expected:
            return expected
        temporary = target.with_suffix(".tmp")
        subprocess.run(["curl", "-fLsS", "--retry", "5", "--retry-all-errors", "--connect-timeout", "20",
                        "--max-time", "600", "--range", f"{start}-{end}", "--max-filesize", str(expected),
                        f"{URL}?download=true&offset={start}", "-o", str(temporary)], check=True)
        if temporary.stat().st_size != expected:
            raise RuntimeError(f"Incomplete range {start}-{end}")
        temporary.replace(target)
        return expected

    completed = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(fetch, pair) for pair in ranges]):
            completed += future.result()
            elapsed = time.monotonic() - started
            print(f"Dataset: {completed / SIZE:.1%}, {completed / 1e9:.2f}/{SIZE / 1e9:.2f} GB, {completed / max(elapsed, 1) / 1e6:.2f} MB/s", flush=True)
    temporary = ARCHIVE.with_suffix(".assembling")
    with temporary.open("wb") as output:
        for start, _ in ranges:
            with (chunks / f"{start:012d}.part").open("rb") as source:
                shutil.copyfileobj(source, output, 8 * 1024 * 1024)
    if digest(temporary) != SHA256:
        raise RuntimeError("Official archive SHA-256 mismatch; range files retained for inspection")
    temporary.replace(ARCHIVE)
    print("Official archive SHA-256 verified", flush=True)
    return ARCHIVE


def extract(archive):
    target = LOCAL / "data"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            destination = (target / entry.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise RuntimeError(f"Invalid archive path: {entry.filename}")
        package.extractall(target)
    print(f"Extracted official data to {target}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()
    archive = download(args.workers)
    if args.extract:
        extract(archive)
