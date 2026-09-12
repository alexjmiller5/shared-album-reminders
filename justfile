# Preview shared albums without creating tasks.
run:
    uv run scripts/shared_album_reminders.py --dry-run

dev: run

test:
    uv run pytest -q

check:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff check --fix .
    uv run ruff format .

build:
    nix build
