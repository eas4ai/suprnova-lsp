//! Ownership and draining of one compiler process tree.

#[cfg(unix)]
mod unix;

#[cfg(unix)]
pub(super) use unix::OwnedProcessTree;

#[cfg(not(unix))]
compile_error!("automatic compiler process supervision needs a platform implementation");
