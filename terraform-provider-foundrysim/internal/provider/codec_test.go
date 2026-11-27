package provider

import (
	"context"
	"encoding/json"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"

	"github.com/nickromney/aifoundry-simulator/terraform-provider-foundrysim/internal/client"
)

type childModel struct {
	ID       types.String `tfsdk:"id"`
	Parent   types.String `tfsdk:"parent_id" fdy:"path"`
	Child    types.String `tfsdk:"child_id" fdy:"path"`
	Name     types.String `tfsdk:"name"`
	Limit    types.Int64  `tfsdk:"limit"`
	Tags     types.List   `tfsdk:"tags"`
	Settings types.Object `tfsdk:"settings"`
	Created  types.String `tfsdk:"created" fdy:"read_only"`
}

var settingsType = types.ObjectType{AttrTypes: map[string]attr.Type{
	"enabled":   types.BoolType,
	"threshold": types.Float64Type,
	"ttl":       types.Int64Type,
	"target":    types.StringType,
}}

func childCodec(t *testing.T) codec {
	t.Helper()
	c, err := newCodec(reflect.TypeFor[childModel](), "parents/{parent_id}/children/{child_id}",
		map[string]types.ObjectType{"settings": settingsType})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func settings(enabled attr.Value, threshold attr.Value) types.Object {
	return types.ObjectValueMust(settingsType.AttrTypes, map[string]attr.Value{
		"enabled":   enabled,
		"threshold": threshold,
		"ttl":       types.Int64Unknown(),
		"target":    types.StringNull(),
	})
}

func TestURLEscapesPathParametersInTemplateOrder(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p 1"), Child: types.StringValue("c/2")}

	got, err := childCodec(t).url(m)

	if err != nil || got != "parents/p%201/children/c%2F2" {
		t.Fatalf("url = %q, %v", got, err)
	}
}

func TestURLRejectsUnknownPathParameters(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p"), Child: types.StringUnknown()}

	if _, err := childCodec(t).url(m); err == nil || !strings.Contains(err.Error(), "child_id") {
		t.Fatalf("err = %v", err)
	}
}

func TestBodySendsNullsSkipsUnknownsAndLeavesOutPathAndReadOnly(t *testing.T) {
	m := &childModel{
		Parent:   types.StringValue("p"),
		Child:    types.StringValue("c"),
		Name:     types.StringNull(),
		Limit:    types.Int64Unknown(),
		Tags:     types.ListValueMust(types.StringType, nil),
		Settings: types.ObjectNull(settingsType.AttrTypes),
		Created:  types.StringValue("ignored"),
	}

	body, err := childCodec(t).body(m)

	want := client.Object{"name": nil, "tags": []any{}, "settings": nil}
	if err != nil || !reflect.DeepEqual(body, want) {
		t.Fatalf("body = %#v, %v", body, err)
	}
}

// Inside an object, null and unknown attributes are left out so the server
// fills them from its defaults.
func TestBodySendsOnlyKnownObjectAttributes(t *testing.T) {
	m := &childModel{
		Parent:   types.StringValue("p"),
		Child:    types.StringValue("c"),
		Settings: settings(types.BoolValue(true), types.Float64Value(0.25)),
	}

	body, err := childCodec(t).body(m)

	want := map[string]any{"enabled": true, "threshold": 0.25}
	if err != nil || !reflect.DeepEqual(body["settings"], want) {
		t.Fatalf("settings = %#v, %v", body["settings"], err)
	}
}

func TestApplyDecodesValuesAndNullsUnreturnedComputedValues(t *testing.T) {
	m := &childModel{
		Parent:   types.StringValue("p"),
		Child:    types.StringValue("c"),
		Settings: settings(types.BoolValue(true), types.Float64Unknown()),
		Created:  types.StringUnknown(),
	}
	response := client.Object{
		"name":  "n",
		"limit": json.Number("7"),
		"tags":  []any{"a", "b"},
		"settings": map[string]any{
			"enabled":   true,
			"threshold": json.Number("0.1"),
			"ttl":       json.Number("300"),
		},
	}

	if err := childCodec(t).apply(m, response); err != nil {
		t.Fatal(err)
	}

	if m.ID.ValueString() != "p/c" || m.Name.ValueString() != "n" || m.Limit.ValueInt64() != 7 {
		t.Errorf("id/name/limit = %v %v %v", m.ID, m.Name, m.Limit)
	}
	if !m.Created.IsNull() {
		t.Errorf("unreturned computed value should be null, got %v", m.Created)
	}
	if got := m.Tags.Elements(); len(got) != 2 {
		t.Errorf("tags = %v", got)
	}
	inner := m.Settings.Attributes()
	if inner["threshold"].(types.Float64).ValueFloat64() != 0.1 || inner["ttl"].(types.Int64).ValueInt64() != 300 {
		t.Errorf("settings = %v", m.Settings)
	}
	if !inner["target"].IsNull() {
		t.Errorf("a missing object attribute should read as null, got %v", inner["target"])
	}
}

func TestApplyReadsANullObject(t *testing.T) {
	m := &childModel{Parent: types.StringValue("p"), Child: types.StringValue("c"), Settings: settings(types.BoolValue(true), types.Float64Null())}

	if err := childCodec(t).apply(m, client.Object{"settings": nil}); err != nil {
		t.Fatal(err)
	}
	if !m.Settings.IsNull() {
		t.Fatalf("settings = %v, want null", m.Settings)
	}
}

func TestApplyRejectsWrongJSONTypes(t *testing.T) {
	cases := map[string]client.Object{
		"limit":    {"limit": "seven"},
		"settings": {"settings": map[string]any{"ttl": "long"}},
		"tags":     {"tags": []any{1}},
	}
	for name, response := range cases {
		t.Run(name, func(t *testing.T) {
			m := &childModel{Parent: types.StringValue("p"), Child: types.StringValue("c")}

			err := childCodec(t).apply(m, response)

			if err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("err = %v", err)
			}
		})
	}
}

