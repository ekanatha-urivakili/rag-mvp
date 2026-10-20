"""Backup, restore and restore test for Postgres, Qdrant and object storage (ARCHITECTURE §8 Phase 6).

  uv run python scripts/backup.py backup  [--out backups/<timestamp>] [--skip-objects]
  uv run python scripts/backup.py verify  backups/<timestamp>     # restore test into scratch targets
  uv run python scripts/backup.py restore backups/<timestamp> --yes  # destructive; stop api/worker/web first

Postgres is the source of truth and is dumped from an exported snapshot, so the row counts in
manifest.json describe exactly what the dump holds. Qdrant is derived state: a snapshot restores it
fastest, and `rag reindex` can always rebuild it from Postgres. Raw uploads are mirrored file by file.
Connection settings come from .env (the same settings the API uses); Postgres tools run inside the
compose `postgres` container, so host and server versions never mismatch. COMPOSE overrides the
compose command (default: "podman compose").
"""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from rag.core.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DB = "rag_restore_check"
COMPOSE = shlex.split(os.environ.get("COMPOSE", "podman compose"))


def _db_name() -> str:
    name = get_settings().database_url.rsplit("/", 1)[1].split("?", 1)[0]
    if not name.replace("_", "").isalnum():
        raise SystemExit(f"Unexpected database name {name!r}")
    return name


def _pg(cmd: str, *, stdin: Path | None = None, stdout: Path | None = None) -> None:
    """Runs a shell command in the postgres container, where POSTGRES_USER is set."""
    with (
        open(stdin, "rb") if stdin else open(os.devnull, "rb") as fin,
        open(stdout, "wb") if stdout else open(os.devnull, "wb") as fout,
    ):
        proc = subprocess.run(  # noqa: S603 - fixed argv; cmd is built from constants and validated names
            [*COMPOSE, "exec", "-T", "postgres", "sh", "-c", cmd],
            cwd=ROOT,
            stdin=fin,
            stdout=fout,
            stderr=subprocess.PIPE,
            check=False,
        )
    if proc.returncode != 0:
        raise SystemExit(f"postgres command failed: {proc.stderr.decode(errors='replace').strip()}")


async def _table_counts(conn: AsyncConnection) -> dict[str, int]:
    tables = (
        await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        )
    ).scalars()
    counts = {}
    for t in list(tables):
        if not t.replace("_", "").isalnum():
            raise SystemExit(f"Unexpected table name {t!r}")
        sql = f'SELECT count(*) FROM "{t}"'  # noqa: S608 - name from information_schema, validated above
        counts[t] = int((await conn.execute(text(sql))).scalar_one())
    return counts


def _qdrant() -> httpx.Client:
    s = get_settings()
    headers = {"api-key": s.qdrant_api_key.get_secret_value()} if s.qdrant_api_key else {}
    return httpx.Client(base_url=s.qdrant_url, headers=headers, timeout=600)


def _qdrant_count(q: httpx.Client, collection: str) -> int:
    r = q.post(f"/collections/{collection}/points/count", json={"exact": True})
    r.raise_for_status()
    return int(r.json()["result"]["count"])


def _alias_target(q: httpx.Client, alias: str) -> str | None:
    r = q.get("/aliases")
    r.raise_for_status()
    return next((a["collection_name"] for a in r.json()["result"]["aliases"] if a["alias_name"] == alias), None)


def _qdrant_upload(q: httpx.Client, collection: str, snapshot: Path) -> None:
    with snapshot.open("rb") as f:
        r = q.post(
            f"/collections/{collection}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            files={"snapshot": (snapshot.name, f, "application/octet-stream")},
        )
    r.raise_for_status()


def _s3() -> Any:
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key.get_secret_value(),
        aws_secret_access_key=s.s3_secret_key.get_secret_value(),
        region_name=s.s3_region,
    )


def _local_objects(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


# --- backup -----------------------------------------------------------------------


async def backup(out: Path, skip_objects: bool) -> None:
    s = get_settings()
    out.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240 - one-off CLI, nothing else runs on this loop
    manifest: dict[str, Any] = {"created_at": datetime.now(UTC).isoformat()}

    engine = create_async_engine(s.database_url)
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        snapshot_id = (await conn.execute(text("SELECT pg_export_snapshot()"))).scalar_one()
        counts = await _table_counts(conn)
        # The exporting transaction stays open while pg_dump reads the same snapshot.
        await asyncio.to_thread(
            _pg,
            f'pg_dump -U "$POSTGRES_USER" -d {_db_name()} -Fc --snapshot={shlex.quote(snapshot_id)}',
            stdout=out / "postgres.dump",
        )
        await conn.rollback()
    await engine.dispose()
    manifest["postgres"] = {"file": "postgres.dump", "tables": counts}
    print(f"postgres: {sum(counts.values())} rows in {len(counts)} tables")

    with _qdrant() as q:
        collection = _alias_target(q, s.qdrant_alias)
        if collection is None:
            raise SystemExit(f"Qdrant alias {s.qdrant_alias} does not exist")
        before = _qdrant_count(q, collection)
        r = q.post(f"/collections/{collection}/snapshots", params={"wait": "true"})
        r.raise_for_status()
        name = r.json()["result"]["name"]
        after = _qdrant_count(q, collection)
        path = out / f"qdrant-{collection}.snapshot"
        with q.stream("GET", f"/collections/{collection}/snapshots/{name}") as resp, path.open("wb") as f:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                f.write(chunk)
        q.delete(f"/collections/{collection}/snapshots/{name}").raise_for_status()
    manifest["qdrant"] = {
        "alias": s.qdrant_alias,
        "collection": collection,
        "file": path.name,
        "points_min": min(before, after),
        "points_max": max(before, after),
    }
    print(f"qdrant: {collection} ({after} points)")

    if not skip_objects:
        s3, root = _s3(), out / "objects"
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=s.s3_bucket):
            for obj in page.get("Contents", []):
                target = root / obj["Key"]
                if root.resolve() not in target.resolve().parents:
                    raise SystemExit(f"Refusing unsafe object key {obj['Key']!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(s.s3_bucket, obj["Key"], str(target))
        count, size = _local_objects(root) if root.exists() else (0, 0)
        manifest["objects"] = {"bucket": s.s3_bucket, "dir": "objects", "count": count, "bytes": size}
        print(f"objects: {count} files, {size} bytes")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"backup written to {out}")


