---
status: accepted
date: 2026-11-09
---

# Compose sandbox policy from native nono profiles

## Context and Problem Statement

Operators need one Sandbox Profile concept without numeric ordering gaps or a second sandbox policy language. nono already supplies inheritance, filesystem and environment policy, platform overrides, raw Seatbelt rules and session hooks. Pi exclusions, credential bridges and launch-dependent facts still require engine orchestration.

## Considered Options

- Numbered engine TOML layers: rejected because ordering requires reserved gaps and introduces a second policy language.
- Engine-implemented `extends` over TOML copying nono's rules: rejected because it reimplements nono.
- Native nono profiles + Pi sidecars: chosen to delegate policy composition to nono.

## Decision Outcome

Chosen option: "Native nono profiles + Pi sidecars", because nono owns sandbox permissions while small optional sidecars express only Pi and launch-time settings.

## Consequences

- Sandbox Profiles compose with native `extends`; the engine snapshots user ancestors immutably and merges optional sidecars in the same traversal order.
- Engine-generated launch facts preserve credential isolation, Git protection, safety-rule ordering and chain pinning without exposing the engine base in user configuration.
- Bad: existing installations must migrate their policy files and selection variable; sidecar validation and nono validation remain separate, and Linux cannot express macOS's remote-port deny with a localhost exception exactly.
