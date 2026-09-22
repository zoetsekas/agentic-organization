# warehouse-officer — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "warehouse_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "warehouse_agent"
    orgagents_team    = "AYC / Operations / Warehouse and Shipping"
    reports_to        = "coo_agent"
    accountable_human = "Andrew, Liang"
  }

  group "warehouse_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "warehouse_agent"
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
  #   placements......... warehouse--warehouse_ops
  #   capabilities....... order_fulfillment, order_query
  #   tools.............. 
  #   decisions it holds. fulfill_order
  #   needs approval for. order_fulfillment
  #   resolved grants.... 3
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
