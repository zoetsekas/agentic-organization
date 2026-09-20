---
id: ADR-0055
title: Mapping eight toolchain classes onto four sandbox images
status: Proposed
version: 0.1.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering]
informed: [All engineering]
scope: [targets, security, docs]
workstreams: [WS-006, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0009, ADR-0011, ADR-0053]
tags: [deployment, sandbox]
---

# ADR-0055: Mapping eight toolchain classes onto four sandbox images

## Context
ADR-0053 names four sandbox images by toolchain: `none` →
`gcr.io/distroless/static-debian12`, `python` → `python:3.11-slim`, `node` →
`node:22-alpine`, `data` → `python:3.11-slim` plus pinned analysis wheels. The
spec's vocabulary is not those four. `ToolchainClass` has eight members —
`none`, `scripting`, `data_analysis`, `software_build`, `browser`, `document`,
`model_training`, `network_client` — and the local target has to resolve every
one of them to something concrete.

Three of them have no image in ADR-0053 at all. `browser` needs a headless
Chromium; `model_training` needs CUDA; `document` needs LibreOffice, poppler
and tesseract (`docs/SANDBOX_TEMPLATES.md`). The old table reached for
`mcr.microsoft.com/playwright/python` for the browser case, which is an image
nobody reviewed and that appears in no lock file — exactly the ad-hoc picking
ADR-0053 exists to stop.

So the choice is: add unreviewed images to the supply chain, or map the classes
ADR-0053 does not name onto the nearest one it does and be explicit that some
classes then get less than their name promises.

## Decision
**Every toolchain class resolves to an image ADR-0053 names.** Nothing else is
emitted by the local target.

| Class | Image | Note |
|---|---|---|
| `none` | `gcr.io/distroless/static-debian12:nonroot` | No shell, no package manager, no interpreter |
| `scripting` | `python:3.11-slim` | |
| `data_analysis` | `python:3.11-slim` | Analysis wheels come from the binding's `packages`, pinned there |
| `software_build` | `node:22-alpine` | ADR-0053's `node` class |
| `browser` | `python:3.11-slim` | **Has no browser.** See Disadvantages |
| `document` | `python:3.11-slim` | No LibreOffice, no tesseract |
| `model_training` | `python:3.11-slim` | No CUDA, no GPU runtime |
| `network_client` | `python:3.11-slim` | |

Three consequences follow, and are implemented rather than merely stated:

1. **A distroless environment's generated Dockerfile installs nothing.** There
   is no shell to `RUN` with and no `useradd`; it copies the IR and switches to
   the image's own `nonroot` user. An environment class that declares packages
   *and* a distroless base gets a comment saying the packages were ignored, not
   a build that fails on a machine somebody else owns.
2. **A `network: none` sandbox service gets `network_mode: none`** — not an
   internal network. A zero-network sandbox that can still resolve its
   neighbours is not zero-network. (The agent *process* that uses the class
   stays on the gateway-less internal network, which is a different object.)
3. **An agent's own container is not its sandbox image.** The agent process
   runs on the platform runtime image; the environment class is the image its
   *code execution* happens in. Before this, the local target built the agent
   container from the environment class, which under this mapping would have
   given an agent in a `none` environment a distroless image with no
   interpreter to run on.
4. **A binding may still override the image.** `environment_binding(...).image`
   wins, because a company with its own reviewed, mirrored toolchain image
   should not have to fork the compiler to use it. That image is then the
   company's supply-chain decision, not ours.

## Scope
The local target's toolchain-to-image resolution and the generated per-class
Dockerfiles. It does not change the spec vocabulary, does not decide what a
browser or GPU sandbox should eventually run, and does not touch the cloud
targets, which bind images through their own modules.

## Implementation
`TOOLCHAIN_IMAGES` and `NO_RUNTIME_IMAGES` in
`src/orgagents/compiler/targets/local.py`; `_sandbox_image`,
`_sandbox_services` and `_environment_dockerfile` consume them. Every resolved
image appears in `docker/images.lock`. `tests/test_plane_compose.py` asserts
that every sandbox image the generator emits is one ADR-0053 names.

## Timeline
Phase 5, with ADR-0053.

## Advantages
- The sandbox supply chain is the ADR's list and nothing else, so reviewing the
  list is reviewing what runs.
- One Python base across most classes means one patch cadence, matching the
  rule ADR-0053 applies to our own images.
- The gap is visible: a `browser` class resolving to `python:3.11-slim` is
  obviously wrong to anyone who reads the table, where a quietly-added
  Playwright image would have looked fine and been unreviewed.

## Disadvantages
- **A `browser` sandbox has no browser, a `document` sandbox has no
  LibreOffice, and a `model_training` sandbox has no GPU runtime.** Code written
  for those classes will fail at run time, not at compile time. This is a real
  regression against the previous Playwright mapping for the browser case, and
  the honest reading is that those three classes are not supported locally
  until ADR-0053's table grows.
- **The mapping flattens a designed distinction.** Five classes resolving to
  the same image means the environment class no longer tells you what is
  installed, only what was declared.
- **`data_analysis` now installs nothing by default.** The wheels have to be
  pinned in the binding, so a spec that assumed pandas gets an image without
  it.
- **`software_build` on `node:22-alpine` has no Python**, which will surprise
  anyone whose build is a Python build. `ToolchainClass` has one class where
  ADR-0053 has two languages.
- Nothing here has been built. No Docker daemon exists in this environment.

## Alternatives considered
- **Keep per-class images chosen ad hoc (the Playwright mapping)** — gives a
  browser sandbox that works and puts an unmirrored, unpinned, unreviewed image
  inside the isolation boundary. Rejected on ADR-0053's terms.
- **Add browser/GPU/document images to ADR-0053's table here** — this is an
  implementation decision record; growing another ADR's reviewed image list
  from inside the compiler is how supply chains grow unnoticed. It should be an
  amendment to ADR-0053, made by the people who own it.
- **Refuse to compile an unsupported class** — honest, and it breaks every
  existing spec that uses `browser` for a compile that would have produced a
  usable stack for everything else in it.

## Verification
`tests/test_plane_compose.py::test_a_sandbox_image_is_one_the_adr_names`,
`::test_a_sandbox_that_executes_nothing_gets_an_image_that_cannot` and
`::test_a_zero_network_sandbox_gets_no_network_at_all`. No image has been
pulled or built; none of this is verified against a daemon.

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-09-20 | First draft. Eight classes mapped onto ADR-0053's four images, with the gaps named. |
