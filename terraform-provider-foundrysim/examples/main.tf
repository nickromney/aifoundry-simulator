terraform {
  required_providers {
    foundrysim = {
      source = "nickromney/foundrysim"
    }
  }
}

# Reads FOUNDRYSIM_ENDPOINT and FOUNDRYSIM_ADMIN_KEY when these are not set.
# The admin key matches examples/foundry.json, the config `make up` starts.
provider "foundrysim" {
  endpoint  = "http://localhost:8020"
  admin_key = "local-foundry-admin-key"
}

resource "foundrysim_blocklist" "codenames" {
  name        = "terraform-codenames"
  description = "Project codenames managed by Terraform"
}

resource "foundrysim_blocklist_item" "falcon" {
  blocklist_name = foundrysim_blocklist.codenames.name
  item_id        = "falcon"
  text           = "project-falcon"
}

resource "foundrysim_content_filter" "strict" {
  name       = "strict"
  categories = { violence = "low", hate = "low" }
  blocklists = [foundrysim_blocklist.codenames.name]
}

resource "foundrysim_deployment" "embeddings" {
  name       = "text-embedding-tf"
  model      = "text-embedding-3-small"
  kind       = "embeddings"
  dimensions = 256
}

resource "foundrysim_deployment" "chat" {
  name           = "gpt-tf"
  model          = "gpt-4.1-mini"
  content_filter = foundrysim_content_filter.strict.name
  latency_ms     = 200

  semantic_cache = {
    enabled               = true
    embeddings_deployment = foundrysim_deployment.embeddings.name
  }
}
