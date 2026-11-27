// Package provider is the foundrysim Terraform provider: it configures a
// running AI Foundry simulator through its management API.
package provider

import (
	"cmp"
	"context"
	"os"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/aifoundry-simulator/terraform-provider-foundrysim/internal/client"
)

const defaultEndpoint = "http://localhost:8020"

type foundrysimProvider struct {
	version string
}

var _ provider.Provider = (*foundrysimProvider)(nil)

// New returns the provider factory providerserver.Serve expects.
func New(version string) func() provider.Provider {
	return func() provider.Provider { return &foundrysimProvider{version: version} }
}

func (p *foundrysimProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "foundrysim"
	resp.Version = p.version
}

func (p *foundrysimProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Configures a running AI Foundry simulator through its management API. " +
			"Changes are held in the simulator's memory and lost when it restarts; the next plan then shows them as missing.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Optional:    true,
				Description: "Simulator base URL. Defaults to `FOUNDRYSIM_ENDPOINT`, then " + defaultEndpoint + ".",
			},
			"admin_key": schema.StringAttribute{
				Optional:    true,
				Sensitive:   true,
				Description: "Admin key, sent as `X-Foundry-Admin-Key`. Defaults to `FOUNDRYSIM_ADMIN_KEY`.",
			},
		},
	}
}

type providerConfig struct {
	Endpoint types.String `tfsdk:"endpoint"`
	AdminKey types.String `tfsdk:"admin_key"`
}

func (p *foundrysimProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerConfig
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if cfg.Endpoint.IsUnknown() || cfg.AdminKey.IsUnknown() {
		resp.Diagnostics.AddError(
			"Provider configuration is not known yet",
			"endpoint and admin_key must be known at plan time; they cannot come from a resource that is not created yet.",
		)
		return
	}
	endpoint := cmp.Or(cfg.Endpoint.ValueString(), os.Getenv("FOUNDRYSIM_ENDPOINT"), defaultEndpoint)
	adminKey := cmp.Or(cfg.AdminKey.ValueString(), os.Getenv("FOUNDRYSIM_ADMIN_KEY"))

	resp.ResourceData = client.New(endpoint, adminKey, nil)
}

func (p *foundrysimProvider) Resources(context.Context) []func() resource.Resource {
	return resources
}

func (p *foundrysimProvider) DataSources(context.Context) []func() datasource.DataSource {
	return nil
}
