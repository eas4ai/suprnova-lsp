use std::path::PathBuf;

use rg_std::{MemorySize, Shrink};
use wincode::{SchemaRead, SchemaWrite};

/// Normalized target metadata with one target kind per target.
#[derive(Debug, Clone, PartialEq, Eq, MemorySize)]
pub struct CargoTarget {
    pub name: String,
    pub kind: TargetKind,
    pub src_path: PathBuf,
}

/// Analysis-relevant target kinds.
///
/// Analysis recognizes a small set of target kinds directly. Unknown or less common kinds are kept
/// as stable display strings instead of becoming special model variants.
#[derive(
    Debug,
    Clone,
    PartialEq,
    Eq,
    Hash,
    derive_more::Display,
    SchemaRead,
    SchemaWrite,
    MemorySize,
    Shrink,
)]
pub enum TargetKind {
    #[display("lib")]
    Lib,
    #[display("proc-macro")]
    ProcMacro,
    #[display("bin")]
    Bin,
    #[display("example")]
    Example,
    #[display("test")]
    Test,
    #[display("bench")]
    Bench,
    #[display("custom-build")]
    CustomBuild,
    #[display("{_0}")]
    Other(String),
}

impl TargetKind {
    pub fn from_cargo_target(target: &cargo_metadata::Target) -> Self {
        if target.is_kind(cargo_metadata::TargetKind::ProcMacro) {
            TargetKind::ProcMacro
        // Cargo reports crate types such as ["cdylib", "rlib"] for one library target.
        // They share one source root and must keep one library identity in analysis.
        } else if target.kind.iter().any(|kind| {
            matches!(
                kind,
                cargo_metadata::TargetKind::Lib
                    | cargo_metadata::TargetKind::RLib
                    | cargo_metadata::TargetKind::DyLib
                    | cargo_metadata::TargetKind::CDyLib
                    | cargo_metadata::TargetKind::StaticLib
            )
        }) {
            TargetKind::Lib
        } else if target.is_kind(cargo_metadata::TargetKind::Bin) {
            TargetKind::Bin
        } else if target.is_kind(cargo_metadata::TargetKind::Example) {
            TargetKind::Example
        } else if target.is_kind(cargo_metadata::TargetKind::Test) {
            TargetKind::Test
        } else if target.is_kind(cargo_metadata::TargetKind::Bench) {
            TargetKind::Bench
        } else if target.is_kind(cargo_metadata::TargetKind::CustomBuild) {
            TargetKind::CustomBuild
        } else {
            let fallback = target
                .kind
                .first()
                .map(|kind| kind.to_string())
                .unwrap_or_else(|| "unknown".to_string());
            TargetKind::Other(fallback)
        }
    }

    /// Returns whether this target belongs to ordinary eager semantic analysis.
    ///
    /// Libraries, proc macros, and binaries are the targets users most often edit directly.
    /// Tests, benches, examples, build scripts, and unknown Cargo target kinds are secondary
    /// targets that can be materialized when a query actually needs them.
    pub fn is_primary_analysis_target(&self) -> bool {
        matches!(self, Self::Lib | Self::ProcMacro | Self::Bin)
    }

    pub fn is_lib(&self) -> bool {
        matches!(self, Self::Lib | Self::ProcMacro)
    }

    pub fn is_custom_build(&self) -> bool {
        matches!(self, Self::CustomBuild)
    }

    pub fn is_proc_macro(&self) -> bool {
        matches!(self, Self::ProcMacro)
    }

    /// Cargo enables `cfg(test)` for test-like targets without reporting it in rustc cfg output.
    pub fn enables_test_cfg(&self) -> bool {
        matches!(self, Self::Test | Self::Bench)
    }

    // Used for predictable ordering, e.g.
    // in test snapshots.
    pub fn sort_order(&self) -> u8 {
        match self {
            Self::Lib => 0,
            Self::ProcMacro => 1,
            Self::Bin => 2,
            Self::Example => 3,
            Self::Test => 4,
            Self::Bench => 5,
            Self::CustomBuild => 6,
            Self::Other(_) => 7,
        }
    }
}
