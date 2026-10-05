//! Ownership and draining of one compiler process tree.

#[cfg(unix)]
mod unix;

#[cfg(unix)]
pub(super) use self::unix::OwnedProcessTree;

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub(super) use self::windows::OwnedProcessTree;
