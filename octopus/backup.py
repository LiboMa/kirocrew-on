"""Consistent SQLite backups, directory archives, and restore verification into a separate directory."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import time


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        tables = {name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in sorted(names)}
        version = db.execute("SELECT coalesce(max(version),0) FROM schema_version").fetchone()[0] if "schema_version" in tables else 0
        return integrity, tables, version
    finally:
        db.close()


def _manifest(path, values):
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)
    return values


def backup_database(source, dest_dir, clock=time.time):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock()))
    target = dest_dir / f"octopus-{stamp}.sqlite3"
    origin, copy = sqlite3.connect(source, timeout=10), sqlite3.connect(target)
    try:
        origin.backup(copy)
    finally:
        copy.close()
        origin.close()
    os.chmod(target, 0o600)
    integrity, tables, version = _inspect(target)
    return _manifest(target.with_suffix(".json"), {
        "file": target.name, "sha256": _sha256(target), "integrity": integrity,
        "schema_version": version, "tables": tables, "created": clock(), "source": str(source)})


def archive_directory(source, dest_dir, name, exclude=()):
    source, dest_dir = Path(source), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = dest_dir / f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.tar.gz"
    count = 0
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if path.is_file() and not path.is_symlink() and not any(fnmatch.fnmatch(relative, p) for p in exclude):
                tar.add(path, arcname=relative)
                count += 1
    return _manifest(dest_dir / (target.name.removesuffix(".tar.gz") + ".json"),
                     {"file": target.name, "sha256": _sha256(target), "files": count, "source": str(source)})


def restore_verify(manifest, into):
    manifest, into = Path(manifest), Path(into)
    values = json.loads(manifest.read_text())
    backup = manifest.parent / values["file"]
    if into.exists() and any(into.iterdir()):
        raise ValueError(f"恢复目标目录非空：{into}；请指定新的独立目录")
    if _sha256(backup) != values["sha256"]:
        raise ValueError("备份文件 sha256 与 manifest 不一致")
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    restored = into / "octopus.sqlite3"
    shutil.copy2(backup, restored)
    integrity, tables, version = _inspect(restored)
    if integrity != "ok" or tables != values["tables"] or version != values["schema_version"]:
        raise ValueError("恢复后的完整性、行数或 schema 版本与 manifest 不一致")
    return {"ok": True, "path": str(restored), "tables": tables, "schema_version": version}
