# pi-sandbox

[![standard-readme](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg)](https://github.com/RichardLitt/standard-readme)

Run Pi under nono with native Sandbox Profiles and accident-prevention policy.

The engine assembles filesystem grants, isolated Git/Docker configuration,
credential bridges, a sandbox notice, and a Pi wrapper inherited by subagents.
A Sandbox Profile is a native nono JSON file; an optional Pi sidecar holds only
Pi settings and launch-time integrations.
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
Sandbox Profiles, Pi sidecars and hooks are trusted executable policy. Review them before installing.
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
engine base and selected Sandbox Profile snapshots. Pack nono profile bytes must match nono's recorded SHA-256.
It sets the pack's Pi settings entry to `skills = []`,
so its sandbox-only skills do not load in ordinary Pi sessions. The launch
wrapper loads those skills inside nono. Other packs are preserved.

For development, `uv run pi-sandbox …` works without a tool installation.

## Usage

```sh
pi-sandbox --profile pi-machine --workdir=~/Code/example
pi-sandbox --scratch=investigation --add-dirs-ro=~/context -p "Inspect this project"
pi-sandbox --add-dirs ~/Code/other --exclude-tools=unsafe_* -- -p "Explain the code"
pi-sandbox-ctl profile --workdir=~/Code/example
```

The workdir must exist and cannot be HOME, its ancestor, or `/`.
On Linux, a Git project must be a linked worktree, not the main checkout.
Without a selected Sandbox Profile, Docker and credential-store integrations are disabled.

Grant flags are consumed before Pi starts. `--add-dirs` and `--add-dirs-ro`
accept colon-separated directories. A literal `--`, including the separator,
passes all following arguments unchanged to Pi. Exclusions from Pi sidecars,
`--exclude-tools`, and `-xt` become one de-duplicated Pi flag.
`--scratch` requires a Pi sidecar's scratch root and a single effort name.

Exit status is Pi's status, 2 for a refused launch, or 128 + signal number.
Private launch, browser, and tmux directories are cleaned on exit, including
HUP/INT/TERM and terminal closure. Screenshots under the XDG state directory's
`agent-sandbox/artifacts/run.*/screenshots` are retained.

## Configuration

A **Sandbox Profile** is a native nono JSON file at
`${XDG_CONFIG_HOME:-$HOME/.config}/nono/profiles/<name>.json`.
Compose Sandbox Profiles using nono's own `extends`; built-in nono profiles and
nono packs can be bases too. The engine's base is added automatically: user JSON
never names it.

Selection: `pi-sandbox --profile <name>` (also `--profile=<name>`) >
`PI_SANDBOX_PROFILE` > engine base alone. A missing selected JSON file refuses.
The engine never reads or sets `NONO_PROFILE`. A literal `--` stops engine flag
parsing, so a later `--profile` goes to Pi.

Create `nono/profiles/pi-user.json` under the XDG config directory:

```json
{
  "meta": { "name": "pi-user" },
  "filesystem": { "read": ["~/context"] },
  "environment": {
    "allow_vars": ["EXAMPLE_REGION"],
    "deny_vars": ["EXAMPLE_ADMIN_*"],
    "set_vars": { "DO_NOT_TRACK": "1" }
  }
}
```

Create `nono/profiles/pi-machine.json`:

```json
{
  "meta": { "name": "pi-machine" },
  "extends": ["pi-user"]
}
```

An optional same-name **Pi sidecar** holds only settings nono cannot express or
the engine must act on at launch:
`${PI_SANDBOX_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/pi-sandbox}/profiles/<name>.toml`.
For example, `pi-sandbox/profiles/pi-user.toml`:

```toml
[workspace]
scratch_root = "~/Code/.scratch"
worktree_root = "$XDG_DATA_HOME/git-worktrees/{repo}"

[pi.mcp.example]
hide_tools = ["delete-*", "write.file"]
names = ["local-example"]

[notice]
lines = ["- Example service access is read-only."]
```

Sidecars support `pi`, `notice`, `credentials`, `workspace`, launch-dependent
`grants`, `docker.socket`, `integrations` and `hooks`. Every setting can be
overridden under `platform.macos` / `platform.linux`. There is no schema/version
key. Unknown fields refuse, with a native-field replacement hint where available.
See the [exact schema and migration mapping](docs/design.md#pi-sidecar-schema)
and [composed example pairs](examples/profiles).

The engine reads only the selected user inheritance graph. Sidecars merge
depth-first, left-to-right; each file's platform block follows its common block.
Tables merge, lists append uniquely and later scalars win; same-name hooks replace.
Shared ancestors are revisited for each sibling, matching nono. Non-user bases
have no sidecar. Sidecar paths support `~/`, `$HOME` and the four XDG home
variables. Native JSON expansion and native policy merging belong to nono.

Put filesystem grants/denies and bypasses in native `filesystem`, environment
policy in native `environment`, and static macOS port rules in
`platform_overrides.macos.unsafe_macos_seatbelt_rules`. Native `deny_vars`
supports `*` patterns and filters all inherited values, not only optional
passthrough. Explicit sidecar credential bridges are injected after filtering.
Linux cannot exactly express a remote-port deny with a localhost exception;
there is no automatic translation. See [native mappings](docs/design.md#moving-native-policy-into-json).
The retired numbered-directory format is not read.

MCP logical keys and aliases match `[A-Za-z0-9_-]+`. Server-local tool patterns
may use `*`; empty patterns, whitespace, commas and controls refuse. Pi naming
replaces punctuation with `_`, so the example hides `mcp__example__delete_*`
and `mcp__example__write_file`, and their local-example equivalents.
Use `pi.exclude_tools` for full Pi patterns, including non-MCP tools.
All exclusions merge with CLI patterns into one de-duplicated flag.

```sh
export PI_SANDBOX_PROFILE=pi-machine
pi-sandbox-ctl chain
pi-sandbox-ctl config --json
pi-sandbox-ctl validate
pi-sandbox-ctl profile --workdir=~/Code/example
pi-sandbox-ctl doctor --workdir ~/Code/example
```

All controller commands accept `--profile <name>`. `config --json` prints the
ordered chain, merged sidecar, sidecar paths and `rendered_exclude_tools`.
`profile` prints launch nono JSON on stdout and the launch-local prefix,
invocation and Pi wrapper on stderr. Preview installs immutable native
snapshots, but creates no launch/runtime roots and executes no hooks or credentials.

Doctor checks names in `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/mcp.json`
and, with `--workdir`, the project's `.pi/mcp.json` (project entries override).
Missing/unparsable files and unmatched logical server aliases warn without
changing policy or exit status. Extension-registered servers are not visible.
The launcher never reads Pi's MCP configuration.

The entire engine config root, including `credentials/` and `host/`, is denied
inside nono. Selected JSON files and sidecars must be user-owned, non-symlink,
not group/world-writable and have exactly one hard link. Reachable user JSON
ancestors are installed as immutable content-addressed snapshots, with parent
references pinned to their snapshots. The full graph is locked during launch.
`setup` and `prune` remove only unreferenced snapshots older than 24 hours,
never active pins; launches never prune. The launch top consumes one of nono's
ten inheritance levels.

## Credentials

Provision read-only credentials outside the sandbox in the engine root's
`credentials/` directory (or set `credentials.dir`).
`pi-sandbox-github.token` supplies `GH_TOKEN` and Git's gh helper.
Without it, GitHub is disabled; inherited human tokens are not substituted.
`pi-sandbox-ghcr.token` supplies only Docker's `ghcr.io` auth entry.
Optional `pi-sandbox-ghcr-build.token` gives builds their own `ghcr.io` credential
(a Buildx alternative config), for hosts whose builds and pulls need different
GitHub accounts.
Trailing newlines are stripped. Tokens never go into argv, native JSON documents, notices,
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
grants, including resolved symlink targets. User-owned hook executables must have
exactly one hard link; only non-user-writable root-owned system binaries are exempt.
Each hook has a private process group;
timeout or interruption terminates that group and reaps its direct process. stdin carries schema, OS,
workdir, launch directory, and HOME as JSON. Success returns empty stdout or
`{"seatbelt_rules":["(deny …)"],"notice":["- Policy note."]}`.
Rules are macOS-only; notices work on both platforms.
Failure refuses the launch and includes the hook's stderr.
The controller's `profile` preview executes neither hooks nor credentials.

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
- Herdr's Pi integration cannot report from inside the sandbox, so Herdr
  reads the pane's state from the screen and does not show a waiting
  question as blocked unless a Pi extension and a matching Herdr detection
  manifest provide that signal.

## Contributing

Questions, bug reports, and pull requests are welcome through the repository's
issue tracker. Read [AGENTS.md](AGENTS.md) and [the design](docs/design.md)
before changing launch behavior. Use Conventional Commits.

```sh
just build
just setup
just check
just test
```

Unit tests and stub integrations run on either OS; only `tests/entry.py` can
inject platform/kernel facts. Production has no test override variables.
Live tests use a probe instead of Pi and fixture HOMEs under
`${XDG_CACHE_HOME:-$HOME/.cache}/pi-sandbox-tests/`, with a cached pinned pack.
They skip clearly if nono is absent. They never use real tokens, configuration,
or Keychains. CI runs unit/integration on Linux and macOS.

## License

[MIT](LICENSE), copyright 2026 Rubio Enterprises.
