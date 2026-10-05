use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::Context as _;
use rg_lsp_proto::{AnalysisConfig, CargoMetadataTarget, RustdocTargetExport, RustdocTargetKind};
use tempfile::TempDir;
use tokio::process::Command;

use super::{GenerationChanges, command::SupervisedCommand};

pub(super) struct PreparedExports {
    pub(super) targets: Vec<RustdocTargetExport>,
    pub(super) producer_files: Vec<rg_lsp_proto::RustdocProducerFile>,
    pub(super) metadata_path: PathBuf,
    pub(super) target_cfg: String,
    pub(super) sysroot_library_root: PathBuf,
    // Paths handed to the engine belong to this immutable staging directory, not Cargo's cache.
    _staging: TempDir,
}

pub(super) struct CompilerPass<'a> {
    root: &'a Path,
    config: &'a AnalysisConfig,
    artifacts: &'a Path,
    generation: u64,
    changes: &'a mut GenerationChanges,
}

impl<'a> CompilerPass<'a> {
    pub(super) fn new(
        root: &'a Path,
        config: &'a AnalysisConfig,
        artifacts: &'a Path,
        generation: u64,
        changes: &'a mut GenerationChanges,
    ) -> Self {
        Self {
            root,
            config,
            artifacts,
            generation,
            changes,
        }
    }

    fn command(
        &self,
        program: impl AsRef<std::ffi::OsStr>,
        producer: Option<&ProducerIdentity>,
    ) -> Command {
        let mut command = Command::new(program);
        command
            .current_dir(self.root)
            .env("RUSTUP_AUTO_INSTALL", "0")
            .env("CARGO_TARGET_DIR", self.artifacts.join("cargo"));
        if let Some(producer) = producer {
            command
                .env("RUSTUP_TOOLCHAIN", &self.config.rustdoc.automatic.toolchain)
                .env("RUSTC", &producer.rustc)
                .env("RUSTDOC", &producer.rustdoc);
        }
        command
    }

    async fn run(
        &mut self,
        command: &mut Command,
        operation: &str,
        limit: usize,
    ) -> anyhow::Result<Vec<u8>> {
        SupervisedCommand::run(
            command,
            self.changes,
            self.generation,
            Duration::from_millis(self.config.rustdoc.automatic.timeout_ms),
            limit,
        )
        .await?
        .require_success(operation)
    }

    fn features(&self, command: &mut Command) {
        let config = &self.config.cargo_metadata_config;
        if config.all_features_enabled() {
            command.arg("--all-features");
        }
        if config.no_default_features_enabled() {
            command.arg("--no-default-features");
        }
        if !config.features().is_empty() {
            command.arg("--features").arg(config.features().join(","));
        }
    }

    async fn graph(
        &mut self,
        target: &str,
        producer: Option<&ProducerIdentity>,
    ) -> anyhow::Result<CargoGraph> {
        let mut command = self.command("cargo", producer);
        command
            .args([
                "metadata",
                "--locked",
                "--format-version",
                "1",
                "--filter-platform",
                target,
            ])
            .arg("--manifest-path")
            .arg(self.root.join("Cargo.toml"));
        self.features(&mut command);
        let bytes = self
            .run(
                &mut command,
                "read locked Cargo model graph",
                32 * 1024 * 1024,
            )
            .await?;
        let value: serde_json::Value =
            serde_json::from_slice(&bytes).context("decode Cargo graph identity")?;
        let mut identity = blake3::Hasher::new();
        CargoGraph::hash_value(&value, &mut identity);
        Ok(CargoGraph {
            metadata: serde_json::from_value(value).context("decode resolved Cargo model graph")?,
            identity: *identity.finalize().as_bytes(),
        })
    }

    async fn producer(&mut self) -> anyhow::Result<ProducerIdentity> {
        let mut paths = Vec::new();
        for program in ["rustc", "rustdoc", "cargo"] {
            let mut command = self.command("rustup", None);
            command.args([
                "which",
                "--toolchain",
                &self.config.rustdoc.automatic.toolchain,
                program,
            ]);
            let bytes = self.run(&mut command, &format!("find {program} producer toolchain {} (install it explicitly, then reindex)", self.config.rustdoc.automatic.toolchain), 32 * 1024).await?;
            let path = PathBuf::from(
                String::from_utf8(bytes)
                    .context("decode producer executable path")?
                    .trim(),
            );
            anyhow::ensure!(
                path.is_absolute() && path.is_file(),
                "producer toolchain {program} executable is missing"
            );
            paths.push(path);
        }
        let mut producer = ProducerIdentity {
            rustc: paths.remove(0),
            rustdoc: paths.remove(0),
            cargo: paths.remove(0),
            identity: [0; 32],
        };
        producer.identity = self.producer_identity(&producer).await?;
        Ok(producer)
    }

