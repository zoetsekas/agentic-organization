# AYC

A salon-furnishings distributor as an agentic organization: a CEO over a COO
(purchasing, warehouse, inventory audit, finance, IT) and a CGO (e-commerce,
customer service, marketing), twelve agents, three separations of duties.

| File | What it is |
|---|---|
| `ayc.system.yaml` | The design. Vendor-neutral. |
| `ayc.binding.yaml` | The design on Google Cloud (`adk`, `terraform:gcp`) and on LangGraph Platform (`langgraph`). |
| `ayc.local.binding.yaml` | The design on this workstation, in Docker: stub model, mock systems. |
| `generated/` | Compiled output per target, committed so it can be read without compiling. `generated/local/` is the Docker stack. |
| `mocks/` | Mock Shopify, Fishbowl, accounting, CMS and deploy pipeline — one MCP server, five seeds. |
| `chat/` | A chat window: pick any agent and talk to it. |
| `end_to_end_operations.py` | The organization coordinating, in one process, no containers. |
| `end_to_end_local.py` | Purchase to pay against the running Docker stack. |

## Running AYC locally

Needs Docker Desktop (Compose v2.24 or later) and the project installed in a
virtual environment. Nothing real is reached: the model is a stub and every
backing system is a mock seeded with AYC's catalogue, stock, suppliers and
ledger. No API key is needed.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,langgraph]"      # .venv/bin/pip elsewhere

