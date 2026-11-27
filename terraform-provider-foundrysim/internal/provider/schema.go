package provider

import (
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64default"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/objectdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

// Attribute constructors. Every attribute the server gives a default to is
// declared with that same default, so leaving it out of configuration resets
// it rather than leaving the old value behind. The server's defaults are the
// config file's (app/config.py); tests/test_management_resources.py checks
// that a PUT applies them.

func required(doc string) schema.StringAttribute {
	return schema.StringAttribute{Required: true, Description: doc}
}

func optional(doc string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Description: doc}
}

func defaulted(doc, value string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString(value), Description: doc}
}

func flag(doc string, value bool) schema.BoolAttribute {
	return schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(value), Description: doc}
}

func defaultedInt(doc string, value int64) schema.Int64Attribute {
	return schema.Int64Attribute{Optional: true, Computed: true, Default: int64default.StaticInt64(value), Description: doc}
}

func defaultedList(doc string, values ...string) schema.ListAttribute {
	elements := make([]attr.Value, len(values))
	for i, v := range values {
		elements[i] = types.StringValue(v)
	}
	return schema.ListAttribute{
		Optional:    true,
		Computed:    true,
		ElementType: types.StringType,
		Default:     listdefault.StaticValue(types.ListValueMust(types.StringType, elements)),
		Description: doc,
	}
}

// Inherited attributes sit inside an optional object whose unset fields the
// server fills from a parent default, such as the root semantic cache
// settings. They are unknown until applied, so the PUT leaves them out.

func inheritedString(doc string) schema.StringAttribute {
	return schema.StringAttribute{Optional: true, Computed: true, Description: doc}
}

func inheritedBool(doc string) schema.BoolAttribute {
	return schema.BoolAttribute{Optional: true, Computed: true, Description: doc}
}

func inheritedInt(doc string) schema.Int64Attribute {
	return schema.Int64Attribute{Optional: true, Computed: true, Description: doc}
}

func inheritedFloat(doc string) schema.Float64Attribute {
	return schema.Float64Attribute{Optional: true, Computed: true, Description: doc}
}

// defaultedStringObject is an object of string attributes that each default
// to the same value, as does the object as a whole when it is left out.
func defaultedStringObject(doc, value string, names map[string]string) schema.SingleNestedAttribute {
	attributes := make(map[string]schema.Attribute, len(names))
	attrTypes := make(map[string]attr.Type, len(names))
	values := make(map[string]attr.Value, len(names))
	for name, innerDoc := range names {
		attributes[name] = defaulted(innerDoc, value)
		attrTypes[name] = types.StringType
		values[name] = types.StringValue(value)
	}
	return schema.SingleNestedAttribute{
		Optional:    true,
		Computed:    true,
		Attributes:  attributes,
		Default:     objectdefault.StaticValue(types.ObjectValueMust(attrTypes, values)),
		Description: doc,
	}
}
