package provider

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-testing/helper/acctest"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"

	"github.com/nickromney/aifoundry-simulator/terraform-provider-foundrysim/internal/client"
)

// Acceptance tests need TF_ACC=1, a terraform binary, and a running simulator
// at FOUNDRYSIM_ENDPOINT (with FOUNDRYSIM_ADMIN_KEY if admin keys are set).
// `make testacc` starts one. After every apply step the harness refreshes and
// plans again, and fails on a non-empty plan: that is the drift check.

var factories = map[string]func() (tfprotov6.ProviderServer, error){
	"foundrysim": providerserver.NewProtocol6WithError(New("test")()),
}

func simulator(t *testing.T) *client.Client {
	t.Helper()
	return client.New(os.Getenv("FOUNDRYSIM_ENDPOINT"), os.Getenv("FOUNDRYSIM_ADMIN_KEY"), nil)
}

func catalogueConfig(suffix, chatExtras, filterExtras, listExtras string) string {
	return fmt.Sprintf(`
resource "foundrysim_blocklist" "codenames" {
  name = "codenames-%[1]s"
  %[4]s
}

resource "foundrysim_blocklist_item" "falcon" {
  blocklist_name = foundrysim_blocklist.codenames.name
  item_id        = "falcon"
  text           = "project-falcon"
}

resource "foundrysim_blocklist_item" "pattern" {
  blocklist_name = foundrysim_blocklist.codenames.name
  item_id        = "pattern"
  text           = "codename-\\w+"
  is_regex       = true
  description    = "Any codename"
}

resource "foundrysim_content_filter" "strict" {
  name       = "strict-%[1]s"
  blocklists = [foundrysim_blocklist.codenames.name]
  %[3]s
}

resource "foundrysim_deployment" "embeddings" {
  name       = "embed-%[1]s"
  model      = "text-embedding-3-small"
  kind       = "embeddings"
  dimensions = 128
}

resource "foundrysim_deployment" "chat" {
  name  = "chat-%[1]s"
  model = "gpt-4.1-mini"
  %[2]s
}
`, suffix, chatExtras, filterExtras, listExtras)
}

func TestAccCatalogue(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	full := catalogueConfig(suffix, fmt.Sprintf(`
  content_filter = foundrysim_content_filter.strict.name
  latency_ms     = 5
  semantic_cache = {
    enabled               = true
    score_threshold       = 0.25
    embeddings_deployment = foundrysim_deployment.embeddings.name
  }
`), `
  categories    = { violence = "low", sexual = "high" }
  prompt_shield = false
`, `description = "Internal codenames"`)
	cleared := catalogueConfig(suffix, "", "", "")

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{
				Config: full,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "id", "chat-"+suffix),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "kind", "chat"),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "content_filter", "strict-"+suffix),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "semantic_cache.score_threshold", "0.25"),
					// Left out of the block: filled from the config file's root defaults.
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "semantic_cache.ttl_seconds", "300"),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "semantic_cache.max_entries", "512"),
					resource.TestCheckResourceAttr("foundrysim_deployment.embeddings", "dimensions", "128"),
					resource.TestCheckResourceAttr("foundrysim_content_filter.strict", "categories.violence", "low"),
					resource.TestCheckResourceAttr("foundrysim_content_filter.strict", "categories.hate", "medium"),
					resource.TestCheckResourceAttr("foundrysim_content_filter.strict", "prompt_shield", "false"),
					resource.TestCheckResourceAttr("foundrysim_blocklist_item.falcon", "id", "codenames-"+suffix+"/falcon"),
					resource.TestCheckResourceAttr("foundrysim_blocklist_item.pattern", "is_regex", "true"),
				),
			},
			{
				// Removing attributes must reset them on the server: no
				// filter, inherited cache settings, default categories.
				Config: cleared,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckNoResourceAttr("foundrysim_deployment.chat", "content_filter"),
					resource.TestCheckNoResourceAttr("foundrysim_deployment.chat", "semantic_cache"),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "latency_ms", "0"),
					resource.TestCheckResourceAttr("foundrysim_content_filter.strict", "categories.violence", "medium"),
					resource.TestCheckResourceAttr("foundrysim_content_filter.strict", "prompt_shield", "true"),
					resource.TestCheckResourceAttr("foundrysim_blocklist.codenames", "description", ""),
					checkServer(t, "deployments/chat-"+suffix, "content_filter", nil),
				),
			},
			{Config: full},
			importStep("foundrysim_deployment.chat"),
			importStep("foundrysim_deployment.embeddings"),
			importStep("foundrysim_content_filter.strict"),
			importStep("foundrysim_blocklist.codenames"),
			importStep("foundrysim_blocklist_item.falcon"),
			importStep("foundrysim_blocklist_item.pattern"),
		},
	})
}

