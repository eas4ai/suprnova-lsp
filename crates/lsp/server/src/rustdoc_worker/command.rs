use std::{io, process::{ExitStatus, Stdio}, time::Duration};
use anyhow::Context as _;
use tokio::{io::{AsyncRead, AsyncReadExt}, process::Command, sync::watch};
use super::process_tree::OwnedProcessTree;

pub(super) struct CommandOutput {
    pub(super) stdout: Vec<u8>,
    pub(super) stderr: String,
    pub(super) status: ExitStatus,
    pub(super) stdout_truncated: bool,
}

impl CommandOutput {
    pub(super) fn require_success(self, operation: &str) -> anyhow::Result<Vec<u8>> {
        anyhow::ensure!(self.status.success(), "{operation} exited with {}: {}", self.status, self.stderr);
        anyhow::ensure!(!self.stdout_truncated, "{operation} exceeded its output limit");
        Ok(self.stdout)
    }
}

pub(super) struct SupervisedCommand;

impl SupervisedCommand {
    pub(super) async fn run(
        command: &mut Command,
        changes: &mut watch::Receiver<u64>,
        generation: u64,
        timeout: Duration,
        stdout_limit: usize,
    ) -> anyhow::Result<CommandOutput> {
        anyhow::ensure!(*changes.borrow() == generation, "superseded compiler preparation");
        command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let (tree, mut child) = OwnedProcessTree::spawn(command)?;
        let stdout = child.stdout.take().context("capture compiler stdout")?;
        let stderr = child.stderr.take().context("capture compiler stderr")?;
        let mut stdout_task = tokio::spawn(Self::capture(stdout, stdout_limit));
        let mut stderr_task = tokio::spawn(Self::capture(stderr, 32 * 1024));
        let result = tokio::select! {
            status = child.wait() => status.context("wait for compiler command"),
            _ = changes.wait_for(|current| *current != generation) => Err(anyhow::anyhow!("superseded compiler preparation")),
            _ = tokio::time::sleep(timeout) => Err(anyhow::anyhow!("compiler command timed out after {} ms", timeout.as_millis())),
        };
        // Cleanup completes before the caller can release the shared compiler permit.
        let cleanup = tree.drain(&mut child).await;
        let captures = tokio::time::timeout(Duration::from_secs(5), async {
            let stdout = (&mut stdout_task).await.context("join compiler stdout capture")?
                .context("read compiler stdout")?;
            let stderr = (&mut stderr_task).await.context("join compiler stderr capture")?
                .context("read compiler stderr")?;
            anyhow::Ok((stdout, stderr))
        }).await;
        stdout_task.abort();
        stderr_task.abort();
        let (stdout, stderr) = captures.context("timeout draining compiler output")??;
        cleanup?;
        Ok(CommandOutput {
            stdout: stdout.0,
            stdout_truncated: stdout.1,
            stderr: String::from_utf8_lossy(&stderr.0).into_owned(),
            status: result?,
        })
    }

    async fn capture(mut stream: impl AsyncRead + Unpin, limit: usize) -> io::Result<(Vec<u8>, bool)> {
        let mut bytes = Vec::new();
        let mut truncated = false;
        let mut buffer = [0u8; 8192];
        loop {
            let count = stream.read(&mut buffer).await?;
            if count == 0 { return Ok((bytes, truncated)); }
            let retained = count.min(limit.saturating_sub(bytes.len()));
            bytes.extend_from_slice(&buffer[..retained]);
            truncated |= retained < count;
        }
    }
}
