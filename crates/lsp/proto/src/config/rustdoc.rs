use std::path::PathBuf;

use gen_lsp_types::{LspAny, LspObject};
use serde::{Deserialize, Serialize};

/// Compiler declarations supplied explicitly or generated from saved workspace inputs.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct RustdocConfig {
    pub inputs: Vec<RustdocInputConfig>,
    pub automatic: RustdocAutomaticConfig,
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
            .ok_or_else(|| anyhow::anyhow!("suprnova-lsp rustdoc must be an object"))?;
        let inputs = match section.get("inputs") {
            None => Vec::new(),
            Some(inputs) => inputs
                .as_array()
                .ok_or_else(|| anyhow::anyhow!("suprnova-lsp rustdoc.inputs must be an array"))?
                .iter()
                .enumerate()
                .map(|(index, value)| RustdocInputConfig::parse(value, index))
                .collect::<anyhow::Result<Vec<_>>>()?,
        };
        Ok(Self {
            inputs,
            automatic: RustdocAutomaticConfig::parse(section.get("automatic"))?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RustdocAutomaticConfig {
    pub enabled: bool,
    pub debounce_ms: u64,
    pub toolchain: String,
    pub timeout_ms: u64,
    pub jobs: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifact_root: Option<PathBuf>,
}

impl RustdocAutomaticConfig {
    fn parse(value: Option<&LspAny>) -> anyhow::Result<Self> {
        let Some(value) = value else {
            return Ok(Self::default());
        };
        let section = value
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("suprnova-lsp rustdoc.automatic must be an object"))?;
        let mut config = Self::default();
        if let Some(value) = section.get("enabled") {
            config.enabled = value.as_bool().ok_or_else(|| {
                anyhow::anyhow!("suprnova-lsp rustdoc.automatic.enabled must be a boolean")
            })?;
        }
        // Keep policy bounded before it reaches timers or Cargo's parallel job count.
        for (field, maximum, destination) in [
            ("debounceMs", 600_000, &mut config.debounce_ms),
            ("timeoutMs", 86_400_000, &mut config.timeout_ms),
            ("jobs", 256, &mut config.jobs),
        ] {
            if let Some(value) = section.get(field) {
                *destination = value
                    .as_u64()
                    .filter(|value| (1..=maximum).contains(value))
                    .ok_or_else(|| anyhow::anyhow!(
                        "suprnova-lsp rustdoc.automatic.{field} must be an integer between 1 and {maximum}"
                    ))?;
            }
        }
        for field in ["toolchain", "artifactRoot"] {
            if let Some(value) = section.get(field) {
                let value = value
                    .as_str()
                    .filter(|value| !value.trim().is_empty())
                    .ok_or_else(|| {
                        anyhow::anyhow!(
                            "suprnova-lsp rustdoc.automatic.{field} must be a nonempty string"
                        )
                    })?;
                if field == "toolchain" {
                    config.toolchain = value.to_owned();
                } else {
                    config.artifact_root = Some(value.into());
                }
            }
        }
        Ok(config)
    }
}

impl Default for RustdocAutomaticConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            debounce_ms: 2000,
            toolchain: "nightly-2026-08-19".to_owned(),
            timeout_ms: 900_000,
            jobs: 2,
            artifact_root: None,
        }
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
            anyhow::anyhow!("suprnova-lsp rustdoc.inputs[{index}] must be an object")
        })?;
        let target_kind = match Self::required_string(input, index, "targetKind")? {
            "lib" => RustdocTargetKind::Lib,
            "bin" => RustdocTargetKind::Bin,
            _ => {
                anyhow::bail!("suprnova-lsp rustdoc.inputs[{index}].targetKind must be lib or bin")
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
            anyhow::anyhow!("suprnova-lsp rustdoc.inputs[{index}].{field} must be a string")
        })?;
        anyhow::ensure!(
            !value.trim().is_empty(),
            "suprnova-lsp rustdoc.inputs[{index}].{field} must not be empty",
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
