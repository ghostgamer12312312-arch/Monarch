import sqlite3

import pytest

from monarchbot.backup import backup_database


def test_backup_is_consistent_and_preserves_player_data(game, tmp_path):
    service, _, _ = game
    service.register(101, "hunter", "Test Hunter")
    source = service.db.path
    destination = tmp_path / "backups" / "copy.sqlite3"
    result = backup_database(source, destination)
    assert result == destination
    with sqlite3.connect(destination) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            con.execute("SELECT display_name FROM players WHERE telegram_id=101").fetchone()[0]
            == "Test Hunter"
        )


def test_backup_refuses_to_overwrite(game, tmp_path):
    service, _, _ = game
    service.register(101, None, "Hunter")
    destination = tmp_path / "existing.sqlite3"
    destination.write_text("keep")
    with pytest.raises(FileExistsError):
        backup_database(service.db.path, destination)
    assert destination.read_text() == "keep"
