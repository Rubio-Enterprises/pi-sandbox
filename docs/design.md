# pi-sandbox design

`pi-sandbox` runs the [Pi coding agent](https://github.com/earendil-works/pi) under
[nono](https://nono.sh) on macOS and Linux (including WSL2). It prevents accidental host
damage, not an adversarial agent or prompt injection. This document is the design contract.

## Principles

1. **Mechanism, not personal policy.** User paths, identities, organisations and service choices
   arrive through Sandbox Profiles and their optional Pi sidecars.
2. **nono owns permissions.** A **Sandbox Profile** is a native nono JSON file, composed only
   through nono's `extends`. The engine handles Pi settings and launch-dependent facts.
3. **Facts are pushed in.** The launcher reads selected policy, environment, Git and filesystem
   facts, not other tools' configuration. Only the controller's advisory MCP check reads Pi config.
4. **Conservative without selection.** The engine base alone supplies no credential-store
   integrations, Docker routing or extra user grants. Provisioned built-in token files still work.
5. **Standard library only.** Runtime programs are `nono`, `pi`, `git`; optional integrations
   use `gh`, `tmux` and `agent-browser`.

## Native composition and immutable snapshots

Sandbox Profiles live at `${XDG_CONFIG_HOME:-$HOME/.config}/nono/profiles/<name>.json`.
They may extend other user files, nono built-ins or nono packs. Names are alphanumeric with
internal hyphens. Selection is `--profile <name>` (or `--profile=<name>`) >
`PI_SANDBOX_PROFILE` > no selection. The engine never reads or sets `NONO_PROFILE`; every
invocation passes an explicit generated nono document, so nono's variable cannot select policy.
A missing selected user JSON file refuses the launch. Paths are not accepted as selection names.

nono merges native policy, including filesystem grants/denies, environment filtering and values,
session hooks, security settings and raw Seatbelt rules. Scalars are child-wins, arrays
dedup-append, maps child-key-wins, denies accumulate. Native platform overrides apply after
inheritance. Unknown native fields are rejected by nono, not maintained in a duplicate engine schema.

The engine traverses only user JSON `extends` edges, depth-first left-to-right, revisiting shared
ancestors for each sibling exactly as nono does. True cycles and excessive depth refuse.
Non-user references are left to nono and have no sidecar; their internals are not traversed.
The generated launch top consumes one inheritance level: at most nine user inheritance edges
fit before nono's depth-10 boundary, potentially fewer when non-user bases also extend others.

Every reachable user JSON object is copied to an immutable content-addressed installed nono file.
Its user-parent references are rewritten to the parents' installed names, preserving the graph;
non-user references remain native names. The engine does not flatten or merge native policy.
Changing an ancestor changes descendant snapshot names. User files never name the engine base.

Launch composition uses two private native nono documents:

- `<launch>/engine.json`: extends the installed engine base, supplies dynamic Git Seatbelt rules.
- `<launch>/profile.json`: extends `["engine", "<selected snapshot>"]` (or just `["engine"]`),
  adds launch grants and environment, and puts hook/host/static safety rules in its platform override.

The engine base extends the verified `nolabs-ai/pi` pack. The prefix is necessary because
nono applies platform overrides after inheritance: Git rules stay first, user rules follow,
and safety floors stay last. Unrelated user scalars remain authoritative; launch isolation
environment values, working-directory access and safety floors are engine-owned.
Native session hooks remain nono-owned; sidecar hooks implement the engine's separate
pre-credential JSON protocol.

Installed names are `pi-sandbox-base-<sha12>` and `pi-sandbox-user-<name>-<sha12>`.
The digest covers exact stored UTF-8 JSON bytes (sorted keys, two-space indent, trailing newline).
Metadata retains its original name. Publication uses exclusive hard-link creation followed by
removal of the temporary alias; existing bytes are compared without newline normalization and
never overwritten. CRLF/CR mutations refuse even if JSON meaning is unchanged.

Launch and validation pin **all** installed ancestors with shared advisory locks on read-only
descriptors. Installation/pinning and pruning also share a short-lived store lock. Pruning needs
a nonblocking exclusive candidate lock. No writable hard-link aliases or state-file pins are
created. Only `setup` and explicit `prune` remove unreferenced `pi-sandbox-*.json` files,
retaining the current graph, active pins and files younger than 24 hours. Launches never prune.
Native built-ins and non-user packs remain nono-resolved; the engine verifies its pinned pack
using the integrity contract below.

Policy files and their containing policy directories must be user-owned, not symlinks and not
group/world-writable. Files require exactly one hard link. Reads are descriptor-relative with
no-follow opens; an absent optional sidecar is allowed, an unsafe one is refused.

## Package layout

`cli.py` splits engine/Pi arguments; `config.py` traverses native user inheritance, validates and
merges sidecars; `profile.py` installs/pins/prunes snapshots and assembles native launch documents;
`launch.py` orchestrates. `paths.py`, `platform.py`, `git.py`, `linux.py`, `seatbelt.py`,
`credentials.py`, `environment.py`, `integrations.py`, `hooks.py`, `wrapper.py` and `pack.py`
keep their respective filesystem, platform, Git, policy, launch integration and cleanup concerns.
`data/base-profile.json` is engine-installed. Tests are split into unit, integration and live tiers.

## CLI

`pi-sandbox [engine flags] [Pi arguments]` consumes:

| Flag | Meaning |
| --- | --- |
| `--profile NAME`, `--profile=NAME` | Select a Sandbox Profile; overrides `PI_SANDBOX_PROFILE`. |
| `--workdir=DIR` | Project directory, default PWD. Must exist; cannot contain HOME. |
| `--scratch=NAME` | Create/grant `<workspace.scratch_root>/NAME`; single effort name matching `[A-Za-z0-9][A-Za-z0-9_-]*`. |
| `--add-dirs[=]DIRS`, `--add-dirs-ro[=]DIRS` | Extra RW/RO colon-separated directories. |
| `--exclude-tools[=]P,Q`, `-xt[=]P,Q` | Merge Pi exclusions with sidecar exclusions. |

A literal `--`, including the separator, passes everything following unchanged to Pi.
Exit status is Pi's status, 2 for refusal, or 128 + signal. Refusal messages start `pi-sandbox:`.
Already inside nono (`NONO_CAP_FILE`), missing tools, unsafe workdirs, invalid policy, missing or
corrupt pack, old nono/kernel, unsafe hooks and failed tokenless Keychain checks refuse.

### Controller

All controller subcommands accept `--profile NAME` with the same environment fallback.

| Subcommand | Behaviour |
| --- | --- |
| `setup` | Pull/verify pinned pack, install current native snapshots, set pack Pi skill filter, prune stale snapshots. |
| `pack` | Print pinned pack reference. |
| `chain` | Print resolved user/non-user traversal and each merged sidecar path (or none). |
| `config --json` | Print `selected`, ordered `chain`, merged `sidecar`, ordered `sidecars` paths, and `rendered_exclude_tools`. |
| `profile [engine flags]` | Install snapshots; preview launch nono JSON on stdout, prefix/invocation/Pi wrapper on stderr; no credentials or hooks. |
| `validate` | Validate both sidecar platforms and use nono to validate installed JSON and resolve the complete chain. |
| `doctor [--workdir DIR]` | Validate plus nono/pack/kernel/Landlock checks and advisory MCP diagnostics. |
| `prune` | Remove only unreferenced engine-owned snapshots older than 24 hours and not actively pinned. |

## Locations

| Purpose | Path |
| --- | --- |
| Sandbox Profiles | `${XDG_CONFIG_HOME:-$HOME/.config}/nono/profiles/<name>.json` |
| Pi sidecars | `${PI_SANDBOX_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/pi-sandbox}/profiles/<name>.toml` |
| Credentials | `<engine config root>/credentials` by default |
| Host hook data/executables | `<engine config root>/host` when configured |
| Launch state | `${XDG_STATE_HOME:-$HOME/.local/state}/pi-sandbox/launch.XXXXXXXX`, private and removed on exit |
| Private cache | `${XDG_CACHE_HOME:-$HOME/.cache}/pi-sandbox` |
| Retained screenshots | `${XDG_STATE_HOME:-$HOME/.local/state}/agent-sandbox/artifacts/run.XXXXXXXX/screenshots` |
| nono data | `${XDG_CONFIG_HOME:-$HOME/.config}/nono/{profiles,packages}` |

The entire engine config root (including sidecars, credentials and host data) is denied inside
nono. An overridden credentials directory is denied separately too.
Content-addressed `pi-sandbox-…-<sha12>` names are reserved for engine snapshots;
ordinary named Sandbox Profiles are never prune candidates.

## Pi sidecar schema

Every key is optional. There is no schema/version or metadata key. Unknown keys refuse with a
native replacement hint where one exists. Every key below is allowed both at the top level and
inside `platform.macos` / `platform.linux`; only `platform` itself cannot nest.

| Key | Type / constraint |
| --- | --- |
| `pi.exclude_tools` | List of full Pi patterns matching `[A-Za-z0-9_*]+`. |
| `pi.mcp.<logical>.hide_tools` | List of server-local tool names/patterns; nonempty, no whitespace, commas or controls; only `*` is wildcard. |
| `pi.mcp.<logical>.names` | List of server aliases; logical names and aliases match `[A-Za-z0-9_-]+`. |
| `pi.skill_packs` | List of absolute pack-directory paths. |
| `notice.lines` | List of strings. |
| `credentials.dir` | Absolute directory path. |
| `credentials.env.<slot>.file` | Token filename relative to credentials.dir without `..`, or an absolute path. |
| `credentials.env.<slot>.command` | Shell command string run outside nono, instead of file. |
| `credentials.env.<slot>.env` | Environment identifier, required after merge. |
| `credentials.env.<slot>.set` | String-to-string map of companion environment identifiers and values. |
| `credentials.env.<slot>.missing`, `.notice` | Optional message strings; missing supports `{path}`. |
| `workspace.scratch_root` | Absolute directory path. |
| `workspace.worktree_root` | Absolute directory template, supports `{repo}`. |
| `grants.protected_checkouts` | List of absolute checkout paths; RO plus write protection. |
| `grants.runtime_dirs` | List of absolute directories, created private and granted RW. |
| `grants.project_state_dirs` | Origin-URL-to-string-list map; paths relative to XDG state, without `..`, empty or `.`. |
| `docker.socket` | Absolute socket path; enables routing and isolated Docker configuration. |
| `integrations` | List from credential-stores, onepassword, cloud-credentials, keychain, agent-mcp. |
| `hooks` | List of tables: nonempty string `name`, nonempty argv string-list `command`, optional `platforms` list from macos/linux (default both). |

A merged credential slot requires exactly one of `file` / `command` and `env`.
Sidecar paths expand `~/`, `$HOME` and all four XDG home variables with standard defaults,
and must be absolute afterward. Hook argv[0] expands `~`. Native JSON path/value expansion
belongs to nono, including its additional supported variables.

Sidecars merge in native traversal order. Each file's common settings merge first, then its
current platform table immediately afterward. Tables merge recursively, lists append uniquely,
later scalars win. Same-name hooks replace earlier hooks. Shared ancestors are revisited in a
diamond (their scalars can override an earlier sibling); lists still deduplicate.
This is not a second native policy resolver: only the Pi sidecars use this merge.

For MCP exclusions, iterate logical key plus aliases, then hidden tool patterns, emitting
`mcp__<normalized-server>__<normalized-pattern>`. Replace non-ASCII-alphanumeric/non-underscore
characters with underscores, preserving `*` in tool patterns. Flat exclusions come first,
then rendered MCP patterns, then CLI exclusions, de-duplicated into one flag.

Only `doctor` reads `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/mcp.json` and, with `--workdir`,
the project's `.pi/mcp.json`. Project entries override user entries by normalized name.
Missing/unparsable files and unmatched logical server names warn without affecting status or policy.
No server is connected to; extension-registered servers are not visible.

## Moving native policy into JSON

No numbered-directory discovery, layer TOML, fragments or compatibility shim remains.

| Former engine key | Native nono JSON field |
| --- | --- |
| `grants.ro_dirs` | `filesystem.read` |
| `grants.rw_dirs` | `filesystem.allow` |
| `grants.ro_files` | `filesystem.read_file` |
| `grants.ro_files_bypass` | Both `filesystem.read_file` and `filesystem.bypass_protection` |
| `env.pass` | `environment.allow_vars` |
| `env.set` | `environment.set_vars` |
| `env.unset` | `environment.deny_vars` |
| `env.never_pass` | `environment.deny_vars`, with native `*` glob patterns |
| `network.remote_port_deny` | `platform_overrides.macos.unsafe_macos_seatbelt_rules` |

Native paths are not engine-filtered by existence: nono decides path handling.
The former never-pass policy only blocked optional passthrough; native deny_vars filters
**all inherited values**, even allowed ones. Native globs support anchored `*`, not Python
fnmatch's `?` or bracket classes: enumerate names or use `*` deliberately. Native explicit
set_vars and credential injection happen after filtering. This stricter inherited-value rule
is deliberate; credential sidecar bridges remain explicit opt-ins.

For port P, use these ordered macOS raw rules:
`(deny network-outbound (remote ip "*:P"))` then
`(allow network-outbound (remote ip "localhost:P"))`.
Raw rules are ignored on Linux. Landlock has positive TCP port grants but no exact equivalent
for denying one port on remote hosts while reopening localhost. There is no silent Linux
translation: use native `network.block` plus explicit port grants if a stricter, different
policy is acceptable, or enforce the exact restriction with an external firewall.
Put the corresponding human-facing explanation in sidecar `notice.lines`.

## Built-in integrations

Names map one-to-one onto behaviours of the predecessor:

| Integration | Effect |
| --- | --- |
| *(none: default)* | Denies `$HOME/.op` (native nono deny) and raw read/write denies on `$XDG_CONFIG_HOME/op`, `incus`, `gcloud`. |
| `credential-stores` | Lifts those denies. |
| `onepassword` | macOS: `~/.config/op` RW + bypass; 1Password group-container `Data/settings` RO + bypass; `~/.config/1Password` RO (each only when present). Linux: a non-empty `op-service-account.token` in the credentials dir is injected with nono's `--env-credential-map file://… PI_SANDBOX_OP_SERVICE_ACCOUNT_TOKEN`, renamed to `OP_SERVICE_ACCOUNT_TOKEN` by the Pi wrapper; `OP_CONFIG_DIR` is `<cache>/op`; inherited `OP_SERVICE_ACCOUNT_TOKEN` is unset. |
| `cloud-credentials` | `~/.aws`, `~/.config/gcloud`, `~/.azure`, `~/.azd` RW when present; bypass for all but `.azd`. |
| `keychain` | macOS, only when the GitHub token is present: `~/Library/Keychains` RW + bypass, `~/Library/Preferences/com.apple.security.plist` allow_file, `/Library/Keychains/System.keychain` read_file + bypass. |
| `agent-mcp` | `$XDG_CONFIG_HOME/agent-mcp` RO (when present); without it, `$HOME/.config/agent-mcp` is denied in the launch nono document and by raw rule. Related services' credential files require explicit native `filesystem.read_file` grants in the consumer's Sandbox Profile. |

## Credentials

**Built-in GitHub slots** in `credentials.dir`:

- `pi-sandbox-github.token`: a read-only fine-grained PAT. When non-empty: `GH_TOKEN` is passed,
  Git's GitHub helper is `!gh auth git-credential`, stderr says `GitHub read-only; publish
  branches from outside the sandbox`. Otherwise Git gets a terminating `!f() { echo quit=1; }; f`
  helper and stderr says `GitHub off: no token at <path>`.
- `pi-sandbox-ghcr.token`: a `read:packages` classic token. When non-empty the per-launch
  Docker config gets a `ghcr.io` auth entry (`base64("x-access-token:<token>")`); otherwise
  stderr says `private ghcr.io images off: no token at <path>`.
- `pi-sandbox-ghcr-build.token`: optional, a second `read:packages` classic token for builds.
  When non-empty the launch also writes `<launch>/docker/buildx/config/ghcr.io/config.json`
  with the same `ghcr.io` auth entry form. Buildx (0.31 or later; Compose builds go through it)
  uses that registry-wide alternative config for every ghcr.io pull during a build, while
  `docker pull`, `docker run` and `docker compose pull` keep the `pi-sandbox-ghcr.token` entry.
  A host whose builds and pulls need different GitHub accounts on the same registry uses it;
  without it nothing is written and nothing is printed.

A trailing newline is stripped. Token bytes never appear in argv, logs, Git config or Git URLs.
Each launch writes `<launch>/gitconfig` (`credential.helper` empty, the GitHub helper,
`commit.gpgsign false`, `user.useConfigOnly true`), `<launch>/gh/` and
`<launch>/docker/config.json` (`auths` always contains `"https://index.docker.io/v1/": {}` so
Docker never selects a platform credential helper; `cliPluginsExtraDirs` per OS as before).

**Environment slots** (`credentials.env.*`) read a file or run a command outside the sandbox,
then export `env` and `set` only when the value is non-empty, add a notice line when present
and print `missing` when absent or empty. Slot variable names and companion names are cleared from inherited values and excluded from
`SANDBOX_ENV_PASS`. Explicit bridges use nono credential injection, after environment filtering.

**Why retain credential sidecars?** nono 0.80.0 supports required direct file credentials in
native `env_credentials` (`{"file:///absolute/path/service.token": "SERVICE_TOKEN"}`), as well
as `--env-credential-map file:///absolute/path/service.token SERVICE_TOKEN`. No sandbox read
grant is needed: nono reads the file before confinement. Use native JSON for mandatory direct
credentials; the renderer must supply the absolute file URI (the URI key is not path-expanded).

The native loader propagates missing/empty-file errors and refuses the launch. It has no optional
slot switch, missing-message/notice protocol, or conditional companion `set` map. Static native
`environment.set_vars` is unconditional, not a replacement for companion values enabled only when
the token exists. Native `cmd://` capture is supervisor/proxy-only and explicitly refuses direct
child-environment injection; it does not replace arbitrary sidecar `command` slots. Native file
loading also removes one trailing LF/CRLF, whereas sidecars strip all trailing newlines.

Therefore the sidecar adapter remains for optional/command/conditional behavior, but delegates
injection to native `--env-credential-map env://NAME NAME` after resolving and normalizing values.
This also pins the loaded value rather than asking nono to reread a potentially changed file.
Linux's existing optional 1Password file bridge remains a launch-time integration, not evidence
that every sidecar slot can be replaced by a required static native credential.
These distinctions are covered by the fixture-only real-nono credential tests and nono 0.80's
[credential loader](https://github.com/always-further/nono/blob/v0.80.0/crates/nono/src/keystore.rs).

**Git identity.** The launcher resolves `git -C <workdir> config user.name` / `user.email`
outside the sandbox and passes `GIT_AUTHOR_*`/`GIT_COMMITTER_*` when non-empty; a missing value
prints a notice and the launch continues.

## Hooks

A hook runs outside the sandbox after grants are computed and before the launch nono document is written.
argv is `command` (first element `~`-expanded). stdin is a JSON object:
`{"schema": 1, "os": "macos"|"linux", "workdir": "...", "launch_dir": "...", "home": "..."}`.
Exit 0 with stdout either empty or a JSON object
`{"seatbelt_rules": ["(deny …)"], "notice": ["- …"]}`; `seatbelt_rules` is accepted only on
macOS and must be a list of strings starting with `(`. Any other exit refuses the launch with
`pi-sandbox: hook <name> refused the launch` followed by the hook's stderr. A hook has a
30-second timeout. Each hook runs in its own session/process group; timeout,
interruption, or excessive output terminates only that group (TERM, then KILL),
and the direct child is waited/reaped. stdout and stderr are each capped at 1 MiB.
The resolved executable, including symlink targets and PATH lookup, must lie outside
every RW grant. Checks include potential token-enabled integration grants before
credentials are read, plus macOS's common-Git grant and private runtime roots.
A provisional credential-free nono document is resolved with nono's manifest format to
include RW grants from native Sandbox Profiles, the official pack, and groups.
Unresolvable/malformed grant data refuses launch. Executable lookup preserves the
invoked path/argv identity (notably virtual environments) while validating both that
path and its canonical target. The resolved executable is opened without following
symlinks and must be a regular file. User-owned executables require `st_nlink == 1`;
the only multi-link exemption is a non-user-owned, root-owned executable that the
effective user cannot write (checked with `os.access`, including ACLs).
Hook argv/inline programs are trusted policy, not inferred or rewritten by the engine.

## Launch sequence

1. Refuse nested nono; split arguments; locate Pi/nono/Git; validate the real workdir.
2. Select the Sandbox Profile, load native user inheritance and merge Pi sidecars.
   Check nono version and pinned pack integrity; compute the immutable graph.
3. On Linux require kernel >=6.12; on WSL refuse grants on /mnt/<drive>. Git launches require
   linked worktrees, the standard private Git-dir layout and inactive extensions.worktreeConfig.
   Caller grants must be directories and must not overlap protected policy, checkouts, credential
   or Git paths in either direction. Resolve the selected native filesystem writes with nono too:
   project the native diagnostic filesystem into a temporary native document with optional nono groups
   excluded and required deny groups retained, then resolve its manifest. Validate those paths against the same floors, preserving
   native path expansion without confusing implicit group or intentional engine Git grants.
4. Compute engine-dependent grants: workdir, CLI dirs, scratch/worktree/runtime/project-state
   roots, protected checkouts and integrations. Native static grants remain in Sandbox Profiles.
   Linux adds common objects/refs/logs and private worktree metadata RW, common metadata and
   main checkout RO. macOS adds @git:common-dir RW and @git:worktree RO, and an overridden
   XDG state mise directory RW.
5. Resolve Git identity. Allocate private launch/artifact/runtime/browser/tmux roots.
   Install/pin the complete snapshot graph; write the launch prefix; resolve a provisional
   native manifest before credentials. Use its RW grants to validate sidecar hook locations.
   Execute hooks before any token file or credential command.
6. Read credentials, finish conditional grants, write isolated gh/Docker/Git config, build
   environment and render the Pi wrapper and notice. Launch directory is RO except private
   gh/Docker subdirectories; cache/socket/artifact/runtime roots are RW.
7. Write the top nono document. macOS ordering is Git → native user rules → hook → host → static.
   On tokenless macOS launches verify effective Keychain denial without querying Keychain.
8. Run `nono wrap --profile <launch>/profile.json --allow-cwd [credential maps] -- pi <args>`
   on macOS. On Linux use `nono run --silent --profile <launch>/profile.json --allow-cwd
   --suppress-save-prompt <launch> --suppress-save-prompt <cache> [credential maps] -- pi <args>`.
   Never use nono run on macOS: its debug log stream stalls Endpoint Security clients.
9. Always clean launch and owned socket roots; stop tmux only by explicit socket path.
   Keep retained artifacts and installed native snapshots.

With `docker.socket`: set DOCKER_HOST, TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE and
JAVA_TOOL_OPTIONS to the selected socket, keep DOCKER_CONFIG private, and on macOS grant
unix_socket access. An existing socket receives bypass_protection on both platforms.
Clear DOCKER_CONTEXT, DOCKER_TLS_VERIFY and DOCKER_CERT_PATH. Without routing, Docker
configuration is still private and the notice says Docker is not configured.

## Engine base nono profile (`pi-sandbox-base`)

Extends `nolabs-ai/pi`. Groups include `mise_manager`, `go_runtime`, `java_runtime`,
`bun_runtime`. Reads: `$XDG_DATA_HOME/agent-extensions`, `~/.local/pipx`, `~/.rbenv`, `~/.rvm`,
`~/.asdf`, `~/.tmux.conf`, `$XDG_CONFIG_HOME/tmux`. Denies: `~/.orbstack`, `~/.rd`,
`~/.local/share/containers`, `~/.config/containers`, `~/.config/incus`, `~/.config/lxc`,
`~/.config/gh`, `$XDG_CONFIG_HOME/pi-sandbox` (and the default path), `~/.pg_service.conf`,
`~/.pgpass`, `~/.pypirc`, plus the integration-dependent denies (those are added to the
per-launch nono document, not the base). macOS override: exclude group `user_caches_macos`,
`security.ipc_mode` `full`, reads of `~/Library/Preferences/.GlobalPreferences*`, `ByHost`, the
Chrome / Chrome for Testing / Chromium preference plists, read_file `~/.CFUserTextEncoding`,
denies `~/Library/Containers/com.docker.docker`, `~/Library/Application Support/Mozilla`.
Linux override: exclude `user_caches_linux`, allow `/dev/shm`.

## Raw Seatbelt rules (macOS)

The effective order is **Git rules → Sandbox Profile rules (including remote port denies) →
hook rules → host rules → static floors**. The launch-local prefix holds Git rules in its common
block; the launch top holds the safety tail in its platform override. This preserves the order
through nono 0.80's final platform-override merge. Later SBPL rules win.

**Static floors, applied last** (absolute paths resolved at launch):

- `(deny file-write* …)` on `$XDG_CONFIG_HOME/zsh`, nono `profiles` and `packages`, Pi's
  `~/.pi/agent/{extensions,npm,git,bin,skills}` and `~/.pi/config` (subpaths), the literal files
  `~/.pi/agent/{settings,mcp,models,ai-accounts,llama-swap-discovery,keybindings,capability-exposure}.json`
  and `AGENTS.md`, and the Git regexes `/\.git/hooks(/|$)` and
  `/\.git/(config(\.worktree)?|worktrees/[^/]+/config\.worktree)(\.lock)?$`.
  `models.json` and `ai-accounts.json` can carry commands and must stay write-blocked.
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
`<launch>/profile.json`, `<launch>/engine.json`; and write-deny on `$XDG_CACHE_HOME` except the private cache.

`nono why` cannot see raw rules; the notice says so.

## Environment

The engine supplies a narrow base allowlist (HOME/PATH/shell/terminal/locale/XDG/proxy variables),
its own launch variables, existing PI_ASK_USER_* and clipboard support, and valid identifier names
from SANDBOX_ENV_PASS. Credential bridge names cannot be restored through passthrough.
Sandbox Profiles add native environment.allow_vars/deny_vars/set_vars; nono owns matching.

All nonsecret engine isolation values are also set_vars in the final platform override, so a
Sandbox Profile cannot redirect Git/gh/Docker config, browser/tmux roots, caches or launch identity.
This includes JAVA_TOOL_OPTIONS, GEM_HOME and GEM_PATH, which nono strips from ordinary inherited
values. Refuse dollar signs in engine-generated set values to avoid unintended native expansion.
Other user set_vars retain nono's child-key-wins rules. Nonsecret cache redirections cover npm,
uv/uv-tools, pip, yarn, pnpm, corepack, bun, cargo, go, gradle, maven, gems and bundle.

GH_TOKEN and configured credential-slot values never enter JSON or argv: argv contains only
`--env-credential-map env://NAME NAME`. They are injected by nono after environment filtering,
so an explicit sidecar bridge works even when inherited NAME matches a deny glob.
Linux's 1Password bridge retains its file:// reference and wrapper rename.
No secret values appear in snapshots, notice, Pi wrapper or Git config.

Built-in removals before nono: HERDR_ENV, SSH_AUTH_SOCK, GITHUB_TOKEN, GH_ENTERPRISE_TOKEN,
GITHUB_ENTERPRISE_TOKEN, GH_DEBUG, GIT_CONFIG_PARAMETERS, TMUX, TMUX_PANE, DOCKER_CONTEXT,
DOCKER_TLS_VERIFY and DOCKER_CERT_PATH. Native deny_vars filters the child rather than removing
variables from the host process that executes nono.

Herdr integration stays disabled inside nono; its socket can control unsandboxed panes.
Herdr must classify the pane by screen detection, which cannot see a waiting question.
TERM_PROGRAM still passes for an extension/detection-manifest pairing; the engine supplies neither.

## Pi wrapper and notice

`<launch>/bin/pi` is a POSIX `sh` script that `exec`s the absolute real Pi with: one
`--exclude-tools` (merged, de-duplicated, comma-joined) when any pattern exists; one `--skill`
per existing skill root of the `nolabs-ai/pi` pack and each `pi.skill_packs` entry (roots from
the pack's `package.json` `pi.skills`, default `skills`); `--append-system-prompt "$(cat
<launch>/notice.md)"`; then `"$@"`. Subagent workers start `pi` from `PATH`, so they inherit it.

The notice has two parts: a static policy section (sandbox denial = stop and report; ask for a
wider launch before a detour; Git config/hooks/protected checkouts/Pi config are write-blocked
by rules `nono why` cannot see (macOS) or the Linux equivalent; Linux socket exposure; GitHub
read-only and publish outside; Docker rule; removed tools exist on purpose) plus the merged sidecar's
`notice.lines`, credential-slot notices and hook notices; and a per-launch section listing
engine-added read-write and read-only grants, runtime dirs, screenshots, and subagent rules (private tmux
server only; never Herdr panes or the host's tmux). It never lists removed tool names.

## Keychain preflight (macOS, tokenless launches)

Resolve the per-launch nono document with `nono profile show <profile> --format manifest` and
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
The single-threaded launcher resets SIGINT to default in the child before exec;
the parent's ignored disposition must not be inherited. Cleanup phases have
independent finalizers and errors do not replace the child's status. Owned private
directory permissions are recovered descriptor-relatively without following symlinks,
including mode-000 tmux directories.

## nono pack

The engine pins `nolabs-ai/pi` (initially `0.2.2`) and requires nono `>= 0.80.0`. `setup`
pulls the pin with `nono pull -s`, then sets `"skills": []` on the pack's entry in
`~/.pi/agent/settings.json` `packages` (matched by its `source`), so the pack's `nono-sandbox`
skill — which asserts a nono launch — never loads outside the sandbox; the wrapper loads it
inside. `setup` never removes other packs.

nono 0.80's lockfile records the installed `profiles/pi.json` artifact's SHA-256
separately from the pack version. The retained `.nono-trust.bundle` maps signed
`policy.json` to that installed path with the same digest. Launch and setup verify the
nono profile bytes against the lockfile digest; missing/malformed metadata, a mismatch,
or malformed nono profile JSON is a refusal. The nono document's own `meta.version` is not the
pack version. This is local integrity checking of nono's installed metadata, not a
replacement signature verifier. `nono trust verify` verifies instruction-file bundles
against a project trust policy, not the installed-pack layout; no suitable pack
reverification command is exposed in 0.80. Signature/provenance verification remains
nono pull's responsibility; corruption is repaired through nono or refused.

## Implementation decisions

- Dry-run `profile` uses stable `launch.preview` and socket/artifact placeholders,
  installs immutable nono snapshots but creates no launch/runtime directories and reads neither
  credentials nor Git identity. Its stderr includes the launch-local prefix document and invocation.
  It does not run hooks: arbitrary outside-sandbox commands are not a safe preview.
  `validate` syncs native snapshots and uses an ephemeral resolver nono document; it never launches Pi.
- Hook stdin requires a real launch directory. It is allocated before hooks run and
  removed on refusal. Scratch/runtime directories are prepared first; retained artifacts
  may therefore exist even when a later hook or Keychain preflight refuses. Credential
  reads/commands occur only after all hooks accept.
- A failed credential command discards partial stdout and follows the slot's missing
  behavior. Slot `set` names, as well as its secret name, are cleared from inherited
  environment and protected from `SANDBOX_ENV_PASS` to prevent stale companion values.
  GitHub and inference bridge names cannot be restored from that passthrough either.
- Missing-tool refusals name `nono` or `git` individually. There is no jq dependency.
  Python's JSON/TOML/path/process APIs replace shell+jq plumbing; nono still resolves
  the native policy chain and applies confinement.
- Native manifest resolution is reused for hook grant checks rather than duplicating
  nono's inheritance, group, platform, and write-grant semantics in a custom resolver.
  The bounded hook supervisor uses selectors because `subprocess.run/communicate`
  cannot cap output or tear down an owned descendant group by themselves. Shared
  file locks pin native snapshots without requiring config/state to share a filesystem or
  introducing writable hard-link aliases; hard links are used only for atomic publish
  inside the protected nono profile store.
- The nono lockfile and installed pack's nono profile must both confirm the pin.
  Setup preserves unrelated pack entries and changes only this pack's skill filter.
- Private temporary roots use exclusive 0700 mkdir with eight cryptographically random
  alphanumeric characters, preserving the predecessor's socket-path naming contract.
  Python's `mkdtemp` also emits underscores, so its names do not match that contract.
- The baseline Seatbelt floor retains default configuration paths and protects XDG
  configuration overrides too. Hooks add rules between port rules and host rules; static
  floors follow all dynamic rules.
- Linux checks the kernel floor before launch; `doctor` additionally queries Landlock's
  ABI. nono performs the actual platform confinement checks.
- Launcher and controller integration tests select OS/kernel/WSL through a Python
  entry point shipped only under `tests/`. Native entry-point checks and live doctor
  tests retain host detection and real kernel/Landlock checks.
  No environment variable relaxes a production preflight. CI excludes the live marker;
  `just test` excludes it too; run `just exec uv run pytest tests/live` explicitly.

## Testing

- **unit**: config merge and validation, path expansion, Seatbelt quoting, argument split,
  notice/wrapper rendering, env allowlist, Keychain preflight predicate, hook protocol.
- **integration** (any OS, no real nono): stub `nono` records argv, nono document and effective
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
