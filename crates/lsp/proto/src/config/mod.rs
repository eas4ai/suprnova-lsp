mod analysis;
mod cache;
mod cargo;
mod cfg;
mod diagnostics;
mod indexing;
mod sysroot;

use gen_lsp_types::LspAny;
use serde::{Deserialize, Serialize};

pub use self::{
    analysis::AnalysisConfig,
    cache::PackageResidencyPolicy,
    cargo::{CargoMetadataConfig, CargoMetadataTarget},
    cfg::AnalysisCfgConfig,
    diagnostics::DiagnosticsConfig,
    indexing::{IndexingPerformancePreference, PackageBatchSize},
    sysroot::SysrootDiscovery,
};

/// Configuration needed to start one analysis engine.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct EngineConfig {
    pub analysis: AnalysisConfig,
    pub diagnostics: DiagnosticsConfig,
}

impl EngineConfig {
    pub fn from_initialization_options(options: Option<&LspAny>) -> anyhow::Result<Self> {
        Ok(Self {
            analysis: AnalysisConfig::from_initialization_options(options)?,
            diagnostics: DiagnosticsConfig::from_initialization_options(options)?,
        })
    }
}

fn section<'a>(
    options: Option<&'a LspAny>,
    key: &'static str,
) -> Option<&'a gen_lsp_types::LspObject> {
    options
        .and_then(LspAny::as_object)
        .and_then(|options| options.get(key))
        .and_then(LspAny::as_object)
}

#[cfg(test)]
mod tests;
