# Agent context

## Commands

Run every project command through `just`, which runs it in this repository's dev image; the host carries no project toolchain.

In a fresh checkout: `just build`, then `just setup`. `just --list` shows the recipes.

Single test: `just exec uv run --frozen pytest tests/unit/test_arguments.py`.
`just test` writes executed JUnit to `reports/test/`.

Real-nono verification is a host-only run: on a host with uv, Python 3.14.6+ and nono
0.80.0+, run `uv run --frozen pytest tests/live`. The dev image has no nono and would skip it.

## Launch invariants

Read [docs/design.md](docs/design.md) before changing launch behavior; it is the
contract. Runtime dependencies are standard-library only. Keep user/host policy
in native Sandbox Profiles and optional Pi sidecars; keep examples generic.

Use `just check` for gates and `just typecheck` for strict pyright. `just test` runs unit and
stub integrations. Platform injection lives only in `tests/entry.py`; the shipped CLI must never
read test override variables.

Run probes only in isolated fixture HOME/XDG roots. Live fixtures belong under
the XDG cache directory's `pi-sandbox-tests/`, never the system temp directory.
Use synthetic credentials and sockets; never query a real Keychain or token.
Preserve the exact Seatbelt rule order and explicit-socket tmux cleanup.

- **Architecture decisions**: an ADR records why a rule exists. Before changing a decision, check
  `docs/adr/` for an accepted ADR; reversing one needs a superseding ADR. Write one only if a
  person approved it: in the session, or in an approved plan or `spec:agent` issue that
  names the ADR. Otherwise the PR description is the record. Follow
  [the format](docs/adr/0001-record-architecture-decisions.md), which states the ADR test.
