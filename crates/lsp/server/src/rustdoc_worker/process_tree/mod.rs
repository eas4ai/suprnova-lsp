//! Ownership and draining of one compiler process tree.

#[cfg(unix)]
mod unix;

#[cfg(unix)]
pub(super) use unix::OwnedProcessTree;

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub(super) use windows::OwnedProcessTree;
