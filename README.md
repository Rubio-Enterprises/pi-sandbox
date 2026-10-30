# pi-sandbox

One-sentence description.

## Background

2–3 sentence elevator pitch: what problem does this solve? Who uses it? What's the operational status (experimental / internal / production = `internal`)?

## Install

```bash
# Copy-pasteable install command(s)
```

## Usage

```bash
# Smallest runnable example
```

## Configuration

Environment variables:

- `LOG_LEVEL` — `debug` | `info` | `warn` | `error` (default `info`)
- `LOG_FORMAT` — `json` | `console` (default `json` for services, `console` for CLIs)
- `SERVICE_NAME` — kebab-case, defaults to `pi-sandbox`

Config file location (if applicable): _document here_.

## Development

```bash
git clone <repo-url>
cd pi-sandbox
mise trust && mise bootstrap --only repos,tools --yes   # clone the shared config, install pinned tools
mise run setup                     # install pinned tools, git hooks and every dependency
mise run lint
mise run test

```

The runtime `.devcontainer/devcontainer.json` uses this repository's prebuilt
Repository Image. Its public org base provides signature-verified, pinned mise,
the personal dotfiles profile and git `safe.directory` for `/workspaces/*`.
The template-owned `.github/.devcontainer/devcontainer.json` lists only extra
Features; the central builder supplies the base and Dockerfile. The
`repository-image` workflow rebuilds on default-branch setup inputs, a staggered
weekly schedule or manual request. Private source stays in private images.

After this starter update, before opening a laptop Dev Container, run
`mise bootstrap --only repos,tools --yes` **on the host**. The workspace bind
mount carries the prepared `.standards-config/` into the container. Setup
validates its clean full-history checkout and uses its commit as the laptop pin;
it never fetches standards in-container, including later editor/CLI setup calls.
A missing checkout fails with host bootstrap guidance. GitHub credentials stay on the host; use the host's
`docker login ghcr.io` for private image pulls.

The shared tasks, `setup` among them, come from `.standards-config/`.
CI, Sprites and Claude cloud provision that checkout with an independently
resolved `STANDARDS_PIN`. Pinned `mise run setup` validates it and installs tools
without repository bootstrap; native unpinned setup retains explicit bootstrap.
Every other `setup:*` task installs stack or repository dependencies. Make each
step safe to repeat.

On Apple silicon macOS, `scripts/devcontainer-apple.sh` is the host-only entry
point for the same JSON. It checks the required `container` and
`adevcontainer` CLIs, runs `adevcontainer doctor`, and then invokes
`adevcontainer up`; if the doctor fails, start the Apple container runtime
explicitly and retry. Standard VS Code and Dev Container CLI workflows use the
JSON directly and do not require this launcher.

Copier seeds the strict-JSON runtime file when absent. Its preservation-aware
image migration updates the image and lifecycle pin checks while retaining
consumer mounts, environment, extra Features and custom lifecycle commands.
Keep repository-specific settings there; do not repeat the baked baseline Feature.

## Contributing

Questions and bug reports belong in the repository's issues. Pull requests are
welcome; run the development checks above before submitting a change.

## License

[MIT](LICENSE) © 2026 Rubio Enterprises.
