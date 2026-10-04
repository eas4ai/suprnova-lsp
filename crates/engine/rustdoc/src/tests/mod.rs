use rustdoc_types::{GenericParamDefKind, ItemEnum, Type, Visibility};
use serde_json::Value;

use super::RustdocExport;

const FIXTURE: &[u8] = include_bytes!("../../fixtures/model/export.json");

#[test]
fn lowering_rejects_an_ambiguous_defining_crate_instead_of_using_source_spelling() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let roots = std::collections::BTreeMap::from([("rustdoc_macro_support".into(), None)]);
    let error = export
        .lower_type("rustdoc_macro_support::Post", &roots)
        .unwrap_err();
    assert!(format!("{error:#}").contains("defining crate rustdoc_macro_support is ambiguous"));
}

fn changed_export(change: impl FnOnce(&mut Value)) -> Vec<u8> {
    let mut value: Value = serde_json::from_slice(FIXTURE).unwrap();
    change(&mut value);
    serde_json::to_vec(&value).unwrap()
}

fn argument_impl_trait_export(change: impl FnOnce(&mut Value)) -> Vec<u8> {
    changed_export(|value| {
        let trait_id = value["paths"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, item)| item["path"] == serde_json::json!(["rustdoc_macro_support", "Model"]))
            .unwrap()
            .0
            .parse::<u64>()
            .unwrap();
        let function = &mut value["index"]
            .as_object_mut()
            .unwrap()
            .values_mut()
            .find(|item| item["name"] == "generated_method")
            .unwrap()["inner"]["function"];
        let bounds = serde_json::json!([{"trait_bound": {"trait": {"path": "Model", "id": trait_id, "args": null}, "generic_params": [], "modifier": "none"}}]);
        function["sig"]["inputs"][1][1] = serde_json::json!({"impl_trait": bounds});
        function["generics"]["params"].as_array_mut().unwrap().push(serde_json::json!({"name": "impl Model", "kind": {"type": {"bounds": bounds, "default": null, "is_synthetic": true}}}));
        change(function);
    })
}

#[test]
fn preserves_argument_impl_trait_bounds_without_an_explicit_synthetic_parameter() {
    let bytes = argument_impl_trait_export(|_| {});
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    let lowered = export
        .type_api("rustdoc_macro_support::Post")
        .unwrap()
        .lower()
        .unwrap();
    let function = lowered
        .items
        .iter()
        .find(|item| {
            item.name
                .as_ref()
                .is_some_and(|name| name.as_str() == "generated_method")
        })
        .unwrap();
    let rg_item_tree::ItemKind::Function(function) = &function.kind else {
        unreachable!()
    };
    assert_eq!(
        function
            .generics
            .type_param_names()
            .map(|name| name.as_str())
            .collect::<Vec<_>>(),
        ["T"]
    );
    let Some(rg_item_tree::TypeRef::ImplTrait(bounds)) = &function.params[1].ty else {
        panic!("opaque argument required")
    };
    assert_eq!(bounds.len(), 1);
    assert_eq!(bounds[0].trait_ty().unwrap().to_string(), "crate::Model");
}

