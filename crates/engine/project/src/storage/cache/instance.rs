//! Per-process cache namespace ownership.
//!
//! Each live LSP engine claims one numbered instance directory and keeps its lock file held for
//! the whole project lifetime. That makes package artifacts private to the engine that may later
//! lazy-load them.

use std::{
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};

use anyhow::Context as _;
use rg_workspace::WorkspaceMetadata;

const CACHE_DIR_NAME: &str = "suprnova_lsp";
const CACHE_INSTANCES_DIR_NAME: &str = "instances";
const CACHE_INSTANCE_LOCK_FILE_NAME: &str = "instance.lock";
const MAX_CACHE_INSTANCE_SLOTS: u64 = 1024;

/// Owned cache namespace for one live project/LSP engine.
#[derive(Debug, Clone)]
pub(crate) struct PackageCacheInstance {
    inner: Arc<PackageCacheInstanceInner>,
}

impl PackageCacheInstance {
    /// Claim the first available cache instance under Cargo's target directory.
    pub(crate) fn for_workspace(workspace: &WorkspaceMetadata) -> anyhow::Result<Self> {
        let target_dir = std::env::var_os("CARGO_TARGET_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| workspace.cargo_target_dir().to_path_buf());

        let instances_root = Self::instances_root(workspace, target_dir);
        for slot in 1..=MAX_CACHE_INSTANCE_SLOTS {
            if let Some(inner) = Self::try_claim_slot(&instances_root, slot)? {
                #[cfg(target_os = "linux")]
                if let Err(error) =
                    Self::reserve_descriptor_capacity(&inner._lock_file, workspace.packages().len())
                {
                    tracing::warn!(
                        %error,
                        "could not prepare package artifact descriptor capacity; using ordinary lazy reads"
                    );
                }
                return Ok(Self {
                    inner: Arc::new(inner),
                });
            }
        }

        anyhow::bail!(
            "no free package cache instance slots under {}",
            instances_root.display()
        )
    }

    // Linux grows a shared descriptor table in power-of-two steps. Each expansion waits for
    // concurrent descriptor installs, so hundreds of pinned artifact readers can add several
    // pauses to the first query. Expand once while initializing the cache, before publication.
    // This duplicates only the existing lock handle and closes the duplicate immediately; no
    // artifact or decoded analysis is retained, and the process's descriptor limit stays intact.
    #[cfg(target_os = "linux")]
    fn reserve_descriptor_capacity(file: &fs::File, package_count: usize) -> std::io::Result<()> {
        use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};

        let minimum = i32::try_from(package_count)
            .ok()
            .and_then(|count| file.as_raw_fd().checked_add(count))
            .ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "package count exceeds the operating system descriptor range",
                )
            })?;
        // SAFETY: file owns a live descriptor. F_DUPFD_CLOEXEC creates a separate owned handle
        // at or above minimum and preserves the original handle and its lock.
        let descriptor = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_DUPFD_CLOEXEC, minimum) };
        if descriptor < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: successful fcntl returned a fresh descriptor owned by this call.
        drop(unsafe { OwnedFd::from_raw_fd(descriptor) });
        tracing::debug!(descriptor, "prepared package artifact descriptor capacity");
        Ok(())
    }

    /// Return this engine's private cache root.
    pub(crate) fn root(&self) -> &Path {
        &self.inner.root
    }

    /// Return the selected slot number for direct ownership tests.
    #[cfg(test)]
    pub(crate) fn slot_for_tests(&self) -> u64 {
        self.inner.slot
    }

    fn try_claim_slot(
        instances_root: &Path,
        slot: u64,
    ) -> anyhow::Result<Option<PackageCacheInstanceInner>> {
        let root = instances_root.join(slot.to_string());
        fs::create_dir_all(&root).with_context(|| {
            format!(
                "while attempting to create package cache instance {}",
                root.display(),
            )
        })?;

        let lock_path = root.join(CACHE_INSTANCE_LOCK_FILE_NAME);
        let lock_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&lock_path)
            .with_context(|| {
                format!(
                    "while attempting to open package cache instance lock {}",
                    lock_path.display(),
                )
            })?;

        match lock_file.try_lock() {
            Ok(()) => Ok(Some(PackageCacheInstanceInner {
                root,
                #[cfg(test)]
                slot,
                _lock_file: lock_file,
            })),
            Err(fs::TryLockError::WouldBlock) => Ok(None),
            Err(fs::TryLockError::Error(error)) => Err(error).with_context(|| {
                format!(
                    "while attempting to lock package cache instance {}",
                    lock_path.display(),
                )
            }),
        }
    }

    fn instances_root(workspace: &WorkspaceMetadata, target_dir: impl Into<PathBuf>) -> PathBuf {
        let workspace_name = workspace
            .workspace_root()
            .file_name()
            .unwrap_or_else(|| OsStr::new("workspace"));

        target_dir
            .into()
            .join(CACHE_DIR_NAME)
            .join(workspace_name)
            .join(CACHE_INSTANCES_DIR_NAME)
    }
}

