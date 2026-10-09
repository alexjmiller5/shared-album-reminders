# Shared Album Reminders

A daily macOS job creates one Soma reminder when it discovers a shared
Photos album. The reminder asks you to copy the photos you want manually.
Its deadline is the album's creation date plus 28 days. If that date is absent,
the deadline is the first successful task-creation date plus 28 days.
Existing reminders are never edited, reset or recreated after completion.

## Setup

Install the flake package, then pass your service's creation descriptor to
`shared-album-reminders --configure` on stdin. The descriptor contains an HTTPS
`endpoint`, exact `policy` ID/revision, `table`, `title_column`, `due_column`,
and `defaults` object. No credentials or personal schemas are compiled in.
The service must configure a create-only singleton policy matching the stable
application identity described below, with atomic origin creation.

Enroll a separately minted, narrowly scoped token through
`shared-album-reminders --enroll-token` on stdin. It is stored in native Keychain
through Soma's credential library, under an application-specific account
bound to the endpoint. It is never written to a config file or command argument.
The app also accepts `SHARED_ALBUM_REMINDERS_TOKEN`, or a generic
`credential_command` argument array whose stdout supplies the token. The scheduled
context must explicitly have its own supported credential access. Operator,
replica and other applications' credentials are rejected.

Choose the initial backlog explicitly:

```sh
# Ignore the albums already present; remind only for later discoveries:
shared-album-reminders --baseline-existing
# Alternatively, include existing albums (some may immediately be overdue):
shared-album-reminders --initialize
```

Initialization is exclusive and never overwrites a baseline. State lives in
`$XDG_STATE_HOME/shared-album-reminders/state.json` (default `~/.local/state`).
It contains only baseline UUIDs and any retained `adopted` UUID-to-task-ID mappings,
not task-creation receipts or discovery deadlines. Preserve it when replacing a
machine; reenroll its credential through the supported interface. Without state,
the configured job fails closed. Adopted IDs always win, including tombstones;
a missing adopted task stops writes and never produces an alternate ID.

Runtime configuration lives in
`$XDG_CONFIG_HOME/shared-album-reminders/config.json` (default `~/.config`).
`--config`, `--state` and `--library` override the respective paths.
The default library is `~/Pictures/Photos Library.photoslibrary`.

## Preview and scheduling

```sh
shared-album-reminders --dry-run
```

With configuration, preview verifies the exact creation capability, reads all
pages of the permitted task projection including tombstones, and displays only
eligible candidates. Without configuration it previews Photos alone and labels
deduplication unchecked. No preview writes tasks.

The flake exports `packages.<system>.default` and `homeModules.default`.
Its Home Manager module installs a daily LaunchAgent at 09:00 local time:

```nix
imports = [ inputs.shared-album-reminders.homeModules.default ];
services.shared-album-reminders = {
  enable = true;
  dryRun = true; # Verify installed Photos and credential access first.
};
```

After a successful installed dry run, set `dryRun = false`. Optional `hour`,
`library` and `configFile` change the schedule and paths. The job is
`org.shared-album-reminders.daily`; logs are in
`~/Library/Logs/shared-album-reminders.log`. Nix owns installation and the job;
runtime code never points at a working tree. Native Photos privacy grants and
Keychain access must work from launchd, not just an interactive terminal.

## Service contract

The app uses supported Soma HTTP interfaces because the installed CLI has
no narrow create-only operation. It uses the shared Python `soma.creation`
receipt/session validators, pinned in uv and Nix, with standard-library transport.
It never uses direct service storage or a Notion fallback.

Required grants are exactly `rows:create:<policy-id>:<revision>` plus projected
reads of `id`, `deleted_at`, the configured title column and due column on the
configured table. No People or arbitrary provenance access is needed.
The service owns lineage and creates it atomically only on CREATED. EXISTING
preserves the entire row, history and provenance without attributing creation.
Malformed or uncertain receipts stop the run; a later run checks the same IDs.

Task IDs are lowercase 32-hex UUIDv5 with `NAMESPACE_URL` and raw UTF-8 name
`shared-album-reminders:album:<canonical-lowercase-hyphenated-Photos-UUID>`.
The service policy uses `occurrenceType: "none"` and
`identity: {encoding: "prefix-source-v1", prefix: "shared-album-reminders:album:"}`.
The request omits `occurrenceKey`; no null, sentinel, year or scan date is hashed.
The external source registry and task defaults are service/user state.

Reads use the macOS 26 Photos `CollectionShare` schema through a read-only SQLite
connection including active WAL. Photos are never copied or modified. Source
creation timestamps are interpreted as UTC dates; fallback uses the local day.
Unsupported schemas fail clearly. Date-only deadlines stay `YYYY-MM-DD`.

## Development

```sh
just test
just check
nix build .#checks.aarch64-darwin.tests .#packages.aarch64-darwin.default
```

Tests use synthetic Photos metadata and task records. Integration tests cover
baseline preservation, singleton IDs, narrow grants, pagination, tombstones,
adopted missing targets, receipt validation and retry deadlines.
