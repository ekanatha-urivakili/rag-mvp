import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from scripts import backup


def _backup(src: Path) -> dict[str, object]:
    (src / "postgres.dump").write_bytes(b"database")
    (src / "qdrant.snapshot").write_bytes(b"index")
    objects = src / "objects"
    objects.mkdir()
    (objects / "upload.pdf").write_bytes(b"original")
    manifest = {
        "postgres": {"file": "postgres.dump", "tables": {"widgets": 0}},
        "qdrant": {"file": "qdrant.snapshot", "points_min": 0, "points_max": 0},
        "objects": {"dir": "objects", "count": 1, "bytes": 8},
        "sha256": {p.relative_to(src).as_posix(): backup._checksum(p) for p in src.rglob("*") if p.is_file()},
    }
    (src / "manifest.json").write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("artifact", ["postgres.dump", "qdrant.snapshot", "objects/upload.pdf"])
@pytest.mark.parametrize("operation", [backup.verify, backup.restore])
async def test_corruption_rejected_before_database_or_store_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str, operation: object
) -> None:
    _backup(tmp_path)
    path = tmp_path / artifact
    path.write_bytes(b"x" * path.stat().st_size)
    check = AsyncMock()
    monkeypatch.setattr(backup, "_check_pg_target", check)
    with pytest.raises(SystemExit, match="missing or corrupted"):
        await operation(tmp_path)
    check.assert_not_awaited()


def test_backup_paths_cannot_escape_directory(tmp_path: Path) -> None:
    manifest = _backup(tmp_path)
    manifest["postgres"]["file"] = "../outside.dump"
    manifest["sha256"]["../outside.dump"] = manifest["sha256"].pop("postgres.dump")
    with pytest.raises(SystemExit, match="Unsafe backup path"):
        backup._validate_backup(tmp_path, manifest)


async def test_verify_uses_unique_scratch_databases_without_preemptive_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backup(tmp_path)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    conn = engine.connect.return_value.__aenter__.return_value
    conn.execute = AsyncMock()
    monkeypatch.setattr(backup, "_check_pg_target", AsyncMock())
    monkeypatch.setattr(backup, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(backup, "_table_counts", AsyncMock(return_value={"widgets": 0}))
    monkeypatch.setattr(backup, "_pg", MagicMock(return_value=b""))
    monkeypatch.setattr(backup, "_qdrant", MagicMock())
    monkeypatch.setattr(backup, "_qdrant_upload", MagicMock())
    monkeypatch.setattr(backup, "_qdrant_count", MagicMock(return_value=0))
    assert await backup.verify(tmp_path)
    assert await backup.verify(tmp_path)
    statements = [str(call.args[0]) for call in conn.execute.await_args_list]
    creates = [s.removeprefix("CREATE DATABASE ") for s in statements if s.startswith("CREATE DATABASE ")]
    drops = [s for s in statements if s.startswith("DROP DATABASE ")]
    assert len(set(creates)) == 2
    assert all(f"DROP DATABASE {name} WITH (FORCE)" in drops for name in creates)
    assert not any("IF EXISTS" in s for s in drops)


async def test_mismatched_postgres_cluster_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    conn = engine.connect.return_value.__aenter__.return_value
    conn.execute = AsyncMock()
    conn.execute.return_value = MagicMock()
    conn.execute.return_value.scalar_one.return_value = "123"
    monkeypatch.setattr(backup, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(backup, "_pg", MagicMock(return_value=b"456\n"))
    with pytest.raises(SystemExit, match="different clusters"):
        await backup._check_pg_target()
    engine.dispose.assert_awaited_once()
