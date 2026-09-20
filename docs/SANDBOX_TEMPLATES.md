# Sandbox environment templates

A sandbox template is the **governed unit of code execution**. Administrators
publish templates; the designer UI offers them as a dropdown; an agent may only
execute inside a template published for it. An agent's `SandboxSpec` may
*narrow* a template (shorter timeout, extra env) but never widen it — extra
egress is ignored on a zero-network template, and a longer timeout is clamped.

Each template fixes seven things:

| Field | Meaning |
|---|---|
| `base_image` + `toolchain` + `packages` | what is available to run |
| `cpu` / `memory` / `disk` / `gpu` | the resource envelope, and the bill |
| `network` + `egress_allowlist` | `none`, `egress_allowlist`, `internal`, `full` |
| `mounts` | which data planes are visible inside the workspace |
| `secret_refs` | secrets injected by name from the secret manager |
| `filesystem` | `ephemeral`, `session_persistent`, `agent_persistent` |
| `timeout_s`, `privileged` | the hard stop and the escape hatch (default off) |

## The eight built-in templates

### 1. `minimal-reasoning`
No code execution at all: reason, delegate, call MCP tools. 0.25 CPU, 256Mi, no
network, 2-minute cap. **The default** — most executive and routing agents never
need more, and it is the cheapest and safest thing to run.

### 2. `data-analysis`
The Python data stack (pandas, numpy, scipy, matplotlib, pyarrow, duckdb) for
analyst agents working on rows pulled through the relational harness. 2 CPU /
8Gi, package-index egress only, all three data planes mounted,
session-persistent disk so a multi-turn analysis keeps its intermediates.

### 3. `software-engineering`
Full developer environment: git, Python/Node/Go, make, docker CLI, test
runners. 4 CPU / 16Gi / 50Gi, one hour, egress limited to source and package
hosts, a GitHub App token, and **agent-persistent** disk so an engineering agent
keeps its checkout and caches between sessions.

### 4. `browser-automation`
Headless Chromium plus Playwright for agents that operate web apps, capture
screenshots or scrape approved sources. Ephemeral disk, internal-domain egress,
10-minute cap — deliberately short, because browser work that runs long is
usually stuck.

### 5. `document-processing`
LibreOffice, poppler, tesseract and the python-docx/openpyxl/pypdf stack, for
contract review and report production. **No network**: documents come in
through mounts and leave through the data planes.

### 6. `ml-training`
GPU environment (PyTorch, CUDA 12.4, 1×A100, 64Gi, 6-hour cap) for fine-tuning
and heavy inference. Requires capacity approval and is budgeted separately —
treat it as a scheduled workload, not an interactive tool.

### 7. `regulated-data-clean-room`
Air-gapped room for PII/PCI/PHI work: zero egress, private plane only,
ephemeral disk wiped on exit, `AUDIT_MODE=strict`, no secrets beyond the scoped
database connection. The only template appropriate for regulated data, and the
reason overrides may not widen network posture.

### 8. `integration-runner`
Network-facing runner for agents calling internal APIs and enterprise SaaS
through the egress proxy. HTTP tooling only (httpx, curl, jq, tenacity) — no
data stack, no compiler, so a compromised integration agent has little to work
with.

## Choosing one

```
Does the agent execute code?           no  → minimal-reasoning
Regulated data in scope?              yes  → regulated-data-clean-room
Needs a GPU?                          yes  → ml-training
Writes code / opens PRs?              yes  → software-engineering
Drives a browser?                     yes  → browser-automation
Reads or writes documents?            yes  → document-processing
Calls internal APIs?                  yes  → integration-runner
Otherwise analyzing data?                  → data-analysis
```

## Adding your own

```python
platform.sandboxes.publish(SandboxTemplate(
    name="mainframe-batch",
    base_image="ghcr.io/org/zos-tools:2026.1",
    toolchain=["jcl", "db2-client"],
    cpu="2", memory="4Gi", network="internal",
    egress_allowlist=["mainframe.corp.internal"],
    mounts=[Visibility.PRIVATE], filesystem="ephemeral",
    secret_refs=["ZOS_SERVICE_ACCOUNT"],
))
```

Published templates immediately appear in the designer palette and in the
marketplace, and `SandboxRunner.container_spec` renders the orchestrator run
spec — resources, network policy, mounts, volume claim and a locked-down
security context (non-root, read-only root, all capabilities dropped).

## Execution backends

- **`local`** — subprocess in a temp workspace. Development and tests only; it
  honors the timeout but not the network or resource envelope.
- **`container`** — returns the run spec for the platform's orchestrator
  (Kubernetes jobs or a microVM pool). The agent process never launches
  workloads itself, which keeps the isolation boundary outside the agent.