    async fn producer_identity(&mut self, producer: &ProducerIdentity) -> anyhow::Result<[u8; 32]> {
        let mut identity = blake3::Hasher::new();
        for (path, argument) in [
            (&producer.rustc, "-vV"),
            (&producer.rustdoc, "--version"),
            (&producer.cargo, "--version"),
        ] {
            let mut command = self.command(path, Some(producer));
            command.arg(argument);
            identity.update(
                &self
                    .run(&mut command, "verify selected compiler producer", 32 * 1024)
                    .await?,
            );
            let metadata = std::fs::metadata(path).context("read producer executable identity")?;
            identity.update(path.as_os_str().as_encoded_bytes());
            identity.update(&metadata.len().to_le_bytes());
            let modified = metadata
                .modified()?
                .duration_since(std::time::UNIX_EPOCH)
                .context("producer modification time precedes the identity epoch")?;
            identity.update(&modified.as_nanos().to_le_bytes());
        }
        Ok(*identity.finalize().as_bytes())
    }

    pub(super) async fn export(mut self) -> anyhow::Result<PreparedExports> {
        let target = match self.config.cargo_metadata_config.target() {
            CargoMetadataTarget::Triple(target) => target.clone(),
            CargoMetadataTarget::Auto => {
                let mut command = self.command("rustc", None);
                command.arg("-vV");
                let bytes = self
                    .run(&mut command, "discover analysis host target", 32 * 1024)
                    .await?;
                rg_workspace::RustcTarget::parse_host_from_verbose_output(&String::from_utf8_lossy(
                    &bytes,
                ))
                .context("compiler did not report an analysis host target")?
                .as_str()
                .to_owned()
            }
        };
        anyhow::ensure!(
            matches!(
                std::path::Path::new(&target)
                    .components()
                    .collect::<Vec<_>>()
                    .as_slice(),
                [std::path::Component::Normal(_)]
            ),
            "automatic rustdoc requires a target triple, not a target path: {target}"
        );
        let source_graph = self.graph(&target, None).await?;
        let targets = source_graph.targets()?;
        let staging = tempfile::Builder::new()
            .prefix("export-")
            .tempdir_in(self.artifacts)
            .context("claim immutable rustdoc generation artifacts")?;
        // Candidate construction receives this exact graph rather than running more unowned
        // commands in the engine. Full metadata lives only in the temporary handoff directory.
        let metadata_path = staging.path().join("metadata.json");
        serde_json::to_writer(
            std::fs::File::create(&metadata_path)?,
            &source_graph.metadata,
        )
        .context("stage captured Cargo graph")?;
        let mut command = self.command("rustc", None);
        command.args(["--print", "cfg", "--target", &target]);
        let target_cfg = String::from_utf8(
            self.run(
                &mut command,
                "capture analysis target configuration",
                32 * 1024,
            )
            .await?,
        )
        .context("decode analysis target configuration")?;
        let mut command = self.command("rustc", None);
        command.args(["--print", "sysroot"]);
        let sysroot = String::from_utf8(
            self.run(&mut command, "capture analysis sysroot", 32 * 1024)
                .await?,
        )
        .context("decode analysis sysroot")?;
        anyhow::ensure!(!sysroot.trim().is_empty(), "analysis sysroot path is empty");
        let sysroot_library_root =
            PathBuf::from(sysroot.trim()).join("lib/rustlib/src/rust/library");
        if targets.is_empty() {
            return Ok(PreparedExports {
                targets: Vec::new(),
                producer_files: Vec::new(),
                metadata_path,
                target_cfg,
                sysroot_library_root,
                _staging: staging,
            });
        }
        let producer = self.producer().await?;
        let producer_files = [&producer.rustc, &producer.rustdoc, &producer.cargo]
            .into_iter()
            .map(|path| rg_lsp_proto::RustdocProducerFile::read(path))
            .collect::<std::io::Result<Vec<_>>>()?;
        let graph = self.graph(&target, Some(&producer)).await?;
        anyhow::ensure!(
            source_graph.identity == graph.identity,
            "selected producer changed the resolved analysis graph"
        );
        let mut exports = Vec::new();
        for (index, selected) in targets.into_iter().enumerate() {
            let output = self
                .artifacts
                .join("cargo")
                .join(&target)
                .join("doc")
                .join(format!("{}.json", selected.name.replace('-', "_")));
            match std::fs::remove_file(&output) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error).context("remove previous mutable Cargo export"),
            }
            let mut command = self.command("cargo", Some(&producer));
            command
                .arg("rustdoc")
                .arg("--locked")
                .arg("--manifest-path")
                .arg(&selected.manifest)
                .arg("--package")
                .arg(&selected.package)
                .arg("--target")
                .arg(&target)
                .arg("--target-dir")
                .arg(self.artifacts.join("cargo"))
                .arg("--jobs")
                .arg(self.config.rustdoc.automatic.jobs.to_string());
            match selected.kind {
                RustdocTargetKind::Lib => {
                    command.arg("--lib");
                }
                RustdocTargetKind::Bin => {
                    command.arg("--bin").arg(&selected.name);
                }
            }
            self.features(&mut command);
            command.args([
                "--",
                "-Z",
                "unstable-options",
                "--output-format",
                "json",
                "--document-private-items",
                "--document-hidden-items",
            ]);
            self.run(
                &mut command,
                &format!(
                    "export {} {:?} from {}",
                    selected.name,
                    selected.kind,
                    selected.manifest.display()
                ),
                32 * 1024,
            )
            .await?;
            let destination = staging.path().join(format!("{index}.json"));
            std::fs::copy(&output, &destination)
                .with_context(|| format!("stage compiler export {}", output.display()))?;
            std::fs::remove_file(&output).context("release mutable Cargo export after staging")?;
            exports.push(RustdocTargetExport {
                manifest_path: selected.manifest,
                target_name: selected.name,
                target_kind: selected.kind,
                export_path: destination,
            });
        }
        anyhow::ensure!(
            self.graph(&target, Some(&producer)).await?.identity == graph.identity,
            "resolved Cargo graph changed during model export"
        );
        anyhow::ensure!(
            self.producer_identity(&producer).await? == producer.identity,
            "compiler producer changed during model export"
        );
        anyhow::ensure!(
            producer_files
                .iter()
                .all(rg_lsp_proto::RustdocProducerFile::is_current),
            "compiler executable changed during model export"
        );
        Ok(PreparedExports {
            targets: exports,
            producer_files,
            metadata_path,
            target_cfg,
            sysroot_library_root,
            _staging: staging,
        })
    }
}

