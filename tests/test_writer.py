import copy
import json
from datetime import UTC, date, datetime

import pytest
from test_reminders import reminders as r

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2030, 1, 1, tzinfo=UTC)
POLICY = {"id": "fixture", "revision": "a" * 64}
CONFIG = {
    "endpoint": "https://hub.test",
    "policy": POLICY,
    "table": "items",
    "title_column": "label",
    "due_column": "due",
    "defaults": {},
}
STATE = {"version": 1, "baseline": [], "adopted": {}}


CURSOR = "tables:read:items:hub_at"


class Client:
    def __init__(self, rows=None, valid=True, cursor=False):
        self.rows = rows or []
        self.valid = valid
        # Whether the credential carries the hub_at grant the arrival cursor needs.
        self.cursor = cursor
        self.calls = []

    def request(self, path, body=None):
        self.calls.append((path, body))
        if path == "/v1/session":
            scopes = r.expected_scopes(CONFIG) + ([CURSOR] if self.cursor else [])
            return {
                "status": 200,
                "data": {
                    "scopes": scopes if self.valid else ["full"],
                    "capabilities": {
                        "rowCreation": {"protocol": "atomic-origin-v1", "policies": [POLICY]}
                    },
                },
            }
        if path == "/v1/cursor":
            if not self.cursor:
                return {"status": 403, "data": None}
            mark = max((row["hub_at"] for row in self.rows), default="")
            at_mark = sum(row["hub_at"] == mark for row in self.rows)
            return {
                "status": 200,
                "data": {
                    "tables": {"items": mark},
                    "at_mark": {"items": at_mark} if mark else {},
                    "pull_batch": {"items": 50, "rows": 5000},
                },
            }
        if path == "/v1/rows/pull" and "batch" in body:
            (item,) = body["batch"]
            rows = sorted(
                (row for row in self.rows if row["hub_at"] >= item["since"]),
                key=lambda row: row["id"],
            )
            page = [{c: row.get(c) for c in item["columns"]} for row in rows]
            return {"status": 200, "data": {"batch": [{"rows": page, "next_cursor": None}]}}
        if path == "/v1/rows/pull":
            return {"status": 200, "data": {"rows": self.rows, "next_cursor": None}}
        if path == "/v1/rows/create":
            assert "occurrenceKey" not in body
            return {
                "status": 200,
                "data": {
                    "kind": "created",
                    "policy": POLICY,
                    "id": body["target"]["id"],
                    "revision": {"updated_at": body["updatedAt"], "hub_at": body["updatedAt"]},
                    "originId": "fixture:" + body["sourceId"] + ":" + body["target"]["id"],
                },
            }
        raise AssertionError(path)


def run(client, *, albums=None, state=None, dry=False, today=date(2030, 1, 1), cache=None):
    return r.run_reminders(
        albums or [r.Album(A, "Example", None)],
        CONFIG,
        state or STATE,
        client,
        today=today,
        now=NOW,
        dry_run=dry,
        cache=cache,
    )


def test_baseline_stores_canonical_ids_once_and_survives_rename(tmp_path):
    path = tmp_path / "state.json"
    r.initialize_state(path, [r.Album(A.upper(), "Old title", None)], baseline_existing=True)
    state = json.loads(path.read_text())
    assert state == {"version": 1, "baseline": [A], "adopted": {}}
    assert run(Client(), state=state)["candidates"] == []
    with pytest.raises(FileExistsError):
        r.initialize_state(path, [], baseline_existing=False)
    assert json.loads(path.read_text()) == state


def test_writer_uses_fixed_id_date_only_and_no_occurrence():
    client = Client()
    result = run(client)
    assert result["created"] == 1
    body = client.calls[-1][1]
    assert body["target"] == {"kind": "generated", "id": r.task_id_for_album(A)}
    assert body["sourceId"] == A
    assert body["values"] == {
        "label": 'Copy shared album "Example" to the photo library',
        "due": "2030-01-29",
    }
    assert "occurrenceKey" not in body


