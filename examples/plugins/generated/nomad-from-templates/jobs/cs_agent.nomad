# customer-service-rep — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "cs_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "cs_agent"
    orgagents_team    = "AYC / Growth / Customer Service"
    reports_to        = "cgo_agent"
    accountable_human = "Jacob, Sherry"
  }

  group "cs_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "cs_agent"
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
  #   sandboxes.......... storefront_ops
  #   placements......... customer_service--storefront_ops
  #   capabilities....... order_query, refund_processing
  #   tools.............. 
  #   decisions it holds. issue_refund
  #   needs approval for. refund_processing
  #   resolved grants.... 3
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
