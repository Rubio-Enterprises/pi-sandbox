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
