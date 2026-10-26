#!/usr/bin/env python3
"""List/check experiment definitions and selected result hashes; never run APIs."""
import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def repository_path(root: Path, value: str) -> Path:
    if Path(value).is_absolute():
        raise ValueError(f"Expected repository-relative path: {value}")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside repository: {value}")
    return path


def definitions(root: Path):
    return sorted((root / "experiments").glob("exp-*/experiment.json"))


def check(root: Path) -> list[str]:
    errors = []
    seen = set()
    for folder in sorted((root / "experiments").glob("exp-*")):
        if folder.is_dir() and not (folder / "experiment.json").is_file():
            errors.append(f"{folder.relative_to(root)}: Missing experiment.json")
    for source in definitions(root):
        try:
            data = json.loads(source.read_text())
            experiment_id = data["id"]
            if not re.fullmatch(r"EXP-[0-9]{3}", experiment_id):
                raise ValueError(f"Invalid experiment ID: {experiment_id}")
            if experiment_id in seen:
                raise ValueError(f"Duplicate experiment ID: {experiment_id}")
            seen.add(experiment_id)
            if not source.parent.name.startswith(experiment_id.lower() + "-"):
                raise ValueError("Directory does not match experiment ID")
            if data["schema_version"] != 1:
                raise ValueError("Unsupported schema_version")
            if data["status"] not in {"planned", "running", "completed", "stopped"}:
                raise ValueError("Invalid experiment status")
            if not data["title"] or not (source.parent / "README.md").is_file():
                raise ValueError("Missing title/README")
            for key in ("code_paths", "result_paths"):
                for value in data[key]:
                    if not repository_path(root, value).exists():
                        raise ValueError(f"Missing {key}: {value}")
            # Raw run roots may be absent in a clean clone. Still reject escaping paths.
            for value in data["local_run_roots"]:
                repository_path(root, value)
            for value in data["result_paths"]:
                if Path(value).name != "manifest.json":
                    continue
                manifest = json.loads(repository_path(root, value).read_text())
                if manifest["experiment_id"] != experiment_id:
                    raise ValueError("Result manifest has a different experiment ID")
                for item in manifest["files"]:
                    path = repository_path(root, item["path"])
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                    if actual != item["sha256"]:
                        raise ValueError(f"SHA-256 mismatch: {item['path']}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{source.relative_to(root)}: {error}")
    if not seen:
        errors.append("No experiment definitions found")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "check"))
    args = parser.parse_args()
    errors = check(ROOT)
    if errors:
        for error in errors:
            print(error)
        return 1
    if args.command == "list":
        for source in definitions(ROOT):
            data = json.loads(source.read_text())
            print(f"{data['id']}  {data['status']:9}  {data['title']}  {source.parent.relative_to(ROOT)}")
            for path in data["local_run_roots"]:
                print(f"  local runs: {path}")
            for path in data["result_paths"]:
                print(f"  result: {path}")
    else:
        print(f"OK: {len(definitions(ROOT))} experiment definitions; selected result hashes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
