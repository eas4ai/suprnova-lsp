//! Immutable export handoff from compiler preparation to ordered engine publication.

use std::{
    io,
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};

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
    pub artifact_directories: Vec<PathBuf>,
    pub metadata_path: PathBuf,
    pub target_cfg: String,
    pub sysroot_library_root: PathBuf,
    pub exports: Vec<RustdocTargetExport>,
    pub producer_files: Vec<RustdocProducerFile>,
}

/// The selected executable must still identify the producer that built this candidate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RustdocProducerFile {
    path: PathBuf,
    resolved_path: PathBuf,
    length: u64,
    modified_ns: u128,
}

impl RustdocProducerFile {
    pub fn read(path: &Path) -> io::Result<Self> {
        let metadata = std::fs::metadata(path)?;
        Ok(Self {
            path: path.to_path_buf(),
            resolved_path: std::fs::canonicalize(path)?,
            length: metadata.len(),
            modified_ns: metadata
                .modified()?
                .duration_since(UNIX_EPOCH)
                .map_err(io::Error::other)?
                .as_nanos(),
        })
    }

    pub fn is_current(&self) -> bool {
        Self::read(&self.path).is_ok_and(|current| current == *self)
    }
}
