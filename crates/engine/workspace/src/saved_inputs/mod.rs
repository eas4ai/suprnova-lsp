//! Content identities for the saved inputs observed by automatic compiler preparation.

use std::{
    collections::BTreeMap,
    ffi::OsStr,
    io,
    path::{Component, Path},
};

#[cfg(test)]
mod tests;

/// A compact identity; source bytes are streamed and never retained in the worker's idle state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SavedWorkspaceInputs {
    digest: [u8; 32],
}

impl SavedWorkspaceInputs {
    pub fn read(root: &Path, artifacts: &[std::path::PathBuf]) -> io::Result<Self> {
        let mut files = BTreeMap::new();
        let filter_root = root.to_path_buf();
        let filter_artifacts = artifacts.to_vec();
        let mut walker = ignore::WalkBuilder::new(root);
        walker
            .hidden(false)
            .parents(false)
            .git_ignore(false)
            .git_global(false)
            .git_exclude(false)
            .filter_entry(move |entry| {
                !Self::is_ignored(&filter_root, entry.path())
                    && !filter_artifacts
                        .iter()
                        .any(|path| entry.path().starts_with(path))
            });
        for entry in walker.build() {
            let entry = entry.map_err(io::Error::other)?;
            let path = entry.path();
            if Self::is_input(root, path) && path.is_file() {
                let mut content = blake3::Hasher::new();
                content.update_reader(std::fs::File::open(path)?)?;
                files.insert(path.to_path_buf(), *content.finalize().as_bytes());
            }
        }
        let mut digest = blake3::Hasher::new();
        for (path, content) in files {
            let path = path
                .strip_prefix(root)
                .expect("walked path belongs to root");
            let spelling = path.as_os_str().as_encoded_bytes();
            digest.update(&(spelling.len() as u64).to_le_bytes());
            digest.update(spelling);
            digest.update(&content);
        }
        Ok(Self {
            digest: *digest.finalize().as_bytes(),
        })
    }

    pub fn digest(self) -> [u8; 32] {
        self.digest
    }

    pub fn is_input(root: &Path, path: &Path) -> bool {
        if Self::is_ignored(root, path) {
            return false;
        }
        let file_name = path.file_name().and_then(OsStr::to_str);
        path.extension().and_then(OsStr::to_str) == Some("rs")
            || matches!(
                file_name,
                Some("Cargo.toml" | "Cargo.lock" | "rust-toolchain" | "rust-toolchain.toml")
            )
            || (matches!(file_name, Some("config" | "config.toml"))
                && path.parent().and_then(Path::file_name) == Some(OsStr::new(".cargo")))
    }

    pub fn is_ignored(root: &Path, path: &Path) -> bool {
        let Ok(relative) = path.strip_prefix(root) else {
            return true;
        };
        relative.components().any(|component| {
            matches!(component, Component::Normal(name)
                if matches!(name.to_str(), Some(".git" | "target" | "node_modules" | ".direnv")))
        })
    }
}
