import importlib.util
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/shared_album_reminders.py"
spec = importlib.util.spec_from_file_location("reminders", SCRIPT)
reminders = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reminders
spec.loader.exec_module(reminders)


def make_library(tmp_path):
    library = tmp_path / "Test.photoslibrary"
    (library / "database").mkdir(parents=True)
    path = library / "database/Photos.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
        INSERT INTO Z_PRIMARYKEY VALUES (92, 'CollectionShare'), (93, 'MomentShare');
        CREATE TABLE ZSHARE (
            Z_ENT INTEGER, ZUUID TEXT, ZTITLE TEXT, ZCREATIONDATE REAL,
            ZTRASHEDSTATE INTEGER, ZCLOUDDELETESTATE INTEGER
        );
        INSERT INTO ZSHARE VALUES
            (92, 'album-a', 'Example Album', 789004800, 0, 0),
            (92, 'album-b', 'Undated Album', NULL, 0, 0),
            (92, 'album-trash', 'Trashed', 789004800, 1, 0),
            (92, 'album-deleted', 'Deleted', 789004800, 0, 1),
            (93, 'moment-a', 'Temporary Link', 789004800, 0, 0);
    """)
    connection.commit()
    connection.close()
    return library


def test_reads_only_live_shared_albums_and_creation_date(tmp_path):
    albums = reminders.read_albums(make_library(tmp_path))
    assert [album.album_id for album in albums] == ["album-a", "album-b"]
    assert albums[0].created_on == date(2026, 1, 2)
    assert albums[1].created_on is None


def test_creation_plus_28_days_and_discovery_fallback(tmp_path):
    planned = reminders.plan_reminders(
        reminders.read_albums(make_library(tmp_path)), [], today=date(2026, 9, 12)
    )
    assert [item.due_on for item in planned] == [date(2026, 1, 30), date(2026, 10, 10)]
    assert [item.date_basis for item in planned] == ["creation", "discovery"]


def test_completed_tasks_and_renamed_albums_still_dedupe():
    album = reminders.Album("album-a", "Renamed Album", date(2026, 1, 2))
    existing = [{"album_id": "album-a", "title": "An older title", "status": "Completed"}]
    assert reminders.plan_reminders([album], existing, today=date(2026, 9, 12)) == []


def test_title_match_dedupes_legacy_task_without_album_id():
    album = reminders.Album("album-a", "Example Album", None)
    existing = [
        {"title": 'Copy shared album "Example Album" to the photo library', "status": "Completed"}
    ]
    assert reminders.plan_reminders([album], existing, today=date(2026, 9, 12)) == []


def test_duplicate_album_titles_are_ambiguous_without_ids():
    albums = [
        reminders.Album("album-a", "Example Album", None),
        reminders.Album("album-b", "Example Album", None),
    ]
    existing = [{"title": 'Copy shared album "Example Album" to the photo library'}]
    with pytest.raises(ValueError, match="ambiguous"):
        reminders.plan_reminders(albums, existing, today=date(2026, 9, 12))


def test_missing_library_does_not_create_a_database(tmp_path):
    library = tmp_path / "Missing.photoslibrary"
    with pytest.raises(FileNotFoundError):
        reminders.read_albums(library)
    assert not library.exists()


def test_cli_refuses_to_claim_task_writing_before_contract_exists():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "task contract" in result.stderr.lower()


def test_wal_albums_are_included_and_database_is_unchanged(tmp_path):
    library = make_library(tmp_path)
    connection = sqlite3.connect(library / "database/Photos.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("INSERT INTO ZSHARE VALUES (92, 'album-c', 'Recent Album', 0, 0, 0)")
    connection.commit()
    try:
        albums = reminders.read_albums(library)
        assert [album.album_id for album in albums] == ["album-a", "album-b", "album-c"]
        assert connection.execute("SELECT COUNT(*) FROM ZSHARE").fetchone()[0] == 6
    finally:
        connection.close()


def test_unsupported_schema_cannot_report_empty_inventory(tmp_path):
    library = make_library(tmp_path)
    with sqlite3.connect(library / "database/Photos.sqlite") as connection:
        connection.execute("DELETE FROM Z_PRIMARYKEY WHERE Z_NAME = 'CollectionShare'")
    with pytest.raises(ValueError, match="unsupported"):
        reminders.read_albums(library)


def test_dry_run_exposes_unchecked_dedupe_without_writing(tmp_path):
    import json

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--library", str(make_library(tmp_path))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["albums"] == 2
    assert output["task_writing_available"] is False
    assert output["dedupe_checked"] is False


def test_same_title_on_different_identified_album_does_not_suppress_new_task():
    album = reminders.Album("album-new", "Example Album", None)
    existing = [
        {"album_id": "album-old", "title": 'Copy shared album "Example Album" to the photo library'}
    ]
    assert len(reminders.plan_reminders([album], existing, today=date(2026, 9, 12))) == 1
