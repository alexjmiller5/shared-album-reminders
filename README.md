# Shared Album Reminders

Plans reminders to copy each new shared photo album into the photo library,
due 28 days after album creation. If creation date is absent, use
the date the reminder task is first successfully created. Copying photos
remains manual.

**Current status:** the album reader and reminder planner work. Task writing
is not connected, so no reminder tasks are created. The default command exits
with a clear error; the scheduled module must remain disabled until the Life
task catalog is available and connected.

## Preview

```sh
nix run . -- --dry-run
nix run . -- --dry-run --library /path/to/Library.photoslibrary
```

The preview shows album candidates and due dates. It explicitly reports that
task deduplication has not been checked. It never writes to Photos or Life.
Reads include SQLite's active WAL, so recently synced album rows are visible.
The reader supports macOS 26's `CollectionShare` schema and fails on an
unsupported schema rather than reporting an empty inventory. Creation dates
are interpreted as UTC calendar dates. Discovery dates use the local date.

The focused query reads album metadata directly, following the layout used
by [osxphotos](https://github.com/RhetTbull/osxphotos/blob/main/osxphotos/photosdb/photosdb.py).
It does not load photo records or require osxphotos's media/export dependencies.

## Nix module

The flake exports `packages.<system>.default` and `homeModules.default`.
Import the Home Manager module into a macOS configuration. The package brings
its own Python and Life CLI; no separate dependency setup is needed.

```nix
# Flake input:
shared-album-reminders = {
  url = "github:alexjmiller5/shared-album-reminders";
  inputs.nixpkgs.follows = "nixpkgs";
};

# Home Manager imports:
imports = [ inputs.shared-album-reminders.homeModules.default ];

# Keep disabled until task integration is available:
services.shared-album-reminders.enable = false;
```

Once task integration is connected, `enable = true` installs a daily user
LaunchAgent at 09:00 local time. Optional `hour` and `library` settings change
the schedule or library. Logs go to `~/Library/Logs/shared-album-reminders.log`.
A native macOS privacy grant may be necessary to read the library. Access
must be checked from the installed LaunchAgent; a successful terminal or SSH
read does not prove access in that context. Reenroll Photos and Life through
their supported interfaces on a replacement machine.

## Remaining task integration

Connect one reader and writer to the actual migrated Life task catalog:

1. Read its table, column, required-field, status and reference contracts.
2. Query existing tasks through `life sql`, including completed tasks. Normalize
   each to `album_id` when available and `title`; pass them to `plan_reminders`.
3. Write new reminders through `life insert`, mapping to that real contract.
   Preserve the stable album identifier in the task's supported source
   reference. Never infer the schema, create a new task table, or bypass the CLI.
4. Re-read writes to verify them. Query again after any uncertain result before
   retrying. An existing task retains its due date, including discovery fallback.
5. Review initial candidates before enabling the schedule; existing old albums
   without matching tasks can yield overdue reminders. Test the installed
   launch context and native Life sync, then enable the daily job.

The task itself retains discovery-based due dates and is the deduplication
record. There is no separate album state file. Missing creation dates use the
first successful task creation; a failed run leaves no hidden receipt.
Retries on later days use that later date until a task exists. Once created,
the task retains its due date across subsequent scans and completion.

## Development

```sh
just test
just check
nix build
```

Tests cover album filtering, WAL visibility, date fallback, completed-task
matching, renamed albums, ambiguous titles, and refusing unavailable writes.