# --- verify (restore test) ----------------------------------------------------------


async def verify(src: Path) -> bool:
    s = get_settings()
    manifest = json.loads((src / "manifest.json").read_text())
    ok: bool = True

    admin_url = s.database_url.rsplit("/", 1)[0] + "/postgres"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        await conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    try:
        await asyncio.to_thread(
            _pg,
            f'pg_restore -U "$POSTGRES_USER" -d {SCRATCH_DB} --no-owner --exit-on-error',
            stdin=src / manifest["postgres"]["file"],
        )
        scratch = create_async_engine(s.database_url.rsplit("/", 1)[0] + f"/{SCRATCH_DB}")
        async with scratch.connect() as conn:
            restored = await _table_counts(conn)
        await scratch.dispose()
        expected = manifest["postgres"]["tables"]
        diff = {t: (expected.get(t), restored.get(t)) for t in expected.keys() | restored.keys()}
        diff = {t: v for t, v in diff.items() if v[0] != v[1]}
        ok &= not diff
        print(f"postgres: {'OK' if not diff else f'MISMATCH {diff}'} ({sum(restored.values())} rows)")
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        await admin.dispose()

    qd = manifest["qdrant"]
    scratch_collection = f"restore_check_{int(time.time())}"
    with _qdrant() as q:
        try:
            _qdrant_upload(q, scratch_collection, src / qd["file"])
            points = _qdrant_count(q, scratch_collection)
            good = qd["points_min"] <= points <= qd["points_max"]
            ok &= good
            print(f"qdrant: {'OK' if good else 'MISMATCH'} ({points} points, expected {qd['points_min']})")
        finally:
            q.delete(f"/collections/{scratch_collection}")

    if objects := manifest.get("objects"):
        count, size = _local_objects(src / objects["dir"]) if (src / objects["dir"]).exists() else (0, 0)
        good = (count, size) == (objects["count"], objects["bytes"])
        ok &= good
        print(f"objects: {'OK' if good else 'MISMATCH'} ({count} files, {size} bytes)")

    print("restore test PASSED" if ok else "restore test FAILED")
    return ok


# --- restore -----------------------------------------------------------------------


async def restore(src: Path) -> None:
    s = get_settings()
    manifest = json.loads((src / "manifest.json").read_text())
    await asyncio.to_thread(
        _pg,
        f'pg_restore -U "$POSTGRES_USER" -d {_db_name()} --clean --if-exists --no-owner --single-transaction',
        stdin=src / manifest["postgres"]["file"],
    )
    print("postgres restored")

    qd = manifest["qdrant"]
    with _qdrant() as q:
        _qdrant_upload(q, qd["collection"], src / qd["file"])
        actions: list[dict[str, Any]] = []
        if _alias_target(q, qd["alias"]) is not None:
            actions.append({"delete_alias": {"alias_name": qd["alias"]}})
        actions.append({"create_alias": {"alias_name": qd["alias"], "collection_name": qd["collection"]}})
        q.post("/collections/aliases", json={"actions": actions}).raise_for_status()
    print(f"qdrant restored into {qd['collection']} and alias {qd['alias']} points to it")

    if objects := manifest.get("objects"):
        s3, root = _s3(), src / objects["dir"]
        files = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
        for p in files:
            s3.upload_file(str(p), s.s3_bucket, p.relative_to(root).as_posix())
        print(f"objects restored: {len(files)} files")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out", type=Path, default=ROOT / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    b.add_argument("--skip-objects", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("src", type=Path)
    r = sub.add_parser("restore")
    r.add_argument("src", type=Path)
    r.add_argument("--yes", action="store_true", help="Confirm overwriting the live database, index and bucket")
    args = p.parse_args()

    if args.cmd == "backup":
        asyncio.run(backup(args.out, args.skip_objects))
    elif args.cmd == "verify":
        return 0 if asyncio.run(verify(args.src)) else 1
    else:
        if not args.yes:
            print("restore overwrites live data; stop api/worker/web and re-run with --yes", file=sys.stderr)
            return 2
        asyncio.run(restore(args.src))
    return 0


if __name__ == "__main__":
    sys.exit(main())
