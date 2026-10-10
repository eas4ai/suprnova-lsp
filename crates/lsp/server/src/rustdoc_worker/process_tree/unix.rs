use std::{io, time::Duration};

use anyhow::Context as _;
use tokio::process::{Child, Command};

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;

/// A dedicated process group. Reaping is restricted to this group, never unrelated engine children.
#[derive(Debug)]
pub(crate) struct OwnedProcessTree {
    group: libc::pid_t,
    drained: bool,
}

impl OwnedProcessTree {
    pub(crate) fn spawn(command: &mut Command) -> anyhow::Result<(Self, Child)> {
        #[cfg(target_os = "linux")]
        {
            // Cargo's children can outlive Cargo. Adopting them lets this owner reap its group
            // after cancellation instead of leaving zombies for an unrelated host init process.
            // SAFETY: prctl receives only a supported integer option and scalar arguments.
            let result = unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) };
            anyhow::ensure!(
                result == 0,
                "enable compiler child reaping: {}",
                io::Error::last_os_error()
            );
        }
        command.process_group(0).kill_on_drop(true);
        let child = command
            .spawn()
            .context("spawn supervised compiler command")?;
        let group = child
            .id()
            .context("compiler command has no process identity")?
            .try_into()
            .context("compiler process identity exceeds the platform range")?;
        Ok((
            Self {
                group,
                drained: false,
            },
            child,
        ))
    }

    fn signal(&self, signal: libc::c_int) -> io::Result<()> {
        // SAFETY: the negative PID addresses only the process group created by this owner.
        if unsafe { libc::kill(-self.group, signal) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            Ok(())
        } else {
            Err(error)
        }
    }

    pub(crate) async fn drain(&mut self, child: &mut Child) -> anyhow::Result<()> {
        let termination = self.signal(libc::SIGTERM);
        // On macOS a group containing only the unreaped leader can report EPERM. Reap the
        // direct child during the grace period so the next group signal sees its actual members.
        let grace_deadline = tokio::time::Instant::now() + Duration::from_millis(100);
        let reaped = match tokio::time::timeout_at(grace_deadline, child.wait()).await {
            Ok(status) => {
                status.context("reap compiler command during termination grace")?;
                true
            }
            Err(_) => false,
        };
        if let Err(error) = termination {
            if error.raw_os_error() != Some(libc::EPERM) || !reaped {
                return Err(error).context("terminate compiler process group");
            }
            // Retry only after proving the leader exited. A permission error from surviving
            // descendants must still fail cleanup rather than being treated as an empty group.
            self.signal(libc::SIGTERM)
                .context("terminate compiler process group after reaping command")?;
        }
        tokio::time::sleep_until(grace_deadline).await;
        // The leader can exit while descendants remain, so always signal the entire group.
        self.signal(libc::SIGKILL)
            .context("kill remaining compiler descendants")?;
        if !reaped {
            tokio::time::timeout(Duration::from_secs(5), child.wait())
                .await
                .context("timeout reaping compiler command")?
                .context("reap compiler command")?;
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        loop {
            #[cfg(target_os = "linux")]
            loop {
                // SAFETY: this wait only collects adopted descendants in the owned group and
                // never races Tokio's direct-child wait, which has already completed above.
                let result =
                    unsafe { libc::waitpid(-self.group, std::ptr::null_mut(), libc::WNOHANG) };
                if result > 0 {
                    continue;
                }
                if result < 0 {
                    let error = io::Error::last_os_error();
                    if error.raw_os_error() == Some(libc::EINTR) {
                        continue;
                    }
                    anyhow::ensure!(
                        error.raw_os_error() == Some(libc::ECHILD),
                        "reap compiler descendants: {error}"
                    );
                }
                break;
            }
            // SAFETY: signal zero observes existence without changing the owned group.
            if unsafe { libc::kill(-self.group, 0) } != 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::ESRCH) {
                    self.drained = true;
                    return Ok(());
                }
                if error.raw_os_error() != Some(libc::EPERM)
                    || tokio::time::Instant::now() >= deadline
                {
                    return Err(error).context("observe compiler process cleanup");
                }
                // An orphaned descendant may be a zombie until the host reaps it. EPERM is
                // never proof of cleanup: keep observing until ESRCH or the bounded deadline.
            }
            anyhow::ensure!(
                tokio::time::Instant::now() < deadline,
                "compiler descendants did not drain before cleanup deadline"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }
}

impl Drop for OwnedProcessTree {
    fn drop(&mut self) {
        if self.drained {
            return;
        }
        // Normal completion drains first. A dropped preparation future must still terminate
        // its whole tree; Tokio's child owner remains responsible for its direct child.
        if let Err(error) = self.signal(libc::SIGKILL) {
            tracing::error!(%error, group = self.group, "failed to terminate owned compiler group");
        }
    }
}
