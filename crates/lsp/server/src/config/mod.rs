mod cargo_overrides;

#[cfg(test)]
mod tests;

use anyhow::Context as _;
use rg_lsp_proto::EngineConfig;
use rg_std::NormalizedPathBuf;
use tower_lsp_server::gen_lsp_types::LspAny;

use self::cargo_overrides::CargoConfigOverrides;

/// Server-local configuration for resolving per-engine settings.
///
/// The engine protocol deliberately receives a concrete `EngineConfig` per Cargo workspace. The
/// server owns path routing, so it also owns path-specific override selection before an engine is
/// spawned.
#[derive(Debug, Clone)]
pub(crate) struct ServerConfig {
    engine_config: EngineConfig,
    cargo_overrides: CargoConfigOverrides,
}

impl ServerConfig {
    pub(crate) fn from_initialization_options(
        options: Option<&LspAny>,
        workspace_folders: &[NormalizedPathBuf],
    ) -> anyhow::Result<Self> {
        let mut engine_config = EngineConfig::from_initialization_options(options)?;
        // Resolve once at the editor boundary. A relative root needs one unambiguous editor
        // folder; manifest and export paths then belong to that selected Cargo workspace.
        for (index, input) in engine_config.analysis.rustdoc.inputs.iter_mut().enumerate() {
            let root = if input.workspace_root.is_absolute() {
                NormalizedPathBuf::from_absolute(&input.workspace_root)
            } else {
                let [folder] = workspace_folders else {
                    anyhow::bail!(
                        "suprnova-lsp rustdoc.inputs[{index}].workspaceRoot is relative and requires exactly one editor workspace folder",
                    );
                };
                NormalizedPathBuf::resolve_from(folder, &input.workspace_root)
            }
            .with_context(|| format!("resolve rustdoc.inputs[{index}].workspaceRoot"))?;
            input.workspace_root = root.to_path_buf();
            input.manifest_path = NormalizedPathBuf::resolve_from(&root, &input.manifest_path)
                .with_context(|| format!("resolve rustdoc.inputs[{index}].manifestPath"))?
                .into_path_buf();
            input.export_path = NormalizedPathBuf::resolve_from(&root, &input.export_path)
                .with_context(|| format!("resolve rustdoc.inputs[{index}].exportPath"))?
                .into_path_buf();
        }
        Ok(Self {
            engine_config,
            cargo_overrides: CargoConfigOverrides::from_initialization_options(
                options,
                workspace_folders,
            )?,
        })
    }

    #[cfg(test)]
    pub(crate) fn from_engine_config(engine_config: EngineConfig) -> Self {
        Self {
            engine_config,
            cargo_overrides: CargoConfigOverrides::default(),
        }
    }

    pub(crate) fn engine_config_for_root(&self, root: &NormalizedPathBuf) -> EngineConfig {
        let mut config = self.engine_config.clone();
        config
            .analysis
            .rustdoc
            .inputs
            .retain(|input| input.workspace_root.as_path() == root.as_path());
        if let Some(cargo_override) = self.cargo_overrides.override_for_root(root) {
            config.analysis.cargo_metadata_config =
                cargo_override.apply_to(config.analysis.cargo_metadata_config);
        }
        config
    }
}
