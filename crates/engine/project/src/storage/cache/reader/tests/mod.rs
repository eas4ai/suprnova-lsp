use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

use rg_ir_model::PackageSlot;
use tracing::{Event, Metadata, Subscriber, field::Visit, span};

use super::{PackageArtifactReaderInner, PackageCacheSectionRange};
use crate::{PackageResidencyPolicy, testonly::ProjectSourceFixture};

struct Fields(BTreeMap<String, String>);

impl Visit for Fields {
    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        self.0.insert(field.name().to_owned(), format!("{value:?}"));
    }
}

struct ReaderTrace {
    reader: Arc<PackageArtifactReaderInner>,
    events: Arc<Mutex<Vec<BTreeMap<String, String>>>>,
}

impl Subscriber for ReaderTrace {
    fn enabled(&self, _metadata: &Metadata<'_>) -> bool {
        true
    }

    fn new_span(&self, _span: &span::Attributes<'_>) -> span::Id {
        span::Id::from_u64(1)
    }

    fn record(&self, _span: &span::Id, _values: &span::Record<'_>) {}
    fn record_follows_from(&self, _span: &span::Id, _follows: &span::Id) {}
    fn enter(&self, _span: &span::Id) {}
    fn exit(&self, _span: &span::Id) {}

    fn event(&self, event: &Event<'_>) {
        let mut fields = Fields(BTreeMap::new());
        event.record(&mut fields);
        if fields.0.contains_key("lock_acquire_us") {
            // A subscriber may do synchronous work. Never call it with either reader lock held.
            assert!(self.reader.file.try_lock().is_ok());
            assert!(self.reader.names.try_lock().is_ok());
            self.events.lock().unwrap().push(fields.0);
        }
    }
}

#[test]
fn reader_lock_traces_preserve_results_and_run_after_unlock() {
    let fixture = ProjectSourceFixture::build(
        r#"
//- /Cargo.toml
[package]
name = "app"
version = "0.1.0"
edition = "2024"

//- /src/lib.rs
pub struct App;
"#,
    );
    let project =
        fixture.build_project_with_package_residency_policy(PackageResidencyPolicy::AllOffloadable);
    let header = project
        .state
        .cache_plan
        .artifact_header(PackageSlot(0), &project.state.package_source_fingerprints)
        .unwrap();
    let reader = project
        .state
        .cache_store
        .open_artifact(&header)
        .unwrap()
        .unwrap();
    let events = Arc::new(Mutex::new(Vec::new()));
    let subscriber = ReaderTrace {
        reader: Arc::clone(&reader.inner),
        events: Arc::clone(&events),
    };
    tracing::subscriber::with_default(subscriber, || {
        let bytes = reader
            .read_section("test.read", reader.inner.layout.probe)
            .unwrap();
        assert_eq!(bytes.len() as u64, reader.inner.layout.probe.len);
        let past_end = PackageCacheSectionRange {
            offset: std::fs::metadata(&reader.inner.path).unwrap().len(),
            len: 1,
        };
        assert!(reader.read_section("test.eof", past_end).is_err());
        let config = wincode::config::Configuration::default();
        let encoded_name = wincode::config::serialize(&rg_text::Name::new("User"), config).unwrap();
        let first = reader
            .decode_with_names("test.decode", || {
                Ok(wincode::config::deserialize_exact::<rg_text::Name, _>(
                    &encoded_name,
                    config,
                )?)
            })
            .unwrap();
        assert_eq!(first.as_str(), "User");
        let error = reader.decode_with_names::<()>("test.error", || {
            let decoded =
                wincode::config::deserialize_exact::<rg_text::Name, _>(&encoded_name, config)?;
            assert_eq!(first.as_str().as_ptr(), decoded.as_str().as_ptr());
            anyhow::bail!("expected decode failure")
        });
        assert_eq!(error.unwrap_err().to_string(), "expected decode failure");
        let after_error = reader
            .decode_with_names("test.after_error", || {
                Ok(wincode::config::deserialize_exact::<rg_text::Name, _>(
                    &encoded_name,
                    config,
                )?)
            })
            .unwrap();
        assert_eq!(first.as_str().as_ptr(), after_error.as_str().as_ptr());
    });

    tracing::subscriber::with_default(tracing::subscriber::NoSubscriber::default(), || {
        assert!(
            reader
                .read_section("test.disabled", reader.inner.layout.probe)
                .is_ok()
        );
    });

    let events = events.lock().unwrap();
    assert_eq!(
        events.len(),
        5,
        "every success and error must retain its trace"
    );
    for (event, succeeded) in events.iter().zip([true, false, true, false, true]) {
        assert_eq!(event["succeeded"], succeeded.to_string());
        for field in ["lock_acquire_us", "lock_held_us"] {
            assert!(
                event[field]
                    .strip_prefix("Some(")
                    .and_then(|value| value.strip_suffix(')'))
                    .unwrap()
                    .parse::<u128>()
                    .is_ok()
            );
        }
        #[cfg(target_os = "linux")]
        for field in ["lock_acquire_cpu_us", "lock_held_cpu_us"] {
            assert_ne!(event[field], "None", "Linux worker CPU must be measured");
        }
    }
}
