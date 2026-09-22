# software-engineer — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "software_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "software_agent"
    orgagents_team    = "AYC / Operations / IT"
    reports_to        = "coo_agent"
    accountable_human = "Clark, Philip"
  }

  group "software_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "software_agent"
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
  #   sandboxes.......... build
  #   placements......... it--build
  #   capabilities....... software_deploy
  #   tools.............. 
  #   decisions it holds. deploy_change
  #   needs approval for. software_deploy
  #   resolved grants.... 2
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