struct ProducerIdentity {
    rustc: PathBuf,
    rustdoc: PathBuf,
    cargo: PathBuf,
    identity: [u8; 32],
}

struct CargoGraph {
    metadata: cargo_metadata::Metadata,
    identity: [u8; 32],
}

impl CargoGraph {
    fn hash_value(value: &serde_json::Value, identity: &mut blake3::Hasher) {
        match value {
            serde_json::Value::Object(object) => {
                identity.update(b"{");
                for (key, value) in object.iter().collect::<BTreeMap<_, _>>() {
                    identity.update(&(key.len() as u64).to_le_bytes());
                    identity.update(key.as_bytes());
                    Self::hash_value(value, identity);
                }
                identity.update(b"}");
            }
            serde_json::Value::Array(values) => {
                identity.update(b"[");
                for value in values {
                    Self::hash_value(value, identity);
                }
                identity.update(b"]");
            }
            value => {
                identity.update(value.to_string().as_bytes());
                identity.update(b";");
            }
        }
    }

    fn targets(&self) -> anyhow::Result<Vec<SelectedTarget>> {
        let resolve = self
            .metadata
            .resolve
            .as_ref()
            .context("Cargo metadata omitted the resolved model graph")?;
        let nodes = resolve
            .nodes
            .iter()
            .map(|node| (&node.id, node))
            .collect::<BTreeMap<_, _>>();
        let packages = self
            .metadata
            .packages
            .iter()
            .map(|package| (&package.id, package))
            .collect::<BTreeMap<_, _>>();
        let mut targets = Vec::new();
        for member in &self.metadata.workspace_members {
            let mut pending = vec![member];
            let mut seen = HashSet::new();
            let mut frameworks = BTreeSet::new();
            while let Some(id) = pending.pop() {
                if !seen.insert(id) {
                    continue;
                }
                let package = packages
                    .get(id)
                    .context("resolved Cargo package is absent")?;
                if package.name.as_str() == "suprnova" {
                    frameworks.insert(id);
                }
                let node = nodes.get(id).context("resolved Cargo node is absent")?;
                pending.extend(
                    node.deps
                        .iter()
                        .filter(|dependency| {
                            dependency
                                .dep_kinds
                                .iter()
                                .any(|kind| kind.kind == cargo_metadata::DependencyKind::Normal)
                        })
                        .map(|dependency| &dependency.pkg),
                );
            }
            if frameworks.is_empty() {
                continue;
            }
            anyhow::ensure!(
                frameworks.len() == 1,
                "workspace member {member} has ambiguous Suprnova package identities"
            );
            let package = packages[member];
            for target in &package.targets {
                let kind = match rg_workspace::TargetKind::from_cargo_target(target) {
                    rg_workspace::TargetKind::Lib => RustdocTargetKind::Lib,
                    rg_workspace::TargetKind::Bin => RustdocTargetKind::Bin,
                    _ => continue,
                };
                targets.push(SelectedTarget {
                    package: package.id.to_string(),
                    manifest: package.manifest_path.clone().into_std_path_buf(),
                    name: target.name.clone(),
                    kind,
                });
            }
        }
        targets.sort_by(|left, right| {
            (
                &left.package,
                &left.name,
                matches!(left.kind, RustdocTargetKind::Bin),
            )
                .cmp(&(
                    &right.package,
                    &right.name,
                    matches!(right.kind, RustdocTargetKind::Bin),
                ))
        });
        Ok(targets)
    }
}

struct SelectedTarget {
    package: String,
    manifest: PathBuf,
    name: String,
    kind: RustdocTargetKind,
}