/// Shared inner state keeps the OS lock alive across cloned cache state handles.
#[derive(Debug)]
struct PackageCacheInstanceInner {
    root: PathBuf,
    #[cfg(test)]
    slot: u64,
    _lock_file: fs::File,
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use std::{fs, os::fd::AsRawFd};

    use super::PackageCacheInstance;

    #[test]
    fn descriptor_reservation_expands_capacity_without_retaining_handles() {
        let file = fs::File::open("/dev/null").expect("null device should open");
        let descriptors_before = fs::read_dir("/proc/self/fd")
            .expect("process descriptors should be readable")
            .count();
        let limit_before = descriptor_limit();
        let minimum = 256;
        PackageCacheInstance::reserve_descriptor_capacity(&file, minimum)
            .expect("descriptor table should reserve one reader set");
        let status =
            fs::read_to_string("/proc/self/status").expect("process status should be readable");
        let capacity = status
            .lines()
            .find_map(|line| {
                line.strip_prefix("FDSize:").map(|value| {
                    value
                        .trim()
                        .parse::<usize>()
                        .expect("descriptor capacity should be numeric")
                })
            })
            .expect("process status should report descriptor capacity");
        assert!(capacity > minimum + file.as_raw_fd() as usize);
        assert_eq!(
            fs::read_dir("/proc/self/fd").unwrap().count(),
            descriptors_before
        );
        assert_eq!(descriptor_limit(), limit_before);
        file.metadata().expect("original handle should remain open");
    }

    #[test]
    fn oversized_descriptor_reservation_preserves_the_original_handle() {
        let file = fs::File::open("/dev/null").expect("null device should open");
        let error = PackageCacheInstance::reserve_descriptor_capacity(&file, usize::MAX)
            .expect_err("unrepresentable package count must be rejected");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        file.metadata()
            .expect("failed reservation must preserve the original handle");
    }

    #[test]
    fn descriptor_reservation_reports_the_os_limit_without_leaking_handles() {
        let file = fs::File::open("/dev/null").expect("null device should open");
        let before = fs::read_dir("/proc/self/fd").unwrap().count();
        let limit_before = descriptor_limit();
        let count = usize::try_from(limit_before.0)
            .expect("descriptor limit should fit the host's address range");
        let error = PackageCacheInstance::reserve_descriptor_capacity(&file, count)
            .expect_err("reservation at the process limit must fail");
        assert!(matches!(
            error.raw_os_error(),
            Some(libc::EINVAL | libc::EMFILE)
        ));
        assert_eq!(fs::read_dir("/proc/self/fd").unwrap().count(), before);
        assert_eq!(descriptor_limit(), limit_before);
        file.metadata()
            .expect("failed reservation must preserve the original handle");
    }

    fn descriptor_limit() -> (libc::rlim_t, libc::rlim_t) {
        let mut limit = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        // SAFETY: limit is writable storage of the type getrlimit expects.
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) },
            0
        );
        (limit.rlim_cur, limit.rlim_max)
    }
}
