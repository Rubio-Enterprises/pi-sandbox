# pi-sandbox

[![standard-readme](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg)](https://github.com/RichardLitt/standard-readme)

Run Pi under nono with layered, accident-prevention policy.

The engine assembles filesystem grants, isolated Git/Docker configuration,
credential bridges, a sandbox notice, and a Pi wrapper inherited by subagents.
Policy belongs in ordered TOML layers and optional native nono profiles.
Runtime code uses only the Python standard library.

## Table of Contents

- [Security](#security)
- [Install](#install)
- [Usage](#usage)
- [Configuration](#configuration)
- [Credentials](#credentials)
- [Hooks](#hooks)
- [Platforms and limits](#platforms-and-limits)
- [Contributing](#contributing)
- [License](#license)

## Security

This prevents accidental host damage, not an adversarial agent or prompt injection.
Layers and hooks are trusted executable policy. Review them before installing.
A denied operation means stop and ask for a wider launch, not find a detour.
Docker VM mounts can bypass host filesystem restrictions.

## Install

Prerequisites: Python 3.14.6+, [uv](https://docs.astral.sh/uv/),
[Pi](https://github.com/earendil-works/pi), Git, and
[nono](https://nono.sh) 0.80.0+. Tools are discovered through `PATH`.
From a source checkout:

```sh
uv tool install .
pi-sandbox-ctl setup
pi-sandbox-ctl doctor
```

Setup pulls the signed `nolabs-ai/pi@0.2.2` pack if needed and installs the
engine/layer profiles. Pack profile bytes must match nono's recorded SHA-256.
It sets the pack's Pi settings entry to `skills = []`,
so its sandbox-only skills do not load in ordinary Pi sessions. The launch
wrapper loads those skills inside nono. Other packs are preserved.

For development, `uv run pi-sandbox …` works without a tool installation.

## Usage

```sh
pi-sandbox --workdir=~/Code/example
pi-sandbox --scratch=investigation --add-dirs-ro=~/context -p "Inspect this project"
pi-sandbox --add-dirs ~/Code/other --exclude-tools=unsafe_* -- -p "Explain the code"
pi-sandbox-ctl profile --workdir=~/Code/example
```

The workdir must exist and cannot be HOME, its ancestor, or `/`.
On Linux, a Git project must be a linked worktree, not the main checkout.
Without layers, Docker and credential-store integrations are disabled.

Grant flags are consumed before Pi starts. `--add-dirs` and `--add-dirs-ro`
accept colon-separated directories. A literal `--`, including the separator,
passes all following arguments unchanged to Pi. Exclusions from layers,
`--exclude-tools`, and `-xt` become one de-duplicated Pi flag.
`--scratch` requires a layer's scratch root and a single effort name.

Exit status is Pi's status, 2 for a refused launch, or 128 + signal number.
Private launch, browser, and tmux directories are cleaned on exit, including
HUP/INT/TERM and terminal closure. Screenshots under the XDG state directory's
`agent-sandbox/artifacts/run.*/screenshots` are retained.

## Configuration

Layers live at `${XDG_CONFIG_HOME:-$HOME/.config}/pi-sandbox/layers/NN-name/`.
Override the engine root with `PI_SANDBOX_CONFIG_DIR`. Each directory contains
`layer.toml` and optionally `profile.json`. The whole engine config root and
credentials directory are denied inside nono.

Create `layers/50-user/layer.toml`:

```toml
schema = 1

[grants]
ro_dirs = ["~/context"]

[workspace]
scratch_root = "~/Code/.scratch"
worktree_root = "~/.local/share/git-worktrees/{repo}"
```

Paths support `~/`, `$HOME`, and all four XDG home variables with their
standard defaults. Layers merge lexically; tables merge, lists append uniquely,
and later scalars win. A platform table merges immediately after its layer's
common settings. Same-name hooks replace earlier hooks.
See the [three-layer example](examples/layers) and [complete contract](docs/design.md).

```sh
pi-sandbox-ctl layers
pi-sandbox-ctl config --json
pi-sandbox-ctl validate
pi-sandbox-ctl pack
```

Native fragments keep nono's syntax. They cannot set `extends`, `workdir`,
raw Seatbelt rules, or another profile name. Installed layer profiles are
content-addressed and installed without overwriting existing files. Launches
pin their immutable chains. `pi-sandbox-ctl setup` and `pi-sandbox-ctl prune`
remove only unreferenced profiles older than 24 hours; launches never prune.
Empty layer directories are ignored. Layer files/directories must be user-owned,
not symlinks, and not group/world-writable.

## Credentials

Provision read-only credentials outside the sandbox in the engine root's
`credentials/` directory (or set `credentials.dir`).
`pi-sandbox-github.token` supplies `GH_TOKEN` and Git's gh helper.
Without it, GitHub is disabled; inherited human tokens are not substituted.
`pi-sandbox-ghcr.token` supplies only Docker's `ghcr.io` auth entry.
Trailing newlines are stripped. Tokens never go into argv, profiles, notices,
wrappers, or Git configuration. The GitHub token covers one organization;
private repositories elsewhere answer 404 or ask for credentials by design.

Generic slots are opt-in:

```toml
[credentials.env.service]
file = "service.token"
env = "EXAMPLE_SERVICE_TOKEN"
set = { EXAMPLE_SERVICE_URL = "https://service.example" }
missing = "Service off: no token at {path}"
notice = "- Service access is read-only; ask the user to make changes."
```

Use `command` instead of `file` to run `sh -c` outside nono; its stderr is
discarded. Empty/missing values and failed commands leave the slot off.
Slot variables cannot be restored through `SANDBOX_ENV_PASS`.
The opt-in integrations are `credential-stores`, `onepassword`,
`cloud-credentials`, `keychain`, and `agent-mcp`.

## Hooks

```toml
[[hooks]]
name = "listener-inventory"
command = ["~/.local/libexec/pi-sandbox/listener-inventory"]
platforms = ["macos"]
```

Hooks run outside nono, before credentials, with a 30-second timeout and a
1 MiB limit per output stream. Hook executables must be outside all launch RW
grants, including resolved symlink targets. Each hook has a private process group;
timeout or interruption terminates that group and reaps its direct process. stdin carries schema, OS,
workdir, launch directory, and HOME as JSON. Success returns empty stdout or
`{"seatbelt_rules":["(deny …)"],"notice":["- Policy note."]}`.
Rules are macOS-only; notices work on both platforms.
Failure refuses the launch and includes the hook's stderr.
The controller's profile preview executes neither hooks nor credentials.

## Platforms and limits

- **macOS:** uses `nono wrap`, never `nono run`; the latter starts a
  debug-level unified-log stream that stalls Endpoint Security clients.
  Raw Seatbelt floors protect Git configuration/hooks, protected checkouts,
  Pi configuration/code, host sockets, and shared caches. `nono why` cannot
  see raw rules. Tokenless launches verify the resolved Keychain deny without
  accessing a Keychain.
- **Linux/WSL2:** requires kernel 6.12+ (Landlock ABI 6+).
  On WSL, update with `wsl --update` and keep grants off Windows drives.
  Git metadata is granted piecewise. Active `extensions.worktreeConfig`,
  broad grants over policy, and main-checkout launches are refused.
  Branch deletion, pack-refs, and worktree creation may need an outside shell.
- **Accepted Linux socket gap:** pathname Unix sockets are not isolated.
  Host tmux, ssh-agent, Docker, the user bus, and WSL interop may remain
  reachable. Do not use them to launch unsandboxed commands.
- Subagents use the private `TMUX_TMPDIR`, never Herdr panes or host tmux.
  This does not constrain commands started outside the sandbox.

## Contributing

Questions, bug reports, and pull requests are welcome through the repository's
issue tracker. Read [AGENTS.md](AGENTS.md) and [the design](docs/design.md)
before changing launch behavior. Use Conventional Commits.

```sh
mise bootstrap --only repos,tools
uv sync --frozen
mise run lint
mise run test
```

Unit tests and stub integrations run on either OS; only `tests/entry.py` can
inject platform/kernel facts. Production has no test override variables.
Live tests use a probe instead of Pi and fixture HOMEs under
`${XDG_CACHE_HOME:-$HOME/.cache}/pi-sandbox-tests/`, with a cached pinned pack.
They skip clearly if nono is absent. They never use real tokens, configuration,
or Keychains. CI runs unit/integration on Linux and macOS.

## License

[MIT](LICENSE), copyright 2026 Rubio Enterprises.
