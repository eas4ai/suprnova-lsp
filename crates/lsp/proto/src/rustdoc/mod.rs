//! Immutable export handoff from compiler preparation to ordered engine publication.

use std::path::PathBuf;
use serde::{Deserialize, Serialize};
use crate::RustdocTargetKind;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustdocTargetExport {
    pub manifest_path: PathBuf,
    pub target_name: String,
    pub target_kind: RustdocTargetKind,
    pub export_path: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RustdocGenerationInput {
    pub generation: u64,
    pub workspace_root: PathBuf,
    pub saved_inputs: [u8; 32],
    pub artifact_directory: PathBuf,
    pub exports: Vec<RustdocTargetExport>,
}