func TestImportAttributes(t *testing.T) {
	c := childCodec(t)

	got, err := c.importAttributes("p/c")
	if err != nil || !reflect.DeepEqual(got, map[string]string{"id": "p/c", "parent_id": "p", "child_id": "c"}) {
		t.Fatalf("got %v, %v", got, err)
	}
	for _, bad := range []string{"p", "p/c/x", "p/", "/c"} {
		if _, err := c.importAttributes(bad); err == nil || !strings.Contains(err.Error(), "{parent_id}/{child_id}") {
			t.Errorf("%q: err = %v", bad, err)
		}
	}
}

func TestNewCodecRejectsMisdeclaredModels(t *testing.T) {
	type noID struct {
		Name types.String `tfsdk:"name"`
	}
	type badType struct {
		ID   types.String `tfsdk:"id"`
		Name string       `tfsdk:"name"`
	}
	type untagged struct {
		ID   types.String `tfsdk:"id"`
		Name types.String `tfsdk:"name" fdy:"secret"`
	}
	type unusedPath struct {
		ID    types.String `tfsdk:"id"`
		Owner types.String `tfsdk:"owner_id" fdy:"path"`
	}
	type undeclaredObject struct {
		ID       types.String `tfsdk:"id"`
		Settings types.Object `tfsdk:"settings"`
	}
	type nestedList struct {
		ID     types.String `tfsdk:"id"`
		Nested types.Object `tfsdk:"nested"`
	}
	objects := map[string]types.ObjectType{
		"nested": {AttrTypes: map[string]attr.Type{"items": types.ListType{ElemType: types.StringType}}},
	}
	cases := map[string]struct {
		model reflect.Type
		path  string
		want  string
	}{
		"missing id":            {reflect.TypeFor[noID](), "things", "tfsdk:\"id\""},
		"unsupported type":      {reflect.TypeFor[badType](), "things", "unsupported type"},
		"unknown role":          {reflect.TypeFor[untagged](), "things", "unknown fdy tag"},
		"undeclared parameter":  {reflect.TypeFor[unusedPath](), "things/{thing_id}", "{thing_id}"},
		"unused path parameter": {reflect.TypeFor[unusedPath](), "things", "declares 1 path fields"},
		"object not in schema":  {reflect.TypeFor[undeclaredObject](), "things", "no object attribute"},
		"non-scalar in object":  {reflect.TypeFor[nestedList](), "things", "unsupported type"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := newCodec(tc.model, tc.path, objects); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want it to mention %q", err, tc.want)
			}
		})
	}
}

// Every catalogue entry's schema must declare exactly the model's attributes
// and pass the framework's own implementation checks.
func TestEveryResourceSchemaMatchesItsModel(t *testing.T) {
	ctx := context.Background()
	for _, factory := range resources {
		r := factory()
		var meta resource.MetadataResponse
		r.Metadata(ctx, resource.MetadataRequest{ProviderTypeName: "foundrysim"}, &meta)
		t.Run(meta.TypeName, func(t *testing.T) {
			var resp resource.SchemaResponse
			r.Schema(ctx, resource.SchemaRequest{}, &resp)
			if diags := resp.Schema.ValidateImplementation(ctx); diags.HasError() {
				t.Fatalf("schema: %v", diags)
			}

			var schemaNames []string
			for name := range resp.Schema.Attributes {
				schemaNames = append(schemaNames, name)
			}
			modelNames := r.(interface{ attributes() []string }).attributes()
			slices.Sort(schemaNames)
			slices.Sort(modelNames)
			if !slices.Equal(schemaNames, modelNames) {
				t.Fatalf("schema %v\nmodel  %v", schemaNames, modelNames)
			}
		})
	}
}
