#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Read shared album metadata and preview reminders. Task writing is not connected."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Album:
    album_id: str
    title: str
    created_on: date | None


@dataclass(frozen=True)
class Reminder:
    album_id: str
    title: str
    due_on: date
    date_basis: str


def read_albums(library: Path) -> list[Album]:
    """Read macOS 26 CollectionShare rows, including the active SQLite WAL."""
    path = (library.expanduser() / "database/Photos.sqlite").resolve(strict=True)
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        if not connection.execute(
            "SELECT 1 FROM Z_PRIMARYKEY WHERE Z_NAME = 'CollectionShare'"
        ).fetchone():
            raise ValueError("Photos schema is unsupported: CollectionShare entity is absent.")
        rows = connection.execute("""
            SELECT s.ZUUID, s.ZTITLE, s.ZCREATIONDATE
            FROM ZSHARE s
            JOIN Z_PRIMARYKEY entity ON entity.Z_ENT = s.Z_ENT
            WHERE entity.Z_NAME = 'CollectionShare'
                AND COALESCE(s.ZTRASHEDSTATE, 0) = 0
                AND COALESCE(s.ZCLOUDDELETESTATE, 0) = 0
            ORDER BY s.ZUUID
        """).fetchall()
    finally:
        connection.close()
    albums = []
    for album_id, title, created in rows:
        if not album_id or not title:
            raise ValueError("A shared album is missing its stable ID or title.")
        created_on = None
        if created is not None and created > 0:
            created_on = (datetime(2001, 1, 1, tzinfo=UTC) + timedelta(seconds=created)).date()
        albums.append(Album(album_id, title, created_on))
    return albums


def plan_reminders(
    albums: list[Album], existing_tasks: list[dict], *, today: date
) -> list[Reminder]:
    """Accept normalized task records from every status; never move an existing due date."""
    existing_ids = {task.get("album_id") for task in existing_tasks}
    existing_titles = {task.get("title") for task in existing_tasks if not task.get("album_id")}
    counts = Counter(album.title for album in albums)
    reminders = []
    for album in albums:
        if album.album_id in existing_ids:
            continue
        title = f'Copy shared album "{album.title}" to the photo library'
        if title in existing_titles:
            if counts[album.title] > 1:
                raise ValueError(f"Title-only task match is ambiguous for album {album.album_id}.")
            continue
        reminders.append(
            Reminder(
                album.album_id,
                title,
                (album.created_on or today) + timedelta(days=28),
                "creation" if album.created_on else "discovery",
            )
        )
    return reminders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library", type=Path, default=Path.home() / "Pictures/Photos Library.photoslibrary"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview candidates without writes.")
    args = parser.parse_args()
    if not args.dry_run:
        print(
            "Task writing is unavailable until the migrated Life task contract is connected. "
            "Use --dry-run to inspect album candidates.",
            file=sys.stderr,
        )
        return 2
    try:
        albums = read_albums(args.library)
        reminders = plan_reminders(albums, [], today=date.today())
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Cannot read shared albums: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "task_writing_available": False,
                "dedupe_checked": False,
                "albums": len(albums),
                "candidates": [asdict(reminder) for reminder in reminders],
            },
            default=str,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