def test_existing_completed_or_tombstoned_id_never_written():
    for deleted in (None, "2029-01-01"):
        client = Client(
            [
                {
                    "id": r.task_id_for_album(A),
                    "label": "renamed",
                    "due": "2020-01-01",
                    "deleted_at": deleted,
                }
            ]
        )
        assert run(client)["candidates"] == []
        assert all(path != "/v1/rows/create" for path, _ in client.calls)


def test_missing_adopted_target_blocks_without_generated_fallback():
    client = Client()
    with pytest.raises(ValueError, match="adopted"):
        run(client, state={**STATE, "adopted": {A: "historical"}})
    assert all(path != "/v1/rows/create" for path, _ in client.calls)


def test_tombstoned_adopted_target_is_preserved_without_write():
    client = Client([{"id": "historical", "label": "old", "due": None, "deleted_at": "2020-01-01"}])
    assert run(client, state={**STATE, "adopted": {A: "historical"}})["candidates"] == []
    assert len(client.calls) == 2


def test_broad_credentials_fail_before_reads_or_writes():
    client = Client(valid=False)
    with pytest.raises(ValueError, match="credential"):
        run(client)
    assert [p for p, _ in client.calls] == ["/v1/session"]


def test_dry_run_authenticates_reads_and_never_writes():
    client = Client()
    result = run(client, dry=True)
    assert result["dedupe_checked"] is True
    assert len(result["candidates"]) == 1
    assert len(client.calls) == 2


def test_incomplete_or_malformed_read_never_writes():
    for reply in (
        {"rows": []},
        {"rows": [], "next_cursor": "nonadvancing"},
        {"rows": [{}], "next_cursor": None},
    ):
        client = Client()
        orig = client.request
        client.request = lambda path, body=None: (
            {"status": 200, "data": reply} if path.endswith("pull") else orig(path, body)
        )
        with pytest.raises(ValueError):
            run(client)
        assert all(path != "/v1/rows/create" for path, _ in client.calls)


def test_lost_write_response_leaves_no_receipt_and_later_due_uses_success_day():
    client = Client()
    original = client.request

    def fail(path, body=None):
        if path.endswith("create"):
            return {"status": 503, "data": {}}
        return original(path, body)

    client.request = fail
    state = copy.deepcopy(STATE)
    with pytest.raises(ValueError, match="unconfirmed"):
        run(client, state=state)
    assert state == STATE
    success = Client()
    run(success, today=date(2030, 1, 3))
    assert success.calls[-1][1]["values"]["due"] == "2030-01-31"


def test_same_title_new_album_is_not_suppressed_by_baseline():
    client = Client()
    result = run(
        client,
        albums=[r.Album(A, "Example", None), r.Album(B, "Example", None)],
        state={**STATE, "baseline": [A]},
    )
    assert result["created"] == 1
    assert client.calls[-1][1]["sourceId"] == B


def test_transport_refuses_redirect_and_invalid_endpoint(monkeypatch):
    monkeypatch.setenv("SHARED_ALBUM_REMINDERS_TOKEN", "synthetic")
    for url in [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test?token=x",
    ]:
        with pytest.raises(ValueError, match="endpoint"):
            r.SomaClient({"endpoint": url})
    with pytest.raises(ValueError, match="redirect"):
        r.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test")


def test_pagination_reads_all_pages_before_creating():
    client = Client()
    original = client.request
    requests = []

    def pages(path, body=None):
        if path.endswith("pull"):
            requests.append(body)
            if "after" not in body:
                return {
                    "status": 200,
                    "data": {
                        "rows": [{"id": "a", "label": "other", "due": None, "deleted_at": None}],
                        "next_cursor": "a",
                    },
                }
            return {
                "status": 200,
                "data": {
                    "rows": [{"id": "b", "label": "other", "due": None, "deleted_at": None}],
                    "next_cursor": None,
                },
            }
        return original(path, body)

    client.request = pages
    assert run(client)["created"] == 1
    assert requests[1]["after"] == "a"


def test_configuration_is_exclusive_and_never_persists_a_token(tmp_path):
    path = tmp_path / "config.json"
    config = {**CONFIG, "endpoint": "https://example.test"}
    with pytest.raises(ValueError):
        r.configure(path, {**config, "token": "secret"})
    assert not path.exists()
    r.configure(path, config)
    assert json.loads(path.read_text()) == config
    with pytest.raises(FileExistsError):
        r.configure(path, config)


