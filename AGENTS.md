# Shared Album Reminders

A macOS daily job discovers shared albums and creates one Soma reminder.
Photo copying remains manual.

- `scripts/shared_album_reminders.py` owns collection, planning, initialization,
  supported API transport and the CLI. Read Photos metadata only, including WAL.
- Preserve UUIDv5 identity: NAMESPACE_URL and raw
  `shared-album-reminders:album:<canonical Photos UUID>`. No occurrence key.
- Due dates are album creation +28 days, otherwise first successful creation
  +28 days. Failed writes leave no per-album receipt. Existing rows never change.
- Baseline and explicit adopted IDs live in the app's XDG state. Initialization
  never overwrites state; adopted missing targets fail closed without fallback.
- Runtime service configuration supplies mappings, policy and defaults. No
  personal content or private schema constants belong in source.
- Soma is an approved shared service. Use its supported narrow APIs and
  shared receipt/session validators, never its storage, other apps' credentials,
  broad grants or Notion. The installed CLI lacks create-only support.
- The session's scopes pick the task read: with `tables:read:<table>:hub_at`
  (exactness still holds, with or without it) `read_task_arrivals` applies
  `soma.table_pull` arrivals and tombstones to the XDG cache copy; otherwise
  `read_tasks` walks every page. Bump the soma pin in the script header,
  pyproject/uv.lock and flake.nix/flake.lock together.
- Credentials are native Keychain state bound to this caller and endpoint, or an
  explicit environment/command seam. No credential files or provider knowledge.
- The exported Home Manager module owns installation and launchd. Its dryRun
  option validates the actual installed context before live activation.
- Verify with `just test`, `just check` and Nix package/check builds. Synthetic
  tests precede behavior changes. Never use real Photos/task records as fixtures.
