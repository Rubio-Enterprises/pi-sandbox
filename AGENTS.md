# Agent context

Read [docs/design.md](docs/design.md) before changing launch behavior; it is the
contract. Runtime dependencies are standard-library only. Keep user/host policy
in layers and examples generic.

Use `mise tasks` for gates. `lint` includes strict pyright; `test` includes
real-nono tests when available. Platform injection lives only in
`tests/entry.py`; the shipped CLI must never read test override variables.

Run probes only in isolated fixture HOME/XDG roots. Live fixtures belong under
the XDG cache directory's `pi-sandbox-tests/`, never the system temp directory.
Use synthetic credentials and sockets; never query a real Keychain or token.
Preserve the exact Seatbelt rule order and explicit-socket tmux cleanup.

## Commands

First command in a fresh checkout: `just build`.
Never install toolchains on the host — all project commands run in the dev image through `just`.

```bash
just build
just setup
just check
just fmt
just check-pr
just test
just typecheck
just setup-deps
just shell
just exec <cmd...>
```

Single test: `just exec uv run pytest path::test` (Python); otherwise prefix the underlying runner with `just exec`.
