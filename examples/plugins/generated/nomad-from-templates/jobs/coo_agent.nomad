# chief-operating-officer — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "coo_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "coo_agent"
    orgagents_team    = "AYC / Operations"
    reports_to        = "ceo_agent"
    accountable_human = "Clark, Philip"
  }

  group "coo_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "coo_agent"
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
  #   sandboxes.......... 
  #   placements......... 
  #   capabilities....... 
  #   tools.............. 
  #   decisions it holds. deploy_change, receive_goods
  #   needs approval for. 
  #   resolved grants.... 1
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
