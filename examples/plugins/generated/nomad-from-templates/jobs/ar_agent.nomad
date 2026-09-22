# accounts-receivable — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "ar_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "ar_agent"
    orgagents_team    = "AYC / Operations / Finance"
    reports_to        = "ap_agent"
    accountable_human = "Sherry, Clark"
  }

  group "ar_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "ar_agent"
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
  #   sandboxes.......... finance_ops
  #   placements......... finance--finance_ops
  #   capabilities....... receivable_management
  #   tools.............. 
  #   decisions it holds. record_receivable
  #   needs approval for. receivable_management
  #   resolved grants.... 2
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
