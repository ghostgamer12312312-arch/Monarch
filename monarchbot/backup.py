from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def backup_database(source_path: str | Path, destination_path: str | Path) -> Path:
    source = Path(source_path).expanduser()
    destination = Path(destination_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the source database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(tmp_name)
    source_con = destination_con = None
    try:
        source_con = sqlite3.connect(str(source), timeout=30)
        destination_con = sqlite3.connect(str(temporary), timeout=30)
        source_con.backup(destination_con)
        check = destination_con.execute("PRAGMA integrity_check").fetchone()
        if check is None or check[0] != "ok":
            raise sqlite3.DatabaseError(
                f"Backup integrity check failed: {check[0] if check else 'no result'}"
            )
        destination_con.close()
        destination_con = None
        source_con.close()
        source_con = None
        os.link(temporary, destination)
        return destination
    finally:
        if destination_con is not None:
            destination_con.close()
        if source_con is not None:
            source_con.close()
        temporary.unlink(missing_ok=True)


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description="Create and integrity-check a SQLite backup.")
    parser.add_argument("--source", default=os.getenv("DATABASE_PATH", "data/monarch.sqlite3"))
    parser.add_argument("--destination", help="New backup file; an existing file will not be overwritten.")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-utc")
    destination = args.destination or f"backups/monarch-{stamp}.sqlite3"
    print(f"Backup created: {backup_database(args.source, destination)}")


if __name__ == "__main__":
    main()