// TestAccDriftIsDetectedAndRepaired: a change made through the management API,
// or a restart that loses runtime state, shows in the next plan, and the
// next apply puts the configured state back.
func TestAccDriftIsDetectedAndRepaired(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	config := fmt.Sprintf(`
resource "foundrysim_deployment" "drift" {
  name           = "drift-%[1]s"
  model          = "gpt-4o"
  content_filter = "default"
}
`, suffix)
	address := "deployments/drift-" + suffix
	ctx := context.Background()

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{Config: config},
			{
				PreConfig: func() {
					if _, err := simulator(t).Put(ctx, address, client.Object{"model": "changed-elsewhere"}); err != nil {
						t.Fatal(err)
					}
				},
				Config:             config,
				PlanOnly:           true,
				ExpectNonEmptyPlan: true,
			},
			{
				Config: config,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("foundrysim_deployment.drift", "model", "gpt-4o"),
					checkServer(t, address, "model", "gpt-4o"),
				),
			},
			{
				PreConfig: func() {
					if err := simulator(t).Delete(ctx, address); err != nil {
						t.Fatal(err)
					}
				},
				Config: config,
				Check:  checkServer(t, address, "model", "gpt-4o"),
			},
		},
	})
}

// TestAccUpdateKeepsInheritedCacheSettingsStable: an in-place update makes the
// inherited cache attributes unknown, so the PUT leaves them out and the
// server fills them again. The follow-up plan must be empty.
func TestAccUpdateKeepsInheritedCacheSettingsStable(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	config := func(model string) string {
		return fmt.Sprintf(`
resource "foundrysim_deployment" "embeddings" {
  name  = "upd-embed-%[1]s"
  model = "text-embedding-3-small"
  kind  = "embeddings"
}

resource "foundrysim_deployment" "chat" {
  name  = "upd-chat-%[1]s"
  model = %[2]q
  semantic_cache = {
    enabled               = true
    embeddings_deployment = foundrysim_deployment.embeddings.name
  }
}
`, suffix, model)
	}

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{Config: config("gpt-4o")},
			{
				Config: config("gpt-4.1"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "model", "gpt-4.1"),
					resource.TestCheckResourceAttr("foundrysim_deployment.chat", "semantic_cache.score_threshold", "0.1"),
				),
			},
		},
	})
}

func importStep(address string, ignore ...string) resource.TestStep {
	return resource.TestStep{
		ResourceName:            address,
		ImportState:             true,
		ImportStateVerify:       true,
		ImportStateVerifyIgnore: ignore,
	}
}

// checkServer reads a resource straight from the simulator, bypassing state.
func checkServer(t *testing.T, address, key string, want any) resource.TestCheckFunc {
	return func(*terraform.State) error {
		stored, err := simulator(t).Get(context.Background(), address)
		if err != nil {
			return err
		}
		if got := stored[key]; got != want {
			return fmt.Errorf("%s: %s = %#v, want %#v", address, key, got, want)
		}
		return nil
	}
}
