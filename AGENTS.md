# Shared Album Reminders

A small macOS scheduled job for reminding users to copy shared albums into
their photo library. Photo copying is manual.

- `scripts/shared_album_reminders.py` reads only live `CollectionShare`
  metadata from the Photos SQLite database, through a read-only connection
  including the active WAL. It supports the macOS 26 schema.
- `plan_reminders` uses album creation date plus 28 days, or discovery date
  plus 28 days. Existing normalized task records must include every status,
  including completed tasks. Match album ID first, then the expected title;
  ambiguous title-only matches fail. Existing due dates are never updated.
- The task writer is unavailable because the destination task catalog
  contract has not been supplied. Default execution fails clearly;
  `--dry-run` displays candidates with `dedupe_checked: false`.
- The Nix package owns its Python and Life CLI dependencies. The exported
  Home Manager module owns the daily launchd job and defaults to disabled.
  Do not enable it until task integration and launch-context access pass.
- No album receipt files, local database writes, photo mutations, credentials,
  remote APIs, or working-tree runtime paths.
- Life is an intentional shared service accessed only through its supported
  installed CLI. Read/write task mapping belongs here, while the user's
  task catalog and native Life enrollment remain external user state.
- Native Photos access may require a macOS privacy grant. Validate in the
  actual launch context before adding any signing or wrapper mechanism.

Use `just test`, `just check`, and `nix build`. Tests use synthetic metadata,
never a real library or personal task records. Test behavior before changes.
