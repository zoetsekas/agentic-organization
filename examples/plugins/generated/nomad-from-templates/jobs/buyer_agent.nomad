# buyer — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "buyer_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "buyer_agent"
    orgagents_team    = "AYC / Operations / Purchasing"
    reports_to        = "coo_agent"
    accountable_human = "Katy, Clark"
  }

  group "buyer_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "buyer_agent"
        ORGAGENTS_MODEL    = "anthropic:claude-sonnet-5"
        ORGAGENTS_RUNTIME  = "langchain_deepagents"
      }

      resources {
        cpu    = 500
        memory = 1024
      }
    }
  }

  # ---- Carried by the design, NOT enforced by Nomad -----------------------
  #   sandboxes.......... warehouse_ops
  #   placements......... purchasing--warehouse_ops
  #   capabilities....... goods_receiving, purchase_ordering, stock_check
  #   tools.............. 
  #   decisions it holds. raise_po, receive_goods
  #   needs approval for. goods_receiving, purchase_ordering
  #   resolved grants.... 4
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
