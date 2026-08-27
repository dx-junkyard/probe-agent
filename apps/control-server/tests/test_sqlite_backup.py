import multiprocessing
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from app.sqlite_backup import (
    SQLiteBackupError,
    backup_database,
    main,
    restore_database,
    verify_database,
)


def _create_database(path: Path, value: str, *, wal: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    if wal:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker (value) VALUES (?)", (value,))
    connection.commit()
    return connection


def _marker(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM marker").fetchone()[0]


def _leave_committed_wal(path: str, value: str) -> None:
    """Simulate a stopped/crashed server that left committed data in WAL."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker (value) VALUES (?)", (value,))
    connection.commit()
    os._exit(0)


def test_live_wal_backup_includes_committed_uncheckpointed_data(tmp_path):
    source = tmp_path / "live.db"
    destination = tmp_path / "backup.db"
    live_connection = _create_database(source, "committed-in-wal", wal=True)
    try:
        wal_path = Path(f"{source}-wal")
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0

        result = backup_database(source, destination)

        assert result == destination
        assert _marker(destination) == "committed-in-wal"
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        verify_database(destination)
    finally:
        live_connection.close()


def test_backup_rejects_same_file_and_existing_destination_without_overwrite(tmp_path):
    source = tmp_path / "source.db"
    existing = tmp_path / "existing.db"
    _create_database(source, "source").close()
    _create_database(existing, "keep-me").close()

    with pytest.raises(SQLiteBackupError, match="different files"):
        backup_database(source, source)
    with pytest.raises(SQLiteBackupError, match="already exists"):
        backup_database(source, existing)

    assert _marker(existing) == "keep-me"


def test_backup_overwrite_requires_explicit_flag_and_replaces_only_target(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    untouched = tmp_path / "untouched.db"
    _create_database(source, "new").close()
    _create_database(destination, "old").close()
    _create_database(untouched, "untouched").close()

    backup_database(source, destination, overwrite=True)

    assert _marker(destination) == "new"
    assert _marker(untouched) == "untouched"


def test_backup_rejects_directory_missing_parent_and_symlink_destination(tmp_path):
    source = tmp_path / "source.db"
    symlink_target = tmp_path / "symlink-target.db"
    symlink_destination = tmp_path / "backup-link.db"
    _create_database(source, "source").close()
    _create_database(symlink_target, "do-not-overwrite").close()
    symlink_destination.symlink_to(symlink_target)

    with pytest.raises(SQLiteBackupError, match="one database file|regular file"):
        backup_database(source, tmp_path, overwrite=True)
    with pytest.raises(SQLiteBackupError, match="directory does not exist"):
        backup_database(source, tmp_path / "missing" / "backup.db")
    with pytest.raises(SQLiteBackupError, match="regular file|symbolic link"):
        backup_database(source, symlink_destination, overwrite=True)

    assert _marker(symlink_target) == "do-not-overwrite"


def test_corrupt_restore_is_rejected_before_current_database_changes(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database")
    current = tmp_path / "probe.db"
    _create_database(current, "current").close()

    with pytest.raises(SQLiteBackupError, match="cannot verify|cannot open"):
        restore_database(
            corrupt,
            current,
            confirm_control_server_stopped=True,
        )

    assert _marker(current) == "current"
    assert list(tmp_path.glob("probe.db.pre-restore-*")) == []


def test_restore_replaces_database_and_keeps_consistent_safety_copy(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    _create_database(source, "from-backup").close()
    backup_database(source, backup)

    current = tmp_path / "probe.db"
    process = multiprocessing.get_context("spawn").Process(
        target=_leave_committed_wal, args=(str(current), "before-restore")
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0
    assert Path(f"{current}-wal").stat().st_size > 0
    current_mode = 0o640
    os.chmod(current, current_mode)

    result = restore_database(
        backup,
        current,
        confirm_control_server_stopped=True,
    )

    assert result.database == current
    assert result.safety_copy is not None
    assert result.safety_copy.is_file()
    assert _marker(current) == "from-backup"
    assert _marker(result.safety_copy) == "before-restore"
    assert stat.S_IMODE(current.stat().st_mode) == current_mode
    assert stat.S_IMODE(result.safety_copy.stat().st_mode) == current_mode
    assert not Path(f"{current}-wal").exists()
    assert not Path(f"{current}-shm").exists()


def test_restore_removes_only_exact_stale_sidecar_names(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    current = tmp_path / "probe.db"
    _create_database(source, "from-backup").close()
    backup_database(source, backup)
    _create_database(current, "current").close()

    Path(f"{current}-wal").touch()
    Path(f"{current}-shm").touch()
    wal_neighbor = Path(f"{current}-wal.keep")
    shm_neighbor = Path(f"{current}-shm.keep")
    wal_neighbor.write_text("keep", encoding="utf-8")
    shm_neighbor.write_text("keep", encoding="utf-8")

    restore_database(
        backup,
        current,
        confirm_control_server_stopped=True,
    )

    assert not Path(f"{current}-wal").exists()
    assert not Path(f"{current}-shm").exists()
    assert wal_neighbor.read_text(encoding="utf-8") == "keep"
    assert shm_neighbor.read_text(encoding="utf-8") == "keep"


def test_sidecar_cleanup_failure_happens_before_main_database_replace(
    tmp_path, monkeypatch
):
    import app.sqlite_backup as sqlite_backup

    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    current = tmp_path / "probe.db"
    _create_database(source, "from-backup").close()
    backup_database(source, backup)
    _create_database(current, "current").close()

    def refuse_cleanup(*_args, **_kwargs):
        raise SQLiteBackupError("cannot remove stale sidecar")

    monkeypatch.setattr(sqlite_backup, "_checkpoint_and_remove_sidecars", refuse_cleanup)
    with pytest.raises(SQLiteBackupError, match="cannot remove stale sidecar"):
        restore_database(
            backup,
            current,
            confirm_control_server_stopped=True,
        )

    assert _marker(current) == "current"


def test_restore_requires_explicit_stopped_confirmation(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "probe.db"
    _create_database(source, "source").close()
    _create_database(destination, "current").close()

    with pytest.raises(SQLiteBackupError, match="explicitly confirm"):
        restore_database(source, destination)

    assert _marker(destination) == "current"


def test_restore_rejects_same_path_and_existing_safety_copy(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "probe.db"
    safety_copy = tmp_path / "safety.db"
    _create_database(source, "source").close()
    _create_database(destination, "current").close()
    _create_database(safety_copy, "do-not-overwrite").close()

    with pytest.raises(SQLiteBackupError, match="different files"):
        restore_database(
            source,
            source,
            confirm_control_server_stopped=True,
        )
    with pytest.raises(SQLiteBackupError, match="safety copy already exists"):
        restore_database(
            source,
            destination,
            confirm_control_server_stopped=True,
            safety_copy=safety_copy,
        )

    assert _marker(destination) == "current"
    assert _marker(safety_copy) == "do-not-overwrite"


def test_cli_backup_verify_restore_round_trip(tmp_path, capsys):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    destination = tmp_path / "probe.db"
    safety = tmp_path / "explicit-safety.db"
    _create_database(source, "backup-value").close()
    _create_database(destination, "current-value").close()

    assert main(["backup", str(source), str(backup)]) == 0
    assert main(["verify", str(backup)]) == 0
    assert main(
        [
            "restore",
            str(backup),
            str(destination),
            "--confirm-control-server-stopped",
            "--safety-copy",
            str(safety),
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "backup created:" in output
    assert "integrity_check ok:" in output
    assert "database restored:" in output
    assert _marker(destination) == "backup-value"
    assert _marker(safety) == "current-value"
