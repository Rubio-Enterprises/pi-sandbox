# pi-sandbox design

`pi-sandbox` runs the [Pi coding agent](https://github.com/earendil-works/pi) under
[nono](https://nono.sh) on macOS and Linux (including WSL2). It prevents **accidental host
damage** by a coding agent. It does not contain an adversarial agent or defend against prompt
injection.

This document is the design contract. Code, tests and README follow it. When it is silent about
a behaviour, preserve the behaviour of the predecessor implementation (a zsh launcher rendered
by a private dotfiles repository; maintainers have the reference copy) and record the decision
here.

## Principles

1. **Mechanism, not policy about a person.** The engine may know about third-party tools
   (Git, Docker, 1Password, Chrome, Playwright, tmux, Herdr, Worktrunk, mise, uv). It must not
   know any user's paths, organisations, hosts, identities, MCP servers or credentials. Those
   arrive through layers.
2. **nono owns what the sandbox permits; the engine owns how a launch is assembled.** Anything
   expressible as a static nono profile key lives in a nono profile: the engine's base profile or
   a layer's `profile.json`. The engine handles what depends on launch-time facts or is not a
   sandbox permission: workdir and Git grants, existence checks, credential bridging, raw
   Seatbelt rules with absolute paths, the Pi wrapper, the notice, hooks and cleanup.
3. **Facts are pushed in, never pulled.** The engine reads only its own layer directory, the
   environment, Git and the filesystem. It never reads another tool's configuration to discover
   policy (for example a dotfiles repository or Pi's MCP config).
4. **Secure by default.** Without layers, the engine still launches a useful, conservative
   sandbox: no credential stores, no Docker, no extra read grants.
5. **Standard library only.** The runtime has no third-party Python dependencies. External
   programs: `nono`, `pi`, `git`; optional `tmux`, `agent-browser`, `gh` (used inside the
   sandbox as Git's credential helper).

## Architecture

```text
nolabs-ai/pi                    official signed nono pack (pinned by the engine)
  └─ pi-sandbox-base            engine-shipped static profile (src/pi_sandbox/data/base-profile.json)
       ├─ pi-sandbox-layer-<a>  layer profile.json, installed by the engine at launch
       ├─ pi-sandbox-layer-<b>
       └─ <launch>/profile.json per-launch profile written by the engine:
                                extends ["pi-sandbox-base", "pi-sandbox-layer-<a>", ...]
```

nono resolves the chain: `extends` takes a list of profile names merged left to right, each
profile's `platform_overrides` applies, arrays are dedup-appended and denies are unioned. A later
layer cannot remove an earlier deny; put each deny in the layer that wants it.

The engine also merges the layers' `layer.toml` files (engine settings, below) into one
**resolved configuration** and uses it to assemble the per-launch profile, environment, Pi
wrapper and notice.

## Package layout

```text
src/pi_sandbox/
  __init__.py
  cli.py            pi-sandbox entry point: argument split, error reporting, exit codes
  ctl.py            pi-sandbox-ctl entry point (admin subcommands)
  config.py         layer discovery, TOML schema validation, merge, path expansion
  platform.py       OS detection, WSL detection, kernel preflight
  paths.py          XDG helpers and the engine's own directories
  git.py            Git topology, identity, worktree root
  seatbelt.py       raw Seatbelt rule rendering and quoting (macOS)
  linux.py          Linux grant validation and piecewise Git grants
  credentials.py    token files, env slots, gitconfig, Docker config, gh config
  integrations.py   built-in integrations (1Password, cloud credentials, Keychain, agent-mcp)
  hooks.py          hook execution protocol
  profile.py        per-launch profile assembly, layer-profile sync, Keychain preflight
  environment.py    allowlist, set_vars, unset list, SANDBOX_ENV_PASS
  wrapper.py        <launch>/bin/pi and notice.md
  launch.py         orchestration, signal handling, cleanup
  pack.py           nono pack pin, setup, Pi settings skill filter
  data/base-profile.json
tests/
  unit/             pure-function tests
  integration/      black-box launches against stub nono/pi (any OS)
  live/             real nono launches with a probe in place of Pi (skip when nono is absent)
docs/design.md      this file
```

`pyproject.toml` declares two console scripts: `pi-sandbox = "pi_sandbox.cli:main"` and
`pi-sandbox-ctl = "pi_sandbox.ctl:main"`. Python floor: the repository's pinned Python.

## CLI

### `pi-sandbox [grant flags] [Pi arguments]`

Grant flags are consumed by the engine and never reach Pi. Everything else reaches Pi unchanged,
in order. A literal `--` passes it and every following argument to Pi.

| Flag | Meaning |
| --- | --- |
| `--workdir=DIR` | Project directory (default: `$PWD`). `~` expands. Must exist. |
| `--scratch=NAME` | Grant `<scratch_root>/NAME` read-write, creating it. `NAME` matches `[A-Za-z0-9][A-Za-z0-9_-]*`. Refused when no layer sets `workspace.scratch_root`. |
| `--add-dirs=DIRS`, `--add-dirs DIRS` | Colon-separated extra read-write directories (`~` expands). |
| `--add-dirs-ro=DIRS`, `--add-dirs-ro DIRS` | Colon-separated extra read-only directories. |
| `--exclude-tools=P,Q`, `--exclude-tools P,Q`, `-xt=…`, `-xt …` | Extra Pi tool patterns merged with the layers' `pi.exclude_tools` into one `--exclude-tools` flag. |

Exit codes: Pi's own status on a completed launch; `2` for a refused launch (message on stderr,
prefixed `pi-sandbox:`); `128+N` after signal `N`.

Refusals (all before any launch state is created unless noted): already inside nono
(`NONO_CAP_FILE` set); `pi`, `nono` or `git` missing; workdir not a directory; workdir is `/`,
`$HOME` or an ancestor of `$HOME`; invalid scratch name or scratch without `scratch_root`; nono
older than the supported minimum; pinned pack missing (tell the user to run
`pi-sandbox-ctl setup`); any hook refusal; Linux preflight failures (below); Git hooks directory
covering the workdir or Git metadata (macOS); Keychain preflight failure (macOS, tokenless).

### `pi-sandbox-ctl`

| Subcommand | Behaviour |
| --- | --- |
| `setup` | Pull the pinned nono pack when absent or at another version; sync layer profiles; set the pack's Pi settings entry to load no skills outside the sandbox. Idempotent. |
| `pack` | Print the pinned pack reference (`nolabs-ai/pi@X.Y.Z`). |
| `layers` | List discovered layers in order with their paths and any validation errors. |
| `config [--json]` | Print the resolved configuration for this platform. |
| `profile [--workdir=DIR] [grant flags]` | Assemble and print the per-launch profile without launching or reading credentials (dry run; token-dependent parts shown as if tokenless). |
| `validate` | Validate every layer (schema, paths, profile.json rules) and resolve the chain with `nono profile show`; non-zero on any error. |
| `doctor` | `validate` plus nono version, pack version, Linux kernel/Landlock checks. |

## Locations

| Purpose | Path |
| --- | --- |
| Engine config root | `${PI_SANDBOX_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/pi-sandbox}` |
| Layers | `<config root>/layers/<NN-name>/layer.toml` and optional `profile.json` |
| Default credentials directory | `<config root>/credentials` |
| Launch state | `${XDG_STATE_HOME:-$HOME/.local/state}/pi-sandbox/launch.XXXXXXXX` (0700, removed on exit) |
| Private caches | `${XDG_CACHE_HOME:-$HOME/.cache}/pi-sandbox` |
| Screenshot artifacts | `${XDG_STATE_HOME:-$HOME/.local/state}/agent-sandbox/artifacts/run.XXXXXXXX/screenshots` (retained) |
| nono config | `${XDG_CONFIG_HOME:-$HOME/.config}/nono` |
| Installed layer profiles | `<nono config>/profiles/pi-sandbox-base.json`, `pi-sandbox-layer-<name>.json` |

The whole engine config root is denied inside the sandbox (read and write). Layers are applied
in lexical order of their directory names; directory names match `[0-9]{2}-[a-z0-9][a-z0-9-]*`
and the profile name is `pi-sandbox-layer-<directory name>`.

## Layer format (`layer.toml`, schema 1)

Every key is optional except `schema`. Unknown keys are errors. Paths accept `~/`, `$HOME`,
`$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, `$XDG_STATE_HOME`, `$XDG_CACHE_HOME` (with the standard
defaults) and must be absolute after expansion.

```toml
schema = 1

# Built-in integrations to enable (see Integrations)
integrations = ["credential-stores", "onepassword", "cloud-credentials", "keychain", "agent-mcp"]

[layer]
description = "Example organisation defaults"

[grants]
ro_dirs = ["~/.agents"]              # read-only, skipped when absent
rw_dirs = []                         # read-write, skipped when absent
ro_files = ["~/.playwright/cli.config.json"]   # exact-file read-only (always granted, listed in the notice)
ro_files_bypass = ["~/.config/herdr/config.toml"]  # file inside a denied directory: read-only + bypass_protection when present
runtime_dirs = []                    # created (0700) at launch and granted read-write
protected_checkouts = []             # read-only grant; macOS write floor on everything but .git; Linux grant-overlap floor

[grants.project_state_dirs]          # origin URL (without .git) -> dirs under $XDG_STATE_HOME, created and granted RW when the workdir's origin matches
"https://github.com/example/app" = ["app-test-harness"]

[workspace]
scratch_root = "~/Code/.scratch"     # enables --scratch
worktree_root = "~/.local/share/git-worktrees/{repo}"  # {repo}: basename of the main checkout; created and granted RW when the workdir is in a Git repository whose common dir ends in /.git

[docker]
socket = "~/.colima/default/docker.sock"   # absolute or ~; enables Docker routing (below)

[network]
remote_port_deny = [{ port = 8080, service = "example-service" }]   # macOS: deny the port on every host, reopen localhost

[credentials]
dir = "~/.config/pi-sandbox/credentials"   # where token files live; denied inside the sandbox

[credentials.env.jira]               # generic environment credential slot, keyed by name
file = "jira.token"                  # relative to credentials.dir, or absolute; OR:
# command = "security find-generic-password -w -s example"   # run outside the sandbox with sh -c
env = "EXAMPLE_JIRA_TOKEN"           # variable the sandbox receives when the value is non-empty
set = { EXAMPLE_URL = "https://example.atlassian.net" }      # also exported when the value is non-empty
missing = "Jira off: no token at {path}"   # stderr notice when absent/empty; omitted = silent
notice = "Jira is reachable read-only; it refuses writes. For a change, tell the user what to change."

[env]
pass = ["SOME_VAR"]                  # extra names allowed through from the caller's environment
set = { DO_NOT_TRACK = "1" }         # nono environment.set_vars (nono expands $HOME etc.)
unset = ["EXAMPLE_ADMIN_TOKEN"]      # removed from the environment before nono starts
never_pass = ["EXAMPLE_*"]           # SANDBOX_ENV_PASS may never admit these (globs)

[pi]
exclude_tools = ["mcp__example__delete_*"]   # Pi tool patterns ([A-Za-z0-9_*]+)
skill_packs = []                     # extra pack directories whose Pi skills load only inside the sandbox

[notice]
lines = ["- Example: an extra rule the agent must follow."]

[[hooks]]
name = "listener-inventory"
command = ["~/.local/libexec/pi-sandbox/listener-inventory"]
platforms = ["macos"]                # default: both

[platform.macos]                     # same keys as the top level (except schema/layer/platform), merged after this layer's top level on macOS
[platform.linux]
```

**Merge.** Layers merge in order. Within one layer the top level merges first, then the
current platform's table. Tables merge key by key; lists append with order-preserving
de-duplication; scalars from later sources win. `[[hooks]]` entries append; a later hook with the
same `name` replaces the earlier one. `credentials.env.<slot>` tables merge key by key, so a
later layer can add `set` entries to an earlier slot (for example a per-user email).

**`profile.json`.** A nono profile fragment. The engine rejects `extends`,
`unsafe_macos_seatbelt_rules` (raw rules carry absolute paths: use hooks), `workdir`, and
`meta.name` values other than the derived profile name; it injects `meta.name` when absent. On
every launch (and on `setup`/`validate`) the engine writes the installed copy atomically when its
content changed and removes `pi-sandbox-layer-*.json` profiles that no longer have a layer.

## Built-in integrations

Names map one-to-one onto behaviours of the predecessor:

| Integration | Effect |
| --- | --- |
| *(none: default)* | Denies `$HOME/.op` (profile) and raw read/write denies on `$XDG_CONFIG_HOME/op`, `incus`, `gcloud`. |
| `credential-stores` | Lifts those denies. |
| `onepassword` | macOS: `~/.config/op` RW + bypass; 1Password group-container `Data/settings` RO + bypass; `~/.config/1Password` RO (each only when present). Linux: a non-empty `op-service-account.token` in the credentials dir is injected with nono's `--env-credential-map file://… PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN`, renamed to `OP_SERVICE_ACCOUNT_TOKEN` by the Pi wrapper; `OP_CONFIG_DIR` is `<cache>/op`; inherited `OP_SERVICE_ACCOUNT_TOKEN` is unset. |
| `cloud-credentials` | `~/.aws`, `~/.config/gcloud`, `~/.azure`, `~/.azd` RW when present; bypass for all but `.azd`. |
| `keychain` | macOS, only when the GitHub token is present: `~/Library/Keychains` RW + bypass, `~/Library/Preferences/com.apple.security.plist` allow_file, `/Library/Keychains/System.keychain` read_file + bypass. |
| `agent-mcp` | `$XDG_CONFIG_HOME/agent-mcp` RO (when present) and `~/.opentabs/extension/auth.json` read_file; without it, `$HOME/.config/agent-mcp` is denied in the profile and by raw rule. |

## Credentials

**Built-in GitHub slots** in `credentials.dir`:

- `pi-sandbox-github.token`: a read-only fine-grained PAT. When non-empty: `GH_TOKEN` is passed,
  Git's GitHub helper is `!gh auth git-credential`, stderr says `GitHub read-only; publish
  branches from outside the sandbox`. Otherwise Git gets a terminating `!f() { echo quit=1; }; f`
  helper and stderr says `GitHub off: no token at <path>`.
- `pi-sandbox-ghcr.token`: a `read:packages` classic token. When non-empty the per-launch
  Docker config gets a `ghcr.io` auth entry (`base64("x-access-token:<token>")`); otherwise
  stderr says `private ghcr.io images off: no token at <path>`.

A trailing newline is stripped. Token bytes never appear in argv, logs, Git config or Git URLs.
Each launch writes `<launch>/gitconfig` (`credential.helper` empty, the GitHub helper,
`commit.gpgsign false`, `user.useConfigOnly true`), `<launch>/gh/` and
`<launch>/docker/config.json` (`auths` always contains `"https://index.docker.io/v1/": {}` so
Docker never selects a platform credential helper; `cliPluginsExtraDirs` per OS as before).

**Environment slots** (`credentials.env.*`) read a file or run a command outside the sandbox,
then export `env` and `set` only when the value is non-empty, add a notice line when present
and print `missing` when absent or empty. Slot variable names are added to `never_pass`
automatically.

**Git identity.** The launcher resolves `git -C <workdir> config user.name` / `user.email`
outside the sandbox and passes `GIT_AUTHOR_*`/`GIT_COMMITTER_*` when non-empty; a missing value
prints a notice and the launch continues.

## Hooks

A hook runs outside the sandbox after grants are computed and before the profile is written.
argv is `command` (first element `~`-expanded). stdin is a JSON object:
`{"schema": 1, "os": "macos"|"linux", "workdir": "...", "launch_dir": "...", "home": "..."}`.
Exit 0 with stdout either empty or a JSON object
`{"seatbelt_rules": ["(deny …)"], "notice": ["- …"]}`; `seatbelt_rules` is accepted only on
macOS and must be a list of strings starting with `(`. Any other exit refuses the launch with
`pi-sandbox: hook <name> refused the launch` followed by the hook's stderr. A hook has a
30-second timeout.

## Launch sequence

1. Refuse when `NONO_CAP_FILE` is set. Split arguments. Resolve `pi` (absolute path), `nono`,
   `git`. Resolve the workdir (`~`, absolute, real path) and apply the workdir refusals.
2. Load and validate layers; resolve configuration for the current OS. Check the nono version
   and the pinned pack. Sync installed layer profiles.
3. **Linux preflight** (Linux only): kernel `>= 6.12` (else refuse with the WSL update hint); on
   WSL (`/proc/version` contains `microsoft`, case-insensitive) refuse any grant under
   `/mnt/<drive>`. For a Git workdir: refuse the main checkout (`git-dir == common-dir`), refuse a
   layout where `git-dir`'s parent is not `<common>/worktrees`, refuse active
   `extensions.worktreeConfig`. Validate every caller grant against the floors: nono config
   (default and XDG), zsh config (default and XDG), each `protected_checkouts`, the credentials
   dir, the common Git dir and a custom hooks dir; refuse overlap in either direction and refuse
   non-directories.
4. Grants: RW = workdir, `--add-dirs`, worktree root, scratch, `rw_dirs`, runtime dirs, project
   state dirs, integration grants; RO = `--add-dirs-ro`, `ro_dirs`, `protected_checkouts`,
   built-in RO dirs (`$XDG_DATA_HOME/agent-browser/skills`, `$XDG_CONFIG_HOME/worktrunk`, when
   present). Linux adds `<common>/{objects,refs,logs}` and the exact `<common>/worktrees/<name>`
   RW, `<common>` and the worktree root RO. macOS adds the nono tokens `@git:common-dir` (allow)
   and `@git:worktree` (read). macOS also grants `$XDG_STATE_HOME/mise` RW when it differs from
   the default.
5. Read credentials and run env-slot commands; resolve the Git identity; run hooks.
6. Create launch state: launch dir, artifacts dir, runtime dirs, `gh`/`docker`/`gitconfig`,
   the agent-browser socket root `/tmp/sab.XXXXXXXX` and tmux root `/tmp/pst.XXXXXXXX`
   (both 0700, real paths), and grant them (launch dir RO; its `gh`, `docker`, the cache, the
   two roots, artifacts and runtime dirs RW).
7. macOS raw rules, in this order: Git rules for the workdir, remote port denies, hook rules,
   host rules, then static floors (below, preserving their internal order). Later Seatbelt
   rules win: the static floors must remain last.
8. Write `<launch>/notice.md` and `<launch>/bin/pi`; prepend `<launch>/bin` to `PATH`.
9. Write `<launch>/profile.json`: `extends` the chain; `meta.name` `pi-sandbox-launch`;
   `workdir.access` `readwrite`; `filesystem.allow/read/allow_file/read_file/bypass_protection`;
   `unix_socket` for the Docker socket (macOS); `environment.allow_vars/set_vars`;
   `unsafe_macos_seatbelt_rules` (macOS).
10. macOS, tokenless: Keychain preflight (below); refuse on failure.
11. Change into the workdir and run, with the `unset` list removed from the environment:
    macOS `nono wrap --profile <profile> --allow-cwd -- pi <args>`; Linux `nono run --silent
    --profile <profile> --allow-cwd --suppress-save-prompt <launch> --suppress-save-prompt
    <cache> [credential maps] -- pi <args>`. `nono run` is never used on macOS: it starts a
    debug-level unified-log stream that stalls Endpoint Security clients system-wide.
12. Cleanup (always, including after signals): stop tmux servers by explicit socket path,
    close agent-browser sessions in the private socket root, remove both roots and the launch
    dir. Artifacts are retained.

**Docker.** With `docker.socket` set: `DOCKER_HOST=unix://<socket>`,
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`,
`JAVA_TOOL_OPTIONS=-Ddocker.host=unix://<socket>`, `DOCKER_CONFIG=<launch>/docker`; macOS adds a
`unix_socket` grant and, when the socket exists, a `bypass_protection` entry. `DOCKER_CONTEXT`,
`DOCKER_TLS_VERIFY` and `DOCKER_CERT_PATH` are unset. Without it, `DOCKER_CONFIG` is still
private and the notice says Docker is not configured.

## Base profile (`pi-sandbox-base`)

Extends `nolabs-ai/pi`. Groups include `mise_manager`, `go_runtime`, `java_runtime`,
`bun_runtime`. Reads: `$XDG_DATA_HOME/agent-extensions`, `~/.local/pipx`, `~/.rbenv`, `~/.rvm`,
`~/.asdf`, `~/.tmux.conf`, `$XDG_CONFIG_HOME/tmux`. Denies: `~/.orbstack`, `~/.rd`,
`~/.local/share/containers`, `~/.config/containers`, `~/.config/incus`, `~/.config/lxc`,
`~/.config/gh`, `$XDG_CONFIG_HOME/pi-sandbox` (and the default path), `~/.pg_service.conf`,
`~/.pgpass`, `~/.pypirc`, plus the integration-dependent denies (those are added to the
per-launch profile, not the base). macOS override: exclude group `user_caches_macos`,
`security.ipc_mode` `full`, reads of `~/Library/Preferences/.GlobalPreferences*`, `ByHost`, the
Chrome / Chrome for Testing / Chromium preference plists, read_file `~/.CFUserTextEncoding`,
denies `~/Library/Containers/com.docker.docker`, `~/Library/Application Support/Mozilla`.
Linux override: exclude `user_caches_linux`, allow `/dev/shm`.

## Raw Seatbelt rules (macOS)

The effective order is **Git rules → remote port denies → hook rules → host rules →
static floors**. nono 0.80 resolved the predecessor's per-launch rules before the old
user profile's platform-override rules. Preserve that resolved order, not the old file
layout: later SBPL rules win, so static floors are applied last.

**Static floors, applied last** (absolute paths resolved at launch):

- `(deny file-write* …)` on `$XDG_CONFIG_HOME/zsh`, nono `profiles` and `packages`, Pi's
  `~/.pi/agent/{extensions,npm,git,bin,skills}` and `~/.pi/config` (subpaths), the literal files
  `~/.pi/agent/{settings,mcp,models,llama-swap-discovery,keybindings,capability-exposure}.json`
  and `AGENTS.md`, and the Git regexes `/\.git/hooks(/|$)` and
  `/\.git/(config(\.worktree)?|worktrees/[^/]+/config\.worktree)(\.lock)?$`.
- For each `protected_checkouts` entry: deny writes inside it except under its `.git`.
- `(deny device-microphone)`; deny exec of `/usr/bin/open`, `/usr/bin/osascript`; allow
  `/bin/ps` with `no-sandbox`; deny the Apple Events, SMAppService and background-task Mach
  services; allow `ipc-sysv-sem`; allow Chrome/Chrome for Testing/Chromium rendezvous and
  crashpad Mach registration; allow `iokit-open RootDomainUserClient`.
- Host-control sockets:
  `(deny file-read-data file-write* network-outbound (regex #"^/private/tmp/(tmux|piab)-[^/]*(/|$)" #"^/private/var/folders/[^/]+/[^/]+/T/(herdr|vscode-git|ssh-askpass)-[^/]*(/|$)" #"^/private/var/run/com\.apple\.launchd\.[^/]*(/|$)"))`.
  These are raw regexes, not native deny globs: nono resolves a native glob by walking its parent
  recursively on every launch, which costs minutes under `/private/tmp` and `$TMPDIR`.

**Dynamic rules, applied before the static floors:** Git rules for the workdir (deny writes to
each resolved `config`,
`config.worktree` and their `.lock` and real-path variants; deny writes to the common and custom
hooks directories, refusing a hooks directory that covers the workdir or metadata), remote port
denies (`(deny network-outbound (remote ip "*:P"))` then `(allow … "localhost:P")`), hook rules,
then host rules: read/write deny on `$XDG_CONFIG_HOME/pi-sandbox`, the credentials dir,
`$XDG_CONFIG_HOME/gh`, plus the integration-dependent ones; read-allow + write-deny on
`$XDG_DATA_HOME/{mise,uv,agent-extensions,agent-browser/skills}`,
`~/.local/share/agent-browser/skills`, `$XDG_CONFIG_HOME/{zsh,worktrunk}`,
`$XDG_STATE_HOME/mise/{trusted-configs,ignored-configs}`, `<launch>/gitconfig`,
`<launch>/profile.json`; and write-deny on `$XDG_CACHE_HOME` except the private cache.

`nono why` cannot see raw rules; the notice says so.

## Environment

`environment.allow_vars` = the base list (`HOME PATH SHELL TMPDIR USER LOGNAME PWD TERM
COLORTERM TERM_PROGRAM TERM_PROGRAM_VERSION EDITOR VISUAL TMP TEMP LANG LC_* (each) TZ
XDG_{CONFIG,CACHE,STATE,DATA}_HOME HTTP(S)_PROXY NO_PROXY (both cases) NODE_EXTRA_CA_CERTS
NO_BROWSER PLAYWRIGHT_MCP_SANDBOX SDKROOT USERNAME TMUX_TMPDIR AGENT_BROWSER_SKILLS_DIR`), the
launcher's own variables (Git isolation: `GH_CONFIG_DIR GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
GIT_CONFIG_COUNT GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false SSH_ASKPASS=/usr/bin/false`;
caches; Docker; agent-browser `AGENT_BROWSER_SOCKET_DIR PI_AGENT_BROWSER_SOCKET_DIR
AGENT_BROWSER_SCREENSHOT_DIR AGENT_BROWSER_ARGS=--no-sandbox,--no-startup-window`;
`MISE_CACHE_DIR`, `BUN_RUNTIME_TRANSPILER_CACHE_PATH`; this list must match the predecessor's
exactly), every set `PI_ASK_USER_*`, `PI_CLIPBOARD_OSC52` when set
or when launched from SSH/mosh, credential variables when non-empty, `[env].pass`, and
`SANDBOX_ENV_PASS` names that are valid identifiers and not matched by `never_pass`.
`environment.set_vars` carries `JAVA_TOOL_OPTIONS`, `GEM_HOME`, `GEM_PATH` (nono strips them
from the inherited environment; refuse a launch whose values contain `$`) and `[env].set`.
Cache redirection: npm, uv (`UV_CACHE_DIR`, `UV_TOOL_DIR`), pip, yarn, pnpm, corepack, bun,
cargo, go, gradle, maven, gems, bundle under the private cache.

Built-in unset list: `HERDR_ENV SSH_AUTH_SOCK GITHUB_TOKEN GH_ENTERPRISE_TOKEN
GITHUB_ENTERPRISE_TOKEN GH_DEBUG GIT_CONFIG_PARAMETERS TMUX TMUX_PANE DOCKER_CONTEXT
DOCKER_TLS_VERIFY DOCKER_CERT_PATH`, plus `[env].unset`.

## Pi wrapper and notice

`<launch>/bin/pi` is a POSIX `sh` script that `exec`s the absolute real Pi with: one
`--exclude-tools` (merged, de-duplicated, comma-joined) when any pattern exists; one `--skill`
per existing skill root of the `nolabs-ai/pi` pack and each `pi.skill_packs` entry (roots from
the pack's `package.json` `pi.skills`, default `skills`); `--append-system-prompt "$(cat
<launch>/notice.md)"`; then `"$@"`. Subagent workers start `pi` from `PATH`, so they inherit it.

The notice has two parts: a static policy section (sandbox denial = stop and report; ask for a
wider launch before a detour; Git config/hooks/protected checkouts/Pi config are write-blocked
by rules `nono why` cannot see (macOS) or the Linux equivalent; Linux socket exposure; GitHub
read-only and publish outside; Docker rule; removed tools exist on purpose) plus each layer's
`notice.lines`, credential-slot notices and hook notices; and a per-launch section listing
read-write and read-only grants, runtime dirs, screenshots, and subagent rules (private tmux
server only; never Herdr panes or the host's tmux). It never lists removed tool names.

## Keychain preflight (macOS, tokenless launches)

Resolve the per-launch profile with `nono profile show <profile> --format manifest` and
`--json` (from the workdir). Refuse unless the manifest denies both `~/Library/Keychains` (as
`$HOME` or real path) and `/Library/Keychains`, no `bypass_protection` entry matches
`Keychains`, and no raw rule starting with `(allow` mentions `Keychains` or
`com.apple.(SecurityServer|securityd|secd|security.agent|security.keychaind)`.

## Signals and cleanup

The launcher runs nono as a child in the foreground process group. While the child runs it
ignores `SIGINT` (the terminal delivers it to Pi), and on `SIGHUP`/`SIGTERM` it forwards the
signal to the child and waits. Cleanup always runs, ignoring further signals, then the launcher
exits with the child's status or `128+N`. Tmux cleanup runs `tmux -S <socket> kill-server` for
each real socket (not a symlink, link count 1) in `<root>/tmux-<uid>/`, never by server name.

## nono pack

The engine pins `nolabs-ai/pi` (initially `0.2.2`) and requires nono `>= 0.80.0`. `setup`
pulls the pin with `nono pull -s`, then sets `"skills": []` on the pack's entry in
`~/.pi/agent/settings.json` `packages` (matched by its `source`), so the pack's `nono-sandbox`
skill — which asserts a nono launch — never loads outside the sandbox; the wrapper loads it
inside. `setup` never removes other packs.

## Implementation decisions

- Dry-run `profile` uses stable `launch.preview` and socket/artifact placeholders,
  does not create directories, and reads neither credentials nor Git identity.
  It does not run hooks: arbitrary outside-sandbox commands are not a safe preview.
  `validate` syncs profiles and uses an ephemeral resolver profile; it never launches Pi.
- Hook stdin requires a real launch directory. It is allocated before hooks run and
  removed on refusal. Scratch/runtime directories are prepared first; retained artifacts
  may therefore exist even when a later hook or Keychain preflight refuses.
- A failed credential command discards partial stdout and follows the slot's missing
  behavior. Slot `set` names, as well as its secret name, are cleared from inherited
  environment and protected from `SANDBOX_ENV_PASS` to prevent stale companion values.
  GitHub and inference bridge names cannot be restored from that passthrough either.
- Missing-tool refusals name `nono` or `git` individually. There is no jq dependency.
  Python's JSON/TOML/path/process APIs replace shell+jq plumbing; nono still resolves
  the native policy chain and applies confinement.
- The nono lockfile and installed profile must both confirm the pinned pack.
  Setup preserves unrelated pack entries and changes only this pack's skill filter.
- Private temporary roots use exclusive 0700 mkdir with eight cryptographically random
  alphanumeric characters, preserving the predecessor's socket-path naming contract.
  Python's `mkdtemp` also emits underscores, so its names do not match that contract.
- The baseline Seatbelt floor retains default configuration paths and protects XDG
  configuration overrides too. Hooks add rules between port rules and host rules; static
  floors follow all dynamic rules.
- Linux checks the kernel floor before launch; `doctor` additionally queries Landlock's
  ABI. nono performs the actual platform confinement checks.
- Tests select OS/kernel/WSL through a Python entry point shipped only under `tests/`.
  No environment variable relaxes a production preflight. CI excludes the live marker;
  normal local `mise run test` includes it.

## Testing

- **unit**: config merge and validation, path expansion, Seatbelt quoting, argument split,
  notice/wrapper rendering, env allowlist, Keychain preflight predicate, hook protocol.
- **integration** (any OS, no real nono): stub `nono` records argv, profile and effective
  environment; stub `pi`; both OS code paths through an injectable platform seam. Covers
  everything the predecessor's stub tier covered: grant separation, merged exclusions, notice,
  workdir change, refusals, credential slots (present/empty/absent), Keychain refusal paths,
  Docker and port routing, hooks, browser/tmux/launch cleanup also after HUP/INT/TERM and pty
  closure.
- **live** (real nono; skipped with a reason when absent): pull the pinned pack into a fixture
  HOME under `${XDG_CACHE_HOME}/pi-sandbox-tests` (nono refuses HOME under `/private/var`), run
  the real launcher with a probe in place of Pi, assert grants, denials (EPERM), environment,
  private tmux inheritance, socket denials and cleanup.
- CI runs unit and integration on Linux and macOS; live where nono can be installed.
