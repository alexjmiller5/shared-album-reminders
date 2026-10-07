#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["life-data @ git+https://github.com/alexjmiller5/life-data@22daeac57fd9a821d70fba468daa8a081b7d98c3"]
# ///
"""Create one reminder per newly discovered shared album."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5


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


def task_id_for_album(album_id: str) -> str:
    """One task per Photos UUID, independent of name, scan date and credentials."""
    canonical = str(UUID(album_id))
    return uuid5(NAMESPACE_URL, f"shared-album-reminders:album:{canonical}").hex


def prepare_task_inserts(
    albums: list[Album],
    existing_tasks: list[dict],
    *,
    title_column: str,
    due_column: str,
    defaults: dict,
    today: date,
    now: datetime,
    legacy_tasks: list[dict] | None = None,
) -> list[dict]:
    """Prepare rows only; no IO and no authorization to write.

    The caller supplies complete reads (including tombstones), runtime field
    mappings/defaults, and explicitly reviewed legacy title-match records.
    Submit candidates only through the service's create-only insert primitive;
    a snapshot check cannot guarantee uniqueness against concurrent creation.
    """
    reserved = {"id", "created_at", "updated_at", "deleted_at", "hub_at"}
    if (
        not title_column
        or not due_column
        or title_column == due_column
        or {title_column, due_column} & reserved
        or defaults.keys() & (reserved | {title_column, due_column})
    ):
        raise ValueError("Task mapping/defaults conflict with adapter-owned fields.")
    if now.utcoffset() is None:
        raise ValueError("The insertion timestamp must have an explicit timezone.")

    ids = {album.album_id: task_id_for_album(album.album_id) for album in albums}
    if len(set(ids.values())) != len(albums):
        raise ValueError("The album inventory contains duplicate UUIDs.")
    legacy_tasks = legacy_tasks or []
    for row in [*existing_tasks, *legacy_tasks]:
        if (
            not isinstance(row.get("id"), str)
            or not row["id"]
            or not isinstance(row.get(title_column), str)
            or not row[title_column]
        ):
            raise ValueError("Task read omitted a valid ID or configured title field.")
    existing_ids = {row["id"] for row in [*existing_tasks, *legacy_tasks]}
    normalized = [
        {"album_id": album_id} for album_id, task_id in ids.items() if task_id in existing_ids
    ]
    normalized.extend({"title": row[title_column]} for row in legacy_tasks)
    planned = plan_reminders(albums, normalized, today=today)
    stamp = now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return [
        {
            **deepcopy(defaults),
            "id": ids[reminder.album_id],
            title_column: reminder.title,
            due_column: reminder.due_on.isoformat(),
            "updated_at": stamp,
        }
        for reminder in planned
    ]


def initialize_state(path: Path, albums: list[Album], *, baseline_existing: bool) -> None:
    """Record an explicit baseline decision once, never task-creation receipts."""
    state = {
        "version": 1,
        "baseline": sorted({str(UUID(a.album_id)) for a in albums}) if baseline_existing else [],
        "adopted": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally baselining newly discovered albums on reenrollment.
    with path.open("x") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def configure(path: Path, config: dict) -> None:
    required = {"endpoint", "policy", "table", "title_column", "due_column", "defaults"}
    if (
        not isinstance(config, dict)
        or not required.issubset(config)
        or set(config) - required - {"credential_command"}
    ):
        raise ValueError("Configuration requires a service descriptor, never a stored token.")
    if not isinstance(config["defaults"], dict):
        raise ValueError("Creation defaults must be an object.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def expected_scopes(config: dict) -> list[str]:
    policy = config["policy"]
    columns = {"id", "deleted_at", config["title_column"], config["due_column"]}
    return [f"rows:create:{policy['id']}:{policy['revision']}"] + [
        f"tables:read:{config['table']}:{column}" for column in sorted(columns)
    ]


def read_tasks(client, config: dict) -> list[dict]:
    columns = sorted({"id", "deleted_at", config["title_column"], config["due_column"]})
    rows, seen = [], set()
    after = None
    for _ in range(1000):
        query = {"table": config["table"], "columns": columns, "limit": 200}
        if after is not None:
            query["after"] = after
        response = client.request("/v1/rows/pull", query)
        data = response.get("data")
        if response.get("status") != 200 or not isinstance(data, dict):
            raise ValueError("Task read failed; no writes attempted.")
        batch = data.get("rows")
        if not isinstance(batch, list) or "next_cursor" not in data or len(batch) > 200:
            raise ValueError("Incomplete task read; no writes attempted.")
        for row in batch:
            if (
                not isinstance(row, dict)
                or not set(columns).issubset(row)
                or not isinstance(row["id"], str)
                or not row["id"]
                or row["id"] in seen
                or (after is not None and row["id"] <= after)
            ):
                raise ValueError("Invalid task read; no writes attempted.")
            seen.add(row["id"])
            rows.append(row)
            after = row["id"]
        cursor = data["next_cursor"]
        if cursor is None:
            return rows
        if not batch or cursor != after:
            raise ValueError("Incomplete task pagination; no writes attempted.")
    raise ValueError("Task read exceeded its bound; no writes attempted.")


def run_reminders(albums, config, state, client, *, today, now, dry_run):
    from life_data.creation import validate_creation_receipt, validate_creation_session

    if not validate_creation_session(
        client.request("/v1/session"), config["policy"], expected_scopes(config)
    ):
        raise ValueError("Life credential does not have exactly the configured narrow grants.")
    if set(state) != {"version", "baseline", "adopted"} or state["version"] != 1:
        raise ValueError("Invalid baseline state; initialization is required.")
    if not isinstance(state["baseline"], list) or not isinstance(state["adopted"], dict):
        raise ValueError("Invalid baseline/adoption state.")
    baseline = {str(UUID(value)) for value in state["baseline"]}
    adopted = {str(UUID(key)): value for key, value in state["adopted"].items()}
    if len(adopted) != len(state["adopted"]) or any(
        not isinstance(value, str) or not value for value in adopted.values()
    ):
        raise ValueError("Invalid adopted task identities.")
    normalized = [Album(str(UUID(a.album_id)), a.title, a.created_on) for a in albums]
    if len({a.album_id for a in normalized}) != len(normalized):
        raise ValueError("Duplicate album UUIDs.")
    tasks = read_tasks(client, config)
    task_ids = {task["id"] for task in tasks}
    for album in normalized:
        if album.album_id in adopted and adopted[album.album_id] not in task_ids:
            raise ValueError("An adopted task is missing; no alternate task will be created.")
    eligible = [a for a in normalized if a.album_id not in baseline and a.album_id not in adopted]
    candidates = prepare_task_inserts(
        eligible,
        tasks,
        title_column=config["title_column"],
        due_column=config["due_column"],
        defaults=config.get("defaults", {}),
        today=today,
        now=now,
    )
    result = {
        "dedupe_checked": True,
        "albums": len(albums),
        "baselined": len(baseline),
        "candidates": candidates,
        "created": 0,
        "existing": 0,
        "dry_run": dry_run,
    }
    sources = {task_id_for_album(a.album_id): a.album_id for a in eligible}
    if not dry_run:
        for row in candidates:
            values = {
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
                if key not in {"id", "updated_at"}
            }
            request = {
                "policy": config["policy"],
                "sourceId": sources[row["id"]],
                "target": {"kind": "generated", "id": row["id"]},
                "updatedAt": row["updated_at"],
                "values": values,
            }
            receipt = validate_creation_receipt(request, client.request("/v1/rows/create", request))
            if receipt is None:
                raise ValueError(
                    "Task creation is unconfirmed. Rerun safely to check the same IDs."
                )
            result[receipt["kind"]] += 1
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Life redirect refused; credentials were not forwarded.")


class LifeClient:
    def __init__(self, config):
        from life_data.credentials import read_token

        endpoint = config["endpoint"].rstrip("/")
        url = urllib.parse.urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Life endpoint must be an HTTPS URL without credentials or query.")
        token = os.environ.get("SHARED_ALBUM_REMINDERS_TOKEN")
        if not token and config.get("credential_command"):
            command = config["credential_command"]
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(x, str) for x in command)
            ):
                raise ValueError("Credential command must be an argument array.")
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            if proc.returncode:
                raise ValueError("Credential command failed.")
            token = proc.stdout.strip()
        if not token:
            token = read_token(credential_account(endpoint), interactive=False)
        if not token or any(char in token for char in "\r\n"):
            raise ValueError("Enroll the dedicated album credential before running.")
        self.endpoint, self.token = endpoint, token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, body=None):
        request = urllib.request.Request(
            self.endpoint + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "shared-album-reminders/0.1.0",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("Life response exceeded its size bound.")
                return {"status": response.status, "data": json.loads(raw)}
        except urllib.error.HTTPError as error:
            return {"status": error.code, "data": None}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise ValueError("Life request failed; task state may be unconfirmed.") from None


def credential_account(endpoint):
    import hashlib

    return "shared-album-reminders:" + hashlib.sha256(endpoint.rstrip("/").encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library", type=Path, default=Path.home() / "Pictures/Photos Library.photoslibrary"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview candidates without writes.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "shared-album-reminders/config.json",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        / "shared-album-reminders/state.json",
    )
    parser.add_argument(
        "--baseline-existing",
        action="store_true",
        help="Initialize once, excluding current albums from reminders.",
    )
    parser.add_argument(
        "--initialize", action="store_true", help="Initialize without excluding existing albums."
    )
    parser.add_argument(
        "--enroll-token",
        action="store_true",
        help="Store a dedicated credential from stdin in native Keychain.",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Store a service descriptor from stdin without credentials.",
    )
    args = parser.parse_args()
    try:
        if args.configure:
            configure(args.config, json.load(sys.stdin))
            print("Service configured. Enroll its dedicated credential separately.")
            return 0
        if args.enroll_token:
            from life_data.credentials import store_token

            config = json.loads(args.config.read_text())
            store_token(credential_account(config["endpoint"]), sys.stdin.read().strip())
            print("Dedicated credential enrolled.")
            return 0
        if args.baseline_existing or args.initialize:
            initialize_state(
                args.state, read_albums(args.library), baseline_existing=args.baseline_existing
            )
            print("Baseline initialized. Existing state is never overwritten.")
            return 0
        if args.config.exists():
            config = json.loads(args.config.read_text())
            state = json.loads(args.state.read_text())
            result = run_reminders(
                read_albums(args.library),
                config,
                state,
                LifeClient(config),
                today=date.today(),
                now=datetime.now(UTC),
                dry_run=args.dry_run,
            )
            print(json.dumps(result, indent=2))
            return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"Shared album reminders failed: {error}", file=sys.stderr)
        return 1
    if not args.dry_run:
        print(
            "Task writing is unavailable until the create-only Life task contract is connected. "
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
