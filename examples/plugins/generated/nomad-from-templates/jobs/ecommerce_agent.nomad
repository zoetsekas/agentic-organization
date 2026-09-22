# ecommerce-manager — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "ecommerce_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "ecommerce_agent"
    orgagents_team    = "AYC / Growth / E-Commerce and Web"
    reports_to        = "cgo_agent"
    accountable_human = "Ping, Lea"
  }

  group "ecommerce_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "ecommerce_agent"
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
  #   sandboxes.......... storefront_ops, warehouse_ops
  #   placements......... ecommerce--storefront_ops, ecommerce--warehouse_ops
  #   capabilities....... order_query, product_publishing, stock_check
  #   tools.............. stock_lookup
  #   decisions it holds. publish_product
  #   needs approval for. product_publishing
  #   resolved grants.... 4
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