def test_transport_sends_product_user_agent_and_bounds_response(monkeypatch):
    monkeypatch.setenv("SHARED_ALBUM_REMINDERS_TOKEN", "synthetic")
    client = r.SomaClient({"endpoint": "https://example.test"})

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("User-agent") == "shared-album-reminders/0.1.0"
            assert request.get_header("Authorization") == "Bearer synthetic"
            return Response()

    client.opener = Opener()
    assert client.request("/v1/session") == {"status": 200, "data": {}}

    # A full 5,000-row page can pass 2 MB; the bound follows the hub's 4 MiB batch.
    page = b'{"rows": "' + b"a" * 3_000_000 + b'"}'
    Response.read = lambda self, limit: page[:limit]
    assert client.request("/v1/rows/pull")["status"] == 200
    page = b'{"rows": "' + b"a" * 5_000_000 + b'"}'
    with pytest.raises(ValueError, match="size bound"):
        client.request("/v1/rows/pull")


T1, T2 = "2030-01-01T00:00:00.000Z", "2030-01-02T00:00:00.000Z"


def task(id, hub_at, deleted_at=None):
    return {"id": id, "label": "other", "due": None, "deleted_at": deleted_at, "hub_at": hub_at}


def paths(client):
    return [path for path, _ in client.calls]


def test_cursor_grant_reads_only_arrivals_and_keeps_tombstones(tmp_path):
    cache = tmp_path / "task-pull.json"
    client = Client([task("x", T1)], cursor=True)
    assert run(client, cache=cache)["created"] == 1
    assert paths(client) == ["/v1/session", "/v1/cursor", "/v1/rows/pull", "/v1/rows/create"]
    assert client.calls[2][1]["batch"][0]["since"] == ""

    # The created reminder was deleted since; its tombstone must still dedupe.
    client = Client([task("x", T1), task(r.task_id_for_album(A), T2, T2)], cursor=True)
    assert run(client, cache=cache)["candidates"] == []
    assert paths(client) == ["/v1/session", "/v1/cursor", "/v1/rows/pull"]
    assert client.calls[2][1]["batch"][0]["since"] == T1

    # Nothing moved: one cursor request, and the held copy still dedupes.
    client = Client([task(r.task_id_for_album(A), T2, T2)], cursor=True)
    assert run(client, cache=cache)["candidates"] == []
    assert paths(client) == ["/v1/session", "/v1/cursor"]
    held = json.loads(cache.read_text())
    assert sorted(row["id"] for row in held["rows"]) == sorted([r.task_id_for_album(A), "x"])


def test_without_the_hub_at_grant_the_whole_table_is_read_and_nothing_cached(tmp_path):
    cache = tmp_path / "task-pull.json"
    client = Client([task(r.task_id_for_album(A), T1)])
    assert run(client, cache=cache)["candidates"] == []
    assert paths(client) == ["/v1/session", "/v1/rows/pull"]
    assert "batch" not in client.calls[1][1]
    assert not cache.exists()


def test_an_unreadable_copy_is_dropped_for_a_full_pull(tmp_path):
    cache = tmp_path / "task-pull.json"
    cache.write_text("{not json")
    client = Client([task(r.task_id_for_album(A), T1)], cursor=True)
    assert run(client, cache=cache)["candidates"] == []
    assert client.calls[2][1]["batch"][0]["since"] == ""


def test_hub_at_grant_does_not_relax_scope_exactness():
    client = Client(cursor=True)
    original = client.request

    def extra(path, body=None):
        reply = original(path, body)
        if path == "/v1/session":
            reply["data"]["scopes"].append("tables:read:items:notes")
        return reply

    client.request = extra
    with pytest.raises(ValueError, match="credential"):
        run(client)
    assert paths(client) == ["/v1/session"]


def test_a_refused_cursor_read_never_writes():
    client = Client([task("x", T1)], cursor=True)
    original = client.request
    client.request = lambda path, body=None: (
        {"status": 500, "data": None} if path == "/v1/rows/pull" else original(path, body)
    )
    with pytest.raises(RuntimeError, match="HTTP 500"):
        run(client)
    assert "/v1/rows/create" not in paths(client)
