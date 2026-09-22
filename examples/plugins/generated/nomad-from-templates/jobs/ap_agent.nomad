# accounts-payable — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "ap_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "ap_agent"
    orgagents_team    = "AYC / Operations / Finance"
    reports_to        = "coo_agent"
    accountable_human = "Beverly, Clark"
  }

  group "ap_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "ap_agent"
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
  #   capabilities....... invoice_payment
  #   tools.............. 
  #   decisions it holds. pay_invoice
  #   needs approval for. invoice_payment
  #   resolved grants.... 2
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
