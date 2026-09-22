# inventory-auditor — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "inventory_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "inventory_agent"
    orgagents_team    = "AYC / Operations / Inventory Audit"
    reports_to        = "coo_agent"
    accountable_human = "Liang, Clark"
  }

  group "inventory_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "inventory_agent"
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
  #   placements......... inventory_audit--warehouse_ops
  #   capabilities....... inventory_adjustment, stock_check
  #   tools.............. 
  #   decisions it holds. adjust_inventory
  #   needs approval for. inventory_adjustment
  #   resolved grants.... 3
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
