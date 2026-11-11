---
status: accepted
date: 2026-11-09
---

# Compose sandbox policy from native nono profiles

## Context and Problem Statement

Operators encode permissions in Sandbox Profiles, so replacing the policy language requires migrating their files. nono already supplies native policy inheritance, but Pi exclusions, credential bridges and launch-dependent facts still require engine orchestration. Should the engine own sandbox policy composition or delegate it to nono?

## Considered Options

- Numbered engine TOML layers: rejected because ordering requires reserved gaps and introduces a second policy language.
- Engine-implemented `extends` over TOML copying nono's rules: rejected because it reimplements nono.
- Native nono profiles + Pi sidecars: chosen to delegate policy composition to nono.

## Decision Outcome

Chosen option: "Native nono profiles + Pi sidecars", because nono owns sandbox permissions while small optional sidecars express only Pi and launch-time settings.

## Consequences

- Good, because native composition avoids maintaining a second implementation of nono's sandbox policy rules.
- Bad, because native policy and Pi sidecars require separate validation.
