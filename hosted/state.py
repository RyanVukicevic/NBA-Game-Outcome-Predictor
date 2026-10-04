"""Durable R2 state and reproducible runtime-bundle helpers."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StateStore:
    def __init__(self, client, bucket: str, prefix: str = "courtside"):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    @classmethod
    def from_env(cls):
        import boto3
        required = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing object-store configuration: " + ", ".join(missing))
        endpoint = f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
        client = boto3.client("s3", endpoint_url=endpoint,
                              aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                              aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                              region_name="auto")
        return cls(client, os.environ["R2_BUCKET"], os.environ.get("R2_PREFIX", "courtside"))

    def key(self, name: str) -> str:
        return f"{self.prefix}/{name.lstrip('/')}"

    def get(self, name: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.key(name))["Body"].read()

    def put(self, name: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.key(name), Body=data,
                               ContentType=content_type,
                               Metadata={"sha256": sha256(data)})

    def restore_ledger(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = gzip.decompress(self.get("state/predictions.sqlite3.gz"))
        destination.write_bytes(data)
        db = sqlite3.connect(destination)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Restored ledger failed its integrity check.")
        finally:
            db.close()

    def save_ledger(self, source: Path) -> None:
        db = sqlite3.connect(source)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Ledger failed its integrity check before upload.")
        finally:
            db.close()
        compressed = gzip.compress(source.read_bytes(), compresslevel=6, mtime=0)
        self.put("state/predictions.sqlite3.gz", compressed, "application/gzip")

    def restore_runtime(self, root: Path) -> dict:
        payload = self.get("state/runtime.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not member.isfile():
                    raise RuntimeError("Runtime bundle contains an unsafe member.")
            archive.extractall(root, members=members, filter="data")
        manifest_path = root / "runtime-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = root / item["path"]
            if not path.is_file() or sha256(path.read_bytes()) != item["sha256"]:
                raise RuntimeError(f"Runtime bundle verification failed for {item['path']}.")
        return manifest


def build_runtime_bundle(root: Path, model_path: Path) -> bytes:
    """Package the pinned model and cached NBA history, never secrets or the ledger."""
    root, model_path = root.resolve(), model_path.resolve()
    paths = [model_path, model_path.with_suffix(".json"), *sorted((root / "data" / "raw").glob("*.csv"))]
    if not paths or any(not path.is_file() or not path.is_relative_to(root) for path in paths):
        raise FileNotFoundError("Production model, manifest, or cached history is missing.")
    manifest = {"format": 1, "files": [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256(path.read_bytes())}
        for path in paths
    ]}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
        for path in paths:
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        info = tarfile.TarInfo("runtime-manifest.json")
        info.size = len(encoded)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(encoded))
    return buffer.getvalue()
