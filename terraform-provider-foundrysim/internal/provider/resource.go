package provider

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"reflect"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/aifoundry-simulator/terraform-provider-foundrysim/internal/client"
)

// spec declares one resource type. The model type T supplies the fields; the
// spec supplies the URL, documentation and the schema for body attributes.
// The id attribute and one attribute per path parameter are added for you.
type spec struct {
	name       string // foundrysim_<name>
	path       string // URL template under /foundry/management/
	doc        string
	attributes map[string]schema.Attribute
}

// managedResource is the one CRUD implementation every resource shares: the
// management API makes each resource a single URL with PUT, GET and DELETE.
// Adapted from apim-simulator's terraform-provider-apimsim.
type managedResource[T any] struct {
	spec   spec
	codec  codec
	client *client.Client
}

// stateReader is what a plan and a state have in common.
type stateReader interface {
	Get(ctx context.Context, target any) diag.Diagnostics
}

var (
	_ resource.ResourceWithConfigure   = (*managedResource[struct{}])(nil)
	_ resource.ResourceWithImportState = (*managedResource[struct{}])(nil)
)

// register builds a resource factory, checking the model against the spec
// once, at provider start-up.
func register[T any](s spec) func() resource.Resource {
	objects := map[string]types.ObjectType{}
	for name, attribute := range s.attributes {
		if object, ok := attribute.GetType().(types.ObjectType); ok {
			objects[name] = object
		}
	}
	c, err := newCodec(reflect.TypeFor[T](), s.path, objects)
	if err != nil {
		panic(fmt.Sprintf("foundrysim_%s: %v", s.name, err))
	}
	return func() resource.Resource { return &managedResource[T]{spec: s, codec: c} }
}

func (r *managedResource[T]) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_" + r.spec.name
}

func (r *managedResource[T]) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	attributes := maps.Clone(r.spec.attributes)
	attributes["id"] = schema.StringAttribute{
		Computed:      true,
		Description:   "The path parameters joined by `/`. Use it to import the resource.",
		PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
	}
	for _, param := range r.codec.params {
		attributes[param.name] = schema.StringAttribute{
			Required:      true,
			Description:   fmt.Sprintf("Identifier in the resource URL (`%s`). Changing it replaces the resource.", r.spec.path),
			PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
		}
	}
	resp.Schema = schema.Schema{Description: r.spec.doc, Attributes: attributes}
}

func (r *managedResource[T]) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	if req.ProviderData == nil {
		return // Terraform validates resources before configuring the provider.
	}
	c, ok := req.ProviderData.(*client.Client)
	if !ok {
		resp.Diagnostics.AddError("Unexpected provider data", fmt.Sprintf("got %T, want *client.Client", req.ProviderData))
		return
	}
	r.client = c
}

func (r *managedResource[T]) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	r.put(ctx, req.Plan, &resp.State, &resp.Diagnostics)
}

func (r *managedResource[T]) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	r.put(ctx, req.Plan, &resp.State, &resp.Diagnostics)
}

func (r *managedResource[T]) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	model, address, ok := r.load(ctx, req.State, &resp.Diagnostics)
	if !ok {
		return
	}
	stored, err := r.client.Get(ctx, address)
	if errors.Is(err, client.ErrNotFound) {
		resp.State.RemoveResource(ctx) // deleted outside Terraform: plan to recreate it
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Cannot read "+address, err.Error())
		return
	}
	r.save(ctx, model, stored, &resp.State, &resp.Diagnostics)
}

func (r *managedResource[T]) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	_, address, ok := r.load(ctx, req.State, &resp.Diagnostics)
	if !ok {
		return
	}
	if err := r.client.Delete(ctx, address); err != nil && !errors.Is(err, client.ErrNotFound) {
		resp.Diagnostics.AddError("Cannot delete "+address, err.Error())
	}
}

func (r *managedResource[T]) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	attributes, err := r.codec.importAttributes(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())
		return
	}
	for name, value := range attributes {
		resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root(name), value)...)
	}
}

func (r *managedResource[T]) put(ctx context.Context, plan stateReader, state *tfsdk.State, diags *diag.Diagnostics) {
	model, address, ok := r.load(ctx, plan, diags)
	if !ok {
		return
	}
	body, err := r.codec.body(model)
	if err != nil {
		diags.AddError("Cannot encode "+address, err.Error())
		return
	}
	stored, err := r.client.Put(ctx, address, body)
	if err != nil {
		diags.AddError("Cannot write "+address, err.Error())
		return
	}
	r.save(ctx, model, stored, state, diags)
}

// load reads the plan or state into a fresh model and works out its URL.
func (r *managedResource[T]) load(ctx context.Context, from stateReader, diags *diag.Diagnostics) (*T, string, bool) {
	model := new(T)
	diags.Append(from.Get(ctx, model)...)
	if diags.HasError() {
		return nil, "", false
	}
	address, err := r.codec.url(model)
	if err != nil {
		diags.AddError("Invalid resource address", err.Error())
		return nil, "", false
	}
	return model, address, true
}

// save folds what the server stored into the model and writes it to state.
func (r *managedResource[T]) save(ctx context.Context, model *T, stored client.Object, state *tfsdk.State, diags *diag.Diagnostics) {
	if err := r.codec.apply(model, stored); err != nil {
		diags.AddError("Unexpected response from the simulator", err.Error())
		return
	}
	diags.Append(state.Set(ctx, model)...)
}

// attributes lists the model's attribute names, for the catalogue test.
func (r *managedResource[T]) attributes() []string { return r.codec.attributes() }