.venv/Scripts/python examples/ayc/local_stack.py up   # or: make ayc-up PY=.venv/Scripts/python
.venv/Scripts/python examples/ayc/end_to_end_local.py # or: make ayc-e2e
.venv/Scripts/python examples/ayc/local_stack.py down # or: make ayc-down
```

`up` writes `generated/local/.env` with fresh random local values (git ignores
it), recompiles the design, builds the orgagents wheel the images install, and
starts everything under the Compose project **`ayc-local`**, waiting until each
container is healthy. It runs beside the designer on :8000 without touching it.

| URL | What |
|---|---|
| http://localhost:18080/ | **Chat** — pick an agent, talk to it, see its tool calls and refusals |
| http://localhost:18000/ui/ | The tenant's own designer/API |
| http://localhost:18101/state | Mock Shopify (products, orders, refunds, promotions) |
| http://localhost:18102/state | Mock Fishbowl (parts, suppliers, purchase orders, sales orders) |
| http://localhost:18103/state | Mock accounting (payables, payments, receivables) |
| http://localhost:18104/state | Mock CMS |
| http://localhost:18105/state | Mock deploy pipeline |

Each mock's `/state` includes an `audit` list of every call: which agent, which
credential, which tool, and whether it was refused.

### What runs

- **Twelve agent containers**, one per agent, each running `orgagents worker
  <agent>` on **LangChain deep agents** — the same runtime the `langgraph` and
  `terraform:gcp` targets use. Only the model is replaced: `provider: stub` is
  a deterministic chat model that makes the tool calls a message spells out,
  one per line, and answers in canned words:

  ```text
  call fishbowl__stock_check {"sku": "AYC-CH-001"}
  ```

- **Five mock systems**, each on an internal network of its own that only the
  agents holding a capability on it join. The buyer's container cannot route to
  accounting; accounts payable's cannot route to Fishbowl.
- The tenant's Postgres, NATS (one user per agent; `bus-init` creates the
  stream and consumers, then exits), SeaweedFS artifact store, OpenTelemetry
  collector and designer, as the local target generates them.
- `memory` and `scheduler` are parked (profile `parked`): the first has no
  command yet, and the second would run agents outside their own containers.
  See ADR-0109.

### The controls, as you will meet them

Every write capability in the design is *supervised*, so the first call stops
with **needs approval**. In the chat window, click the approver the design
names (the COO, Clark, for Operations and Finance) to release **that one call
with those arguments**; the agent is asked again and it goes. Then try what the
separations forbid:

- ask **buyer** to `call accounting__invoice_payment {...}` — it holds no such
  tool, and its container has no route to the ledger;
- ask **accounts-payable** to pay `SINV-7790` — the accounting system's
  three-way match refuses: it names no purchase order;
- the warehouse's Fishbowl credential cannot adjust a count, even called
  directly: Fishbowl tells the dock and the cycle count apart by credential.

`end_to_end_local.py` walks exactly this path — reorder, approval, purchase
order, receipt, supplier invoice, payment — and checks each refusal. It resets
the mocks to their seed first, so it can be run again.

### Agents talking to agents (ADR-0118)

Agents reach each other over the tenant's **NATS JetStream**, each as its own
broker user, and only along edges the design declares: a leader to its team,
declared interaction flows (`consult`/`notify`/`escalate` let an agent *message*,
only `delegate` lets it hand work over), a member to the agent it reports to,
`oversees`/`serves` unit links between unit leaders, and a live mission. The
edges are compiled into each agent's `agents/<id>.json` (`links`) and into
`generated/local/nats/nats.conf`, so **the sender's worker, the broker and the
receiver's worker each refuse the rest**. Every agent holds three tools:

```text
call send_message {"to_agent": "coo_agent", "text": "stock of AYC-CH-001 is low"}
call delegate {"to_agent": "buyer_agent", "task": "review stock of AYC-CH-001"}
then call check_delegation {"handle": "$last.handle", "wait_s": 60}
```

`delegate` returns a handle at once; `check_delegation` collects the answer. A
`then call` line is a later turn of the stub model, and `"$last.handle"` is the
previous turn's result. A delegated task's `inputs.script` lines become the
next agent's script, so a chain can be driven from one message:

```bash
.venv/Scripts/python examples/ayc/local_stack.py e2e-messaging
```

walks the CEO delegating "review stock of AYC-CH-001" to the COO, who delegates
it to the buyer; the buyer's attempt to message accounts receivable (no edge) is
refused by its own worker, then by the broker when published by hand from its
container, then by accounts receivable when published by an identity the broker
lets through; and the buyer asking the COO to have payables pay is refused on
separation `purchasing_and_payment` — at the COO's delegation, and at payables'
payment tool. In the chat, a reply that delegated or messaged shows its
**agent-to-agent hops**: every worker's part of the one trace.

A second copy of the stack runs beside the first with a project and ports of its
own (and images named after the project, so the first copy's are not re-tagged):

```bash
.venv/Scripts/python examples/ayc/local_stack.py --project ayc-msg --port-base 19000 up
.venv/Scripts/python examples/ayc/local_stack.py --project ayc-msg --port-base 19000 e2e-messaging
docker compose -p ayc-msg down -v
```

### Purchase-to-pay as a governed workflow (ADR-0110)

`purchase_to_pay` in the spec draws the same process: the buyer raises the
order, the COO approves (a human step), then a fork runs goods receiving (buyer)
and invoice capture (payables) in parallel, a join waits for both, and payables
matches and pays. Invoice capture is `body: external` — its insides are built in
the tenant's flow engine and the spec holds only its interface. The binding
sends it to `flows.internal`, which on a workstation is a mock of the engine's
run API (`mocks/server.py`, `MOCK_SYSTEM=langflow`; its flow is published on
http://localhost:17860/flow/invoice-capture). Run the bound step as payables,
through the egress checks:

    docker exec ayc-local-agent-ap_agent-1 python -c "import json,urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8000/workflow', json.dumps({'workflow': 'invoice_capture', 'inputs': {'purchase_order': 'PO-1040'}}).encode(), {'content-type': 'application/json'})).read().decode())"

### Changing it

The design is `ayc.system.yaml`; the local choices are `ayc.local.binding.yaml`.
Everything under `generated/local/` except `overlays/` is rewritten by
`local_stack.py generate` (and by `up`). Workstation-only changes — ports, the
mock and chat images — live in `generated/local/overlays/20-ayc-workstation.yaml`,
which the compiler never touches (ADR-0092). To run on a real model, change the
binding's `model:` block; nothing else in the stack changes.
