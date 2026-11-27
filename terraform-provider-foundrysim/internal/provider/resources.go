package provider

import (
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

const severityLevels = "`low`, `medium`, `high` or `off`."

// The resource catalogue. Each entry is a model (the fields and their roles,
// see codec.go) and a spec (URL, docs, schema for the body attributes). Names
// and defaults follow the config file, which app/config.py parses for both
// startup and the management API's PUT bodies.
var resources = []func() resource.Resource{
	register[deploymentModel](spec{
		name: "deployment",
		path: "deployments/{name}",
		doc: "A model deployment. Its name is the routing identity callers use; " +
			"the model name is metadata the simulator reports.",
		attributes: map[string]schema.Attribute{
			"model": required("Model name the deployment reports, such as `gpt-4o-mini`."),
			"kind":  defaulted("`chat` or `embeddings`.", "chat"),
			"content_filter": optional("Name of the `foundrysim_content_filter` applied to prompts and completions. " +
				"Leave it out for no filtering. Unlike the config file, where an omitted filter means `default`, " +
				"Terraform cannot tell an omitted attribute from `null`, so set `\"default\"` to use the built-in policy."),
			"latency_ms": defaultedInt("Simulated latency added to each response, in milliseconds.", 0),
			"dimensions": defaultedInt("Vector length for an `embeddings` deployment, 8 to 4096.", 256),
			"semantic_cache": schema.SingleNestedAttribute{
				Optional: true,
				Description: "Semantic cache settings for this deployment. Leave it out to inherit the resource-level " +
					"defaults from the config file. Attributes left out of the block take those defaults too.",
				Attributes: map[string]schema.Attribute{
					"enabled":               inheritedBool("Whether chat answers are cached."),
					"score_threshold":       inheritedFloat("Maximum vector distance for a hit, 0.0 to 1.0. Lower is stricter."),
					"ttl_seconds":           inheritedInt("How long an entry lives, in seconds."),
					"max_entries":           inheritedInt("Entries kept for this deployment before the oldest is evicted."),
					"embeddings_deployment": inheritedString("Name of the `embeddings` deployment that embeds prompts."),
				},
			},
		},
	}),
	register[contentFilterModel](spec{
		name: "content_filter",
		path: "content-filters/{name}",
		doc:  "A content filter (RAI policy) that deployments apply to prompts and completions.",
		attributes: map[string]schema.Attribute{
			"categories": defaultedStringObject("Level each harm category blocks at: "+severityLevels, "medium", map[string]string{
				"hate":      "Hate and fairness: " + severityLevels,
				"self_harm": "Self-harm: " + severityLevels,
				"sexual":    "Sexual: " + severityLevels,
				"violence":  "Violence: " + severityLevels,
			}),
			"prompt_shield": flag("Whether prompts are checked for jailbreak attempts.", true),
			"blocklists":    defaultedList("Names of the `foundrysim_blocklist` resources checked against prompts."),
			"output_filter": flag("Whether completions are filtered too.", true),
		},
	}),
	register[blocklistModel](spec{
		name: "blocklist",
		path: "blocklists/{name}",
		doc:  "A Content Safety text blocklist. Its items are `foundrysim_blocklist_item` resources.",
		attributes: map[string]schema.Attribute{
			"description": defaulted("Description.", ""),
		},
	}),
	register[blocklistItemModel](spec{
		name: "blocklist_item",
		path: "blocklists/{blocklist_name}/items/{item_id}",
		doc: "One term in a blocklist. Items from the config file have the IDs `<blocklist>-<index>`, " +
			"so they can be imported.",
		attributes: map[string]schema.Attribute{
			"text":        required("Text to match, or a regular expression when `is_regex` is true."),
			"is_regex":    flag("Whether `text` is a regular expression.", false),
			"description": defaulted("Description.", ""),
		},
	}),
}

type deploymentModel struct {
	ID            types.String `tfsdk:"id"`
	Name          types.String `tfsdk:"name" fdy:"path"`
	Model         types.String `tfsdk:"model"`
	Kind          types.String `tfsdk:"kind"`
	ContentFilter types.String `tfsdk:"content_filter"`
	LatencyMS     types.Int64  `tfsdk:"latency_ms"`
	Dimensions    types.Int64  `tfsdk:"dimensions"`
	SemanticCache types.Object `tfsdk:"semantic_cache"`
}

type contentFilterModel struct {
	ID           types.String `tfsdk:"id"`
	Name         types.String `tfsdk:"name" fdy:"path"`
	Categories   types.Object `tfsdk:"categories"`
	PromptShield types.Bool   `tfsdk:"prompt_shield"`
	Blocklists   types.List   `tfsdk:"blocklists"`
	OutputFilter types.Bool   `tfsdk:"output_filter"`
}

type blocklistModel struct {
	ID          types.String `tfsdk:"id"`
	Name        types.String `tfsdk:"name" fdy:"path"`
	Description types.String `tfsdk:"description"`
}

type blocklistItemModel struct {
	ID            types.String `tfsdk:"id"`
	BlocklistName types.String `tfsdk:"blocklist_name" fdy:"path"`
	ItemID        types.String `tfsdk:"item_id" fdy:"path"`
	Text          types.String `tfsdk:"text"`
	IsRegex       types.Bool   `tfsdk:"is_regex"`
	Description   types.String `tfsdk:"description"`
}
