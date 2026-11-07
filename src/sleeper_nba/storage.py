"""Atomic transactions are the audit source; exports are disposable views."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .common import canonical, digest, iso


class Ledger:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "data" / "transactions"

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        # Outside tracked data; flock releases automatically on process failure.
        lockpath = self.root / ".cache" / "writer.lock"
        lockpath.parent.mkdir(exist_ok=True)
        with lockpath.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def transactions(self):
        if not self.path.exists():
            return []
        result = []
        for path in sorted(self.path.glob("*.json")):
            value = json.loads(path.read_text())
            body = {k: v for k, v in value.items() if k != "sha256"}
            if digest(body) != value["sha256"]:
                raise ValueError(f"Corrupt transaction: {path.name}")
            result.append(value)
        return result

    def records(self, kind=None):
        records = [r for tx in self.transactions() for r in tx["records"]]
        return [r for r in records if kind is None or r["kind"] == kind]

    def append(self, records):
        if not records:
            return 0
        existing = {r["id"]: r for r in self.records()}
        unique = {}
        for record in records:
            if record["id"] in existing:
                if canonical(existing[record["id"]]) != canonical(record):
                    raise ValueError(f"Attempted immutable overwrite: {record['id']}")
                continue
            if record["id"] in unique and canonical(unique[record["id"]]) != canonical(record):
                raise ValueError("Conflicting record IDs in transaction")
            unique[record["id"]] = record
        if not unique:
            return 0
        body = {"schema_version": 1, "created_at": iso(), "records": list(unique.values())}
        value = {**body, "sha256": digest(body)}
        self.path.mkdir(parents=True, exist_ok=True)
        self._atomic(self.path / (value["sha256"] + ".json"), canonical(value) + "\n")
        return len(unique)

    @staticmethod
    def _atomic(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def event(code, context, at=None, **details):
    return {
        "id": "quality:" + digest([code, context, details]),
        "kind": "quality",
        "code": code,
        "context": context,
        "at": at or iso(),
        **details,
    }