#[test]
fn rejects_a_synthetic_parameter_without_matching_argument_bounds() {
    let bytes = argument_impl_trait_export(|function| {
        function["sig"]["inputs"][1][1] = serde_json::json!({"generic": "T"})
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(
        export
            .type_api("rustdoc_macro_support::Post")
            .unwrap()
            .lower()
            .is_err()
    );
}

#[test]
fn rejects_a_reference_to_a_removed_synthetic_parameter() {
    let bytes = argument_impl_trait_export(|function| {
        function["sig"]["output"] = serde_json::json!({"generic": "impl Model"})
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(
        export
            .type_api("rustdoc_macro_support::Post")
            .unwrap()
            .lower()
            .is_err()
    );
}

#[test]
fn preserves_trait_object_bounds_and_lifetime() {
    let bytes = argument_impl_trait_export(|function| {
        let trait_ =
            function["sig"]["inputs"][1][1]["impl_trait"][0]["trait_bound"]["trait"].clone();
        function["sig"]["output"] = serde_json::json!({"dyn_trait": {"traits": [{"trait": trait_, "generic_params": []}], "lifetime": "'static"}});
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    let lowered = export
        .type_api("rustdoc_macro_support::Post")
        .unwrap()
        .lower()
        .unwrap();
    let function = lowered
        .items
        .iter()
        .find(|item| {
            item.name
                .as_ref()
                .is_some_and(|name| name.as_str() == "generated_method")
        })
        .unwrap();
    let rg_item_tree::ItemKind::Function(function) = &function.kind else {
        unreachable!()
    };
    let Some(rg_item_tree::TypeRef::DynTrait(bounds)) = &function.ret_ty else {
        panic!("object type required")
    };
    assert_eq!(bounds.len(), 2);
    assert_eq!(bounds[0].trait_ty().unwrap().to_string(), "crate::Model");
    assert!(
        matches!(&bounds[1], rg_item_tree::TypeBound::Lifetime(name) if name.as_str() == "'static")
    );
}

#[test]
fn selects_explicit_impls_for_the_exact_type() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    assert_eq!(api.path.join("::"), "rustdoc_macro_support::Post");
    assert_eq!(api.impls.len(), 6);
    let mut names = api
        .impls
        .iter()
        .flat_map(|implementation| &implementation.associated_items)
        .filter_map(|item| item.name.as_deref())
        .collect::<Vec<_>>();
    names.sort_unstable();
    assert_eq!(
        names,
        [
            "Key",
            "bounded",
            "generated_method",
            "hidden_method",
            "private_method",
            "query",
            "source_method"
        ]
    );

    // A second `Post` in another module must not inherit the first one's methods.
    let other = export
        .type_api("rustdoc_macro_support::other::Post")
        .unwrap();
    assert!(other.impls.is_empty());
}

#[test]
fn preserves_generated_trait_impl_and_associated_type() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    let implementation = api.impls.iter().find(|implementation| {
        matches!(&implementation.declaration.inner, ItemEnum::Impl(data) if data.trait_.as_ref().is_some_and(|path| path.path == "Model"))
    }).unwrap();
    let ItemEnum::Impl(data) = &implementation.declaration.inner else {
        unreachable!()
    };
    let trait_path = data.trait_.as_ref().unwrap();
    assert_eq!(
        export.resolve_path(trait_path.id).unwrap().path,
        ["rustdoc_macro_support", "Model"]
    );
    let key = implementation
        .associated_items
        .iter()
        .find(|item| item.name.as_deref() == Some("Key"))
        .unwrap();
    assert!(
        matches!(&key.inner, ItemEnum::AssocType { type_: Some(Type::Primitive(name)), .. } if name == "u64")
    );
    let query = implementation
        .associated_items
        .iter()
        .find(|item| item.name.as_deref() == Some("query"))
        .unwrap();
    let ItemEnum::Function(function) = &query.inner else {
        unreachable!()
    };
    let Some(Type::ResolvedPath(output)) = &function.sig.output else {
        panic!("query must return Builder<Self>")
    };
    assert_eq!(
        export.resolve_path(output.id).unwrap().path,
        ["rustdoc_macro_support", "Builder"]
    );
    let Some(rustdoc_types::GenericArgs::AngleBracketed { args, .. }) = output.args.as_deref()
    else {
        panic!("query must retain its generic argument")
    };
    assert!(
        matches!(&args[0], rustdoc_types::GenericArg::Type(Type::Generic(name)) if name == "Self")
    );
}

#[test]
fn preserves_generic_method_signature_and_visibility() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    let items = api
        .impls
        .iter()
        .flat_map(|implementation| &implementation.associated_items)
        .collect::<Vec<_>>();
    let method = items
        .iter()
        .find(|item| item.name.as_deref() == Some("generated_method"))
        .unwrap();
    let ItemEnum::Function(function) = &method.inner else {
        unreachable!()
    };
    assert_eq!(function.generics.params.len(), 1);
    assert_eq!(function.generics.params[0].name, "T");
    assert!(matches!(
        &function.generics.params[0].kind,
        GenericParamDefKind::Type { .. }
    ));
    assert_eq!(
        function.sig.inputs[1],
        ("value".to_owned(), Type::Generic("T".to_owned()))
    );
    let Some(Type::ResolvedPath(output)) = &function.sig.output else {
        panic!("generated method must return Builder<T>")
    };
    assert_eq!(
        export.resolve_path(output.id).unwrap().path,
        ["rustdoc_macro_support", "Builder"]
    );
    let private = items
        .iter()
        .find(|item| item.name.as_deref() == Some("private_method"))
        .unwrap();
    // A private method in the crate root is accessible throughout that crate.
    assert_eq!(private.visibility, Visibility::Crate);
}

#[test]
fn rejects_format_mismatch_before_decoding_item_shapes() {
    let bytes = changed_export(|value| {
        value["format_version"] = Value::from(rustdoc_types::FORMAT_VERSION + 1);
        value["index"] = Value::String("a future schema".to_owned());
    });
    let error = RustdocExport::read(bytes.as_slice())
        .err()
        .unwrap()
        .to_string();
    assert!(error.contains("unsupported rustdoc JSON format"), "{error}");
}

#[test]
fn rejects_export_without_private_items() {
    let bytes = changed_export(|value| value["includes_private"] = Value::Bool(false));
    let error = RustdocExport::read(bytes.as_slice())
        .err()
        .unwrap()
        .to_string();
    assert!(error.contains("--document-private-items"), "{error}");
}

#[test]
fn rejects_missing_root() {
    let bytes = changed_export(|value| {
        let root = value["root"].as_u64().unwrap().to_string();
        value["index"].as_object_mut().unwrap().remove(&root);
    });
    let error = RustdocExport::read(bytes.as_slice())
        .err()
        .unwrap()
        .to_string();
    assert!(error.contains("root"), "{error}");
}

#[test]
fn rejects_root_that_is_not_a_crate_module() {
    let bytes = changed_export(|value| {
        let root = value["root"].as_u64().unwrap().to_string();
        value["index"][root]["inner"]["module"]["is_crate"] = Value::Bool(false);
    });
    assert!(RustdocExport::read(bytes.as_slice()).is_err());
}

#[test]
fn rejects_mismatched_index_identity() {
    let bytes = changed_export(|value| {
        let root = value["root"].as_u64().unwrap().to_string();
        value["index"][root]["id"] = Value::from(999999);
    });
    assert!(RustdocExport::read(bytes.as_slice()).is_err());
}

#[test]
fn requires_a_fully_qualified_local_type() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    for path in [
        "Post",
        "missing::Post",
        "rustdoc_macro_support",
        "rustdoc_macro_support::Model",
    ] {
        assert!(export.type_api(path).is_err(), "accepted {path}");
    }
}

#[test]
fn rejects_missing_associated_item() {
    let bytes = changed_export(|value| {
        let items = value["index"].as_object_mut().unwrap();
        let method = items
            .iter()
            .find(|(_, item)| item["name"] == "generated_method")
            .unwrap()
            .0
            .clone();
        items.remove(&method);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    let error = export
        .type_api("rustdoc_macro_support::Post")
        .err()
        .unwrap()
        .to_string();
    assert!(error.contains("associated item"), "{error}");
}

#[test]
fn rejects_impl_attached_to_the_wrong_type() {
    let bytes = changed_export(|value| {
        let other_id = value["paths"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, path)| {
                path["path"] == serde_json::json!(["rustdoc_macro_support", "other", "Post"])
            })
            .unwrap()
            .0
            .parse::<u64>()
            .unwrap();
        let items = value["index"].as_object_mut().unwrap();
        let generated_impl = items
            .values_mut()
            .find(|item| item["inner"]["impl"]["trait"]["path"] == "Model")
            .unwrap();
        generated_impl["inner"]["impl"]["for"]["resolved_path"]["id"] = Value::from(other_id);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(export.type_api("rustdoc_macro_support::Post").is_err());
}

#[test]
fn preserves_trait_argument_attachment_without_changing_the_receiver() {
    let bytes = changed_export(|value| {
        let paths = value["paths"].as_object().unwrap();
        let owner = paths
            .iter()
            .find(|(_, item)| {
                item["kind"] == "struct"
                    && item["path"] == serde_json::json!(["rustdoc_macro_support", "Post"])
            })
            .unwrap()
            .0
            .parse::<u64>()
            .unwrap();
        let other = paths
            .iter()
            .find(|(_, item)| {
                item["kind"] == "struct"
                    && item["path"] == serde_json::json!(["rustdoc_macro_support", "other", "Post"])
            })
            .unwrap()
            .0
            .parse::<u64>()
            .unwrap();
        let implementation = value["index"]
            .as_object_mut()
            .unwrap()
            .values_mut()
            .find(|item| item["inner"]["impl"]["trait"]["path"] == "Model")
            .unwrap();
        let data = &mut implementation["inner"]["impl"];
        data["for"]["resolved_path"]["id"] = Value::from(other);
        data["trait"]["args"] = serde_json::json!({"angle_bracketed": {"args": [{"type": {"resolved_path": {"path": "Post", "id": owner, "args": null}}}], "constraints": []}});
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    let reverse = api.impls.iter().find(|implementation| matches!(&implementation.declaration.inner, ItemEnum::Impl(data) if data.trait_.as_ref().is_some_and(|path| path.path == "Model"))).unwrap();
    let ItemEnum::Impl(data) = &reverse.declaration.inner else {
        unreachable!()
    };
    let Type::ResolvedPath(receiver) = &data.for_ else {
        panic!("nominal receiver required")
    };
    assert_eq!(
        export.resolve_path(receiver.id).unwrap().path,
        ["rustdoc_macro_support", "other", "Post"]
    );
    // Inspecting an attachment must not install its methods onto the selected nominal owner.
    let lowered = api.lower().unwrap();
    assert!(lowered.impls.iter().all(|id| !matches!(&lowered.items[*id].kind, rg_item_tree::ItemKind::Impl(data) if data.trait_ref.as_ref().is_some_and(|ty| ty.to_string().ends_with("Model")))));
}

#[test]
fn rejects_trailing_data_and_invalid_json() {
    let mut bytes = FIXTURE.to_vec();
    bytes.extend_from_slice(b" {}");
    for input in [bytes.as_slice(), b"{", b""] {
        assert!(RustdocExport::read(input).is_err());
    }
}

#[test]
fn preserves_impls_for_references_to_the_selected_type() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    assert!(api.impls.iter().any(|implementation| {
        matches!(&implementation.declaration.inner, ItemEnum::Impl(data) if matches!(&data.for_, Type::BorrowedRef { .. }))
    }));
}

#[test]
fn preserves_impls_for_boxed_selected_types() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    assert!(api.impls.iter().any(|implementation| {
        matches!(&implementation.declaration.inner, ItemEnum::Impl(data) if matches!(&data.for_, Type::ResolvedPath(path) if path.path == "Box"))
    }));
}

#[test]
fn rejects_missing_signature_paths() {
    let bytes = changed_export(|value| {
        let builder_id = value["paths"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, path)| {
                path["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"])
            })
            .unwrap()
            .0
            .clone();
        value["paths"].as_object_mut().unwrap().remove(&builder_id);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    let error = export
        .type_api("rustdoc_macro_support::Post")
        .err()
        .unwrap()
        .to_string();
    assert!(error.contains("type path"), "{error}");
}

#[test]
fn rejects_missing_local_signature_declarations() {
    let bytes = changed_export(|value| {
        let builder_id = value["paths"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, path)| {
                path["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"])
            })
            .unwrap()
            .0
            .clone();
        value["index"].as_object_mut().unwrap().remove(&builder_id);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(export.type_api("rustdoc_macro_support::Post").is_err());
}

#[test]
fn retains_fields_and_variants_with_their_signatures() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let payload = export.type_api("rustdoc_macro_support::Payload").unwrap();
    assert_eq!(payload.members.len(), 5);
    assert_eq!(
        payload
            .members
            .iter()
            .filter(|item| matches!(item.inner, ItemEnum::Variant(_)))
            .count(),
        3
    );
    let id = export.type_api("rustdoc_macro_support::Id").unwrap();
    assert_eq!(id.members.len(), 1);
    assert!(
        matches!(&id.members[0].inner, ItemEnum::StructField(Type::Primitive(name)) if name == "u64")
    );
}

#[test]
fn retains_nested_bounds_and_external_type_paths() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export.type_api("rustdoc_macro_support::Post").unwrap();
    assert!(api.type_paths.values().any(|path| {
        path.path.last().is_some_and(|name| name == "IntoIterator") && path.crate_id != 0
    }));
    let bytes = changed_export(|value| {
        let iterator = value["paths"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, path)| path["path"].as_array().unwrap().last().unwrap() == "IntoIterator")
            .unwrap()
            .0
            .clone();
        value["paths"].as_object_mut().unwrap().remove(&iterator);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(export.type_api("rustdoc_macro_support::Post").is_err());
}

#[test]
fn validates_module_restricted_visibility() {
    let export = RustdocExport::read(FIXTURE).unwrap();
    let api = export
        .type_api("rustdoc_macro_support::scoped::inner::Packet")
        .unwrap();
    assert!(matches!(
        api.members[0].visibility,
        Visibility::Restricted { .. }
    ));
    let bytes = changed_export(|value| {
        let item = value["index"]
            .as_object_mut()
            .unwrap()
            .values_mut()
            .find(|item| item["name"] == "payload")
            .unwrap();
        item["visibility"]["restricted"]["parent"] = Value::from(999999);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(
        export
            .type_api("rustdoc_macro_support::scoped::inner::Packet")
            .is_err()
    );
}

#[test]
fn rejects_missing_fields() {
    let bytes = changed_export(|value| {
        let id = value["index"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(_, item)| item["name"] == "payload")
            .unwrap()
            .0
            .clone();
        value["index"].as_object_mut().unwrap().remove(&id);
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(
        export
            .type_api("rustdoc_macro_support::scoped::inner::Packet")
            .is_err()
    );
}

#[test]
fn stops_unwrapping_at_the_selected_box_type() {
    let bytes = changed_export(|value| {
        let root = value["root"].as_u64().unwrap().to_string();
        value["index"][&root]["name"] = Value::from("alloc");
        for summary in value["paths"].as_object_mut().unwrap().values_mut() {
            if summary["path"][0] == "rustdoc_macro_support" {
                summary["path"][0] = Value::from("alloc");
            }
            if summary["kind"] == "struct"
                && summary["path"] == serde_json::json!(["alloc", "Post"])
            {
                summary["path"] = serde_json::json!(["alloc", "boxed", "Box"]);
            }
        }
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(
        !export
            .type_api("alloc::boxed::Box")
            .unwrap()
            .impls
            .is_empty()
    );
}

#[test]
fn rejects_enum_variants_in_a_struct_field_list() {
    let bytes = changed_export(|value| {
        let items = value["index"].as_object_mut().unwrap();
        let variant = items.values().find(|item| item["name"] == "Empty").unwrap()["id"].clone();
        let post = items
            .values_mut()
            .find(|item| {
                item["name"] == "Post"
                    && item["inner"].get("struct").is_some()
                    && item["inner"]["struct"]["kind"].get("plain").is_some()
            })
            .unwrap();
        post["inner"]["struct"]["kind"]["plain"]["fields"][0] = variant;
    });
    let export = RustdocExport::read(bytes.as_slice()).unwrap();
    assert!(export.type_api("rustdoc_macro_support::Post").is_err());
}
