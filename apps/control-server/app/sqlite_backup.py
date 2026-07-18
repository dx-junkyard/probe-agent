"""Consistent SQLite backup and restore utility.

The Control Server uses WAL mode, so copying or archiving ``probe.db`` while
the service is running can produce an inconsistent database.  This module
uses SQLite's online backup API and publishes completed databases atomically.

Restore must only be run while the Control Server is stopped.  The CLI
requires an explicit acknowledgement before it will replace a database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Union


class SQLiteBackupError(RuntimeError):
    """A backup, verification, or restore operation failed."""


@dataclass(frozen=True)
class RestoreResult:
    database: Path
    safety_copy: Optional[Path]


def _resolved_existing_file(path: Path, *, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise SQLiteBackupError(f"{label} does not exist: {path}") from exc
    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise SQLiteBackupError(f"cannot inspect {label}: {resolved}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SQLiteBackupError(f"{label} is not a regular file: {resolved}")
    return resolved


def _resolved_destination(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.name or expanded.name in {".", ".."}:
        raise SQLiteBackupError(f"destination must name one database file: {path}")
    try:
        parent = expanded.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SQLiteBackupError(
            f"destination directory does not exist: {expanded.parent}"
        ) from exc
    try:
        metadata = parent.stat()
    except OSError as exc:
        raise SQLiteBackupError(
            f"cannot inspect destination directory: {parent}: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise SQLiteBackupError(f"destination parent is not a directory: {parent}")
    destination = parent / expanded.name
    if destination.exists():
        try:
            destination_metadata = destination.lstat()
        except OSError as exc:
            raise SQLiteBackupError(
                f"cannot inspect destination: {destination}: {exc}"
            ) from exc
        if not stat.S_ISREG(destination_metadata.st_mode):
            raise SQLiteBackupError(
                f"destination is not a regular file: {destination}"
            )
    return destination


def _reject_same_file(source: Path, destination: Path) -> None:
    if source == destination:
        raise SQLiteBackupError("source and destination must be different files")
    if destination.exists():
        try:
            if source.samefile(destination):
                raise SQLiteBackupError("source and destination must be different files")
        except OSError as exc:
            raise SQLiteBackupError(
                f"cannot compare source and destination: {exc}"
            ) from exc


def _readonly_connection(path: Path) -> sqlite3.Connection:
    # Path.as_uri() percent-encodes '?' and '#' in file names before URI query
    # parameters are appended.
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30.0)


def _integrity_check(connection: sqlite3.Connection, *, label: str) -> None:
    try:
        rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    except sqlite3.Error as exc:
        raise SQLiteBackupError(f"cannot verify {label}: {exc}") from exc
    if rows != ["ok"]:
        detail = "; ".join(rows[:10]) or "no integrity result"
        raise SQLiteBackupError(f"{label} failed SQLite integrity_check: {detail}")


DatabasePath = Union[os.PathLike[str], str]


def verify_database(path: DatabasePath) -> Path:
    database = _resolved_existing_file(Path(path), label="database")
    try:
        with closing(_readonly_connection(database)) as connection:
            _integrity_check(connection, label=str(database))
    except sqlite3.Error as exc:
        raise SQLiteBackupError(f"cannot open database {database}: {exc}") from exc
    return database


def _temporary_database(destination: Path) -> Path:
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
    except OSError as exc:
        raise SQLiteBackupError(
            f"cannot create temporary database beside {destination}: {exc}"
        ) from exc
    os.close(descriptor)
    return Path(raw_path)


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _checkpoint_and_remove_sidecars(path: Path, *, database_exists: bool) -> None:
    if database_exists:
        try:
            with closing(sqlite3.connect(path, timeout=30.0)) as connection:
                result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error as exc:
            raise SQLiteBackupError(
                f"cannot checkpoint current database before restore: {exc}"
            ) from exc
        if result is not None and int(result[0]) != 0:
            raise SQLiteBackupError(
                "current database WAL is still busy; Control Server may not be stopped"
            )

    # Delete only the two exact sidecar names for the destination.  This runs
    # before the main-file replacement, after the safety copy has captured
    # any committed WAL data.  A deletion failure therefore leaves the main
    # destination un-replaced and fails closed.
    for sidecar in _sidecars(path):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SQLiteBackupError(f"cannot remove stale sidecar {sidecar}: {exc}") from exc
    _fsync_directory(path.parent)


def _remove_temporary_database(path: Path) -> None:
    # Only caller-created, randomized files are passed here.  Never recurse or
    # expand a glob when cleaning up after a failed operation.
    for candidate in (path, *_sidecars(path)):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Preserve the original operation error.  The randomized path is
            # printed by that error and can be removed manually if necessary.
            pass


def _fsync_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SQLiteBackupError(f"cannot fsync database file {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SQLiteBackupError(f"cannot fsync directory {path}: {exc}") from exc


def _copy_database(source: Path, temporary: Path, *, label: str) -> None:
    try:
        with closing(_readonly_connection(source)) as source_connection:
            # Verify before copying.  The generated database is verified again
            # below, so corruption detected at either boundary fails closed.
            _integrity_check(source_connection, label=label)
            with closing(
                sqlite3.connect(temporary, timeout=30.0)
            ) as destination_connection:
                source_connection.backup(destination_connection)
                _integrity_check(destination_connection, label=f"copy of {label}")
                destination_connection.commit()
    except SQLiteBackupError:
        raise
    except sqlite3.Error as exc:
        raise SQLiteBackupError(f"cannot copy {label}: {exc}") from exc


def _apply_metadata(
    path: Path,
    *,
    mode: int = 0o600,
    owner: Optional[tuple[int, int]] = None,
) -> None:
    if owner is not None and hasattr(os, "chown"):
        uid, gid = owner
        try:
            os.chown(path, uid, gid)
        except PermissionError:
            metadata = path.stat()
            if (metadata.st_uid, metadata.st_gid) != owner:
                raise SQLiteBackupError(
                    f"cannot preserve owner {uid}:{gid} on {path}"
                )
        except OSError as exc:
            raise SQLiteBackupError(f"cannot preserve owner on {path}: {exc}") from exc

    # chown can clear special mode bits, so apply the requested mode last.
    try:
        os.chmod(path, mode)
    except OSError as exc:
        raise SQLiteBackupError(f"cannot set mode on {path}: {exc}") from exc


def _publish_database(temporary: Path, destination: Path, *, overwrite: bool) -> None:
    if overwrite:
        try:
            os.replace(temporary, destination)
        except OSError as exc:
            raise SQLiteBackupError(
                f"cannot atomically replace destination {destination}: {exc}"
            ) from exc
    else:
        # Hard-linking within the destination directory is an atomic
        # no-clobber publication: it fails if another process creates the
        # destination after our earlier existence check.
        try:
            os.link(temporary, destination)
            temporary.unlink()
        except FileExistsError as exc:
            raise SQLiteBackupError(
                f"destination already exists (use --overwrite to replace it): {destination}"
            ) from exc
        except OSError as exc:
            raise SQLiteBackupError(
                f"cannot atomically publish destination {destination}: {exc}"
            ) from exc
    _fsync_directory(destination.parent)


def backup_database(
    source: DatabasePath,
    destination: DatabasePath,
    *,
    overwrite: bool = False,
) -> Path:
    source_path = _resolved_existing_file(Path(source), label="source database")
    destination_path = _resolved_destination(Path(destination))
    _reject_same_file(source_path, destination_path)
    if destination_path.exists() and not overwrite:
        raise SQLiteBackupError(
            f"destination already exists (use --overwrite to replace it): {destination_path}"
        )
    if destination_path.is_symlink():
        raise SQLiteBackupError(f"destination must not be a symbolic link: {destination_path}")

    temporary = _temporary_database(destination_path)
    try:
        _copy_database(source_path, temporary, label=str(source_path))
        _apply_metadata(temporary, mode=0o600)
        _fsync_file(temporary)
        _publish_database(temporary, destination_path, overwrite=overwrite)
        temporary = Path()
    finally:
        if temporary != Path():
            _remove_temporary_database(temporary)
    return destination_path


def _default_safety_copy(destination: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return destination.with_name(f"{destination.name}.pre-restore-{timestamp}")


def restore_database(
    backup: DatabasePath,
    destination: DatabasePath,
    *,
    confirm_control_server_stopped: bool = False,
    safety_copy: Optional[DatabasePath] = None,
) -> RestoreResult:
    if not confirm_control_server_stopped:
        raise SQLiteBackupError(
            "restore refused: stop the Control Server and explicitly confirm it is stopped"
        )

    backup_path = _resolved_existing_file(Path(backup), label="backup database")
    destination_path = _resolved_destination(Path(destination))
    _reject_same_file(backup_path, destination_path)
    if destination_path.is_symlink():
        raise SQLiteBackupError(f"destination must not be a symbolic link: {destination_path}")

    # A corrupt backup is rejected before a safety copy or destination is
    # created.  This is intentionally redundant with _copy_database's check.
    verify_database(backup_path)

    current_metadata: Optional[os.stat_result] = None
    if destination_path.exists():
        current_path = _resolved_existing_file(
            destination_path, label="current database"
        )
        if current_path != destination_path:
            raise SQLiteBackupError(
                f"destination must not resolve to another path: {destination_path}"
            )
        current_metadata = current_path.stat()

    restored_temporary = _temporary_database(destination_path)
    published_safety_copy: Optional[Path] = None
    try:
        _copy_database(backup_path, restored_temporary, label=str(backup_path))

        if current_metadata is not None:
            safety_path = (
                _resolved_destination(Path(safety_copy))
                if safety_copy is not None
                else _default_safety_copy(destination_path)
            )
            if safety_path == destination_path or safety_path == backup_path:
                raise SQLiteBackupError(
                    "safety copy must differ from the backup and destination"
                )
            if safety_path.exists() or safety_path.is_symlink():
                raise SQLiteBackupError(f"safety copy already exists: {safety_path}")
            safety_temporary = _temporary_database(safety_path)
            try:
                _copy_database(destination_path, safety_temporary, label=str(destination_path))
                _apply_metadata(
                    safety_temporary,
                    mode=stat.S_IMODE(current_metadata.st_mode),
                    owner=(current_metadata.st_uid, current_metadata.st_gid),
                )
                _fsync_file(safety_temporary)
                _publish_database(safety_temporary, safety_path, overwrite=False)
                safety_temporary = Path()
                published_safety_copy = safety_path
            finally:
                if safety_temporary != Path():
                    _remove_temporary_database(safety_temporary)

        if current_metadata is None:
            _apply_metadata(restored_temporary, mode=0o600)
        else:
            _apply_metadata(
                restored_temporary,
                mode=stat.S_IMODE(current_metadata.st_mode),
                owner=(current_metadata.st_uid, current_metadata.st_gid),
            )
        _fsync_file(restored_temporary)

        _checkpoint_and_remove_sidecars(
            destination_path, database_exists=current_metadata is not None
        )
        _publish_database(restored_temporary, destination_path, overwrite=True)
        restored_temporary = Path()
        verify_database(destination_path)
    finally:
        if restored_temporary != Path():
            _remove_temporary_database(restored_temporary)

    return RestoreResult(database=destination_path, safety_copy=published_safety_copy)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, verify, or restore a consistent SQLite database"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="create a consistent online backup")
    backup.add_argument("source")
    backup.add_argument("destination")
    backup.add_argument(
        "--overwrite",
        action="store_true",
        help="explicitly replace the one destination file if it already exists",
    )

    verify = commands.add_parser("verify", help="run SQLite integrity_check")
    verify.add_argument("database")

    restore = commands.add_parser("restore", help="restore a verified backup")
    restore.add_argument("backup")
    restore.add_argument("destination")
    restore.add_argument(
        "--confirm-control-server-stopped",
        action="store_true",
        required=True,
        help="required acknowledgement; restoring while the server runs is unsafe",
    )
    restore.add_argument(
        "--safety-copy",
        help="optional non-existing path for the current database safety copy",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_database(args.source, args.destination, overwrite=args.overwrite)
            print(f"backup created: {result}")
        elif args.command == "verify":
            result = verify_database(args.database)
            print(f"integrity_check ok: {result}")
        else:
            result = restore_database(
                args.backup,
                args.destination,
                confirm_control_server_stopped=args.confirm_control_server_stopped,
                safety_copy=args.safety_copy,
            )
            print(f"database restored: {result.database}")
            if result.safety_copy is not None:
                print(f"previous database safety copy: {result.safety_copy}")
    except SQLiteBackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main().
    raise SystemExit(main())
