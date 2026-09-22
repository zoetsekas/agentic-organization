# marketer — generated from the ayc design. Do not hand-edit;
# change the spec and recompile.
job "marketing_agent" {
  datacenters = ["dc1"]
  type        = "service"

  meta {
    # Provenance, so an operator can trace a running job back to the design.
    orgagents_system  = "ayc"
    orgagents_agent   = "marketing_agent"
    orgagents_team    = "AYC / Growth / Marketing"
    reports_to        = "cgo_agent"
    accountable_human = "Lea, Ping"
  }

  group "marketing_agent" {
    count = 1

    task "agent" {
      driver = "docker"

      config {
        image = "anthropic-agent:latest"
      }

      env {
        ORGAGENTS_AGENT_ID = "marketing_agent"
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
  #   sandboxes.......... studio
  #   placements......... marketing--studio
  #   capabilities....... content_publishing, promotion_run
  #   tools.............. 
  #   decisions it holds. publish_content, run_promotion
  #   needs approval for. promotion_run
  #   resolved grants.... 3
  # A scheduler cannot hold a mandate. Deploy `terraform:gcp` or `local`
  # alongside if you want those enforced rather than documented.
}
