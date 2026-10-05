use std::path::PathBuf;

use gen_lsp_types::{LspAny, LspObject};
use serde::{Deserialize, Serialize};

/// Explicit compiler exports prepared before the editor session starts.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct RustdocConfig {
    pub inputs: Vec<RustdocInputConfig>,
}

impl RustdocConfig {
    pub fn from_initialization_options(options: Option<&LspAny>) -> anyhow::Result<Self> {
        let Some(value) = options
            .and_then(LspAny::as_object)
            .and_then(|o| o.get("rustdoc"))
        else {
            return Ok(Self::default());
        };
        // An explicitly malformed input must stop initialization: dropping it would silently
        // publish source-only analysis for a model the developer asked us to import.
        let section = value
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("rust-glancer rustdoc must be an object"))?;
        let Some(inputs) = section.get("inputs") else {
            return Ok(Self::default());
        };
        let inputs = inputs
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("rust-glancer rustdoc.inputs must be an array"))?;
        let inputs = inputs
            .iter()
            .enumerate()
            .map(|(index, value)| RustdocInputConfig::parse(value, index))
            .collect::<anyhow::Result<Vec<_>>>()?;
        Ok(Self { inputs })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RustdocInputConfig {
    pub workspace_root: PathBuf,
    pub manifest_path: PathBuf,
    pub target_name: String,
    pub target_kind: RustdocTargetKind,
    pub export_path: PathBuf,
    pub item_path: String,
}

impl RustdocInputConfig {
    fn parse(value: &LspAny, index: usize) -> anyhow::Result<Self> {
        let input = value.as_object().ok_or_else(|| {
            anyhow::anyhow!("rust-glancer rustdoc.inputs[{index}] must be an object")
        })?;
        let target_kind = match Self::required_string(input, index, "targetKind")? {
            "lib" => RustdocTargetKind::Lib,
            "bin" => RustdocTargetKind::Bin,
            _ => {
                anyhow::bail!("rust-glancer rustdoc.inputs[{index}].targetKind must be lib or bin")
            }
        };
        Ok(Self {
            workspace_root: Self::required_string(input, index, "workspaceRoot")?.into(),
            manifest_path: Self::required_string(input, index, "manifestPath")?.into(),
            target_name: Self::required_string(input, index, "targetName")?.to_owned(),
            target_kind,
            export_path: Self::required_string(input, index, "exportPath")?.into(),
            item_path: Self::required_string(input, index, "itemPath")?.to_owned(),
        })
    }

    fn required_string<'a>(
        input: &'a LspObject,
        index: usize,
        field: &str,
    ) -> anyhow::Result<&'a str> {
        let value = input.get(field).and_then(LspAny::as_str).ok_or_else(|| {
            anyhow::anyhow!("rust-glancer rustdoc.inputs[{index}].{field} must be a string")
        })?;
        anyhow::ensure!(
            !value.trim().is_empty(),
            "rust-glancer rustdoc.inputs[{index}].{field} must not be empty",
        );
        // Preserve the spelling of paths and compiler identities; only the server resolves paths.
        Ok(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RustdocTargetKind {
    Lib,
    Bin,
}
