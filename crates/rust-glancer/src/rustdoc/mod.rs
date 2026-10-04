use std::{fs::File, io::Write as _, path::PathBuf};

use anyhow::Context as _;
use rg_rustdoc::RustdocExport;

/// Inspect an existing export without launching a compiler or starting project analysis.
pub(crate) fn inspect(path: PathBuf, item: &str) -> anyhow::Result<()> {
    let file =
        File::open(&path).with_context(|| format!("open rustdoc export {}", path.display()))?;
    let export = RustdocExport::read(file)
        .with_context(|| format!("import rustdoc export {}", path.display()))?;
    let api = export.type_api(item)?;
    let mut output = std::io::stdout().lock();
    serde_json::to_writer_pretty(&mut output, &api).context("write rustdoc API report")?;
    writeln!(output).context("finish rustdoc API report")
}
