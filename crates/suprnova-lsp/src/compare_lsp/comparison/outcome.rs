//! Shared comparison outcome helpers.

use super::metrics::NonComparableMetrics;
use crate::compare_lsp::normalization::NormalizedOutcome;

#[derive(Debug)]
pub(crate) struct NonComparableComparison {
    suprnova_lsp: OutcomeStatus,
    rust_analyzer: OutcomeStatus,
    suprnova_lsp_detail: Option<String>,
    rust_analyzer_detail: Option<String>,
}

impl NonComparableComparison {
    pub(super) fn new(suprnova_lsp: &NormalizedOutcome, rust_analyzer: &NormalizedOutcome) -> Self {
        Self {
            suprnova_lsp: OutcomeStatus::from_outcome(suprnova_lsp),
            rust_analyzer: OutcomeStatus::from_outcome(rust_analyzer),
            suprnova_lsp_detail: Self::outcome_detail(suprnova_lsp),
            rust_analyzer_detail: Self::outcome_detail(rust_analyzer),
        }
    }

    pub(crate) fn metrics(&self) -> NonComparableMetrics {
        NonComparableMetrics {
            suprnova_lsp_status: self.suprnova_lsp,
            rust_analyzer_status: self.rust_analyzer,
            suprnova_lsp_detail: self.suprnova_lsp_detail.clone(),
            rust_analyzer_detail: self.rust_analyzer_detail.clone(),
        }
    }

    fn outcome_detail(outcome: &NormalizedOutcome) -> Option<String> {
        match outcome {
            NormalizedOutcome::MalformedSuccess { message } => Some(message.clone()),
            NormalizedOutcome::Error { code, message } => Some(format!("{code}: {message}")),
            NormalizedOutcome::TransportFailure { message } => Some(message.clone()),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OutcomeStatus {
    Locations,
    PrepareRenames,
    RenameEdits,
    Ranges,
    Symbols,
    InlayHints,
    HoverPresent,
    HoverAbsent,
    MalformedSuccess,
    Error,
    Timeout,
    TransportFailure,
}

impl OutcomeStatus {
    fn from_outcome(outcome: &NormalizedOutcome) -> Self {
        match outcome {
            NormalizedOutcome::Locations(_) => Self::Locations,
            NormalizedOutcome::PrepareRenames(_) => Self::PrepareRenames,
            NormalizedOutcome::RenameEdits(_) => Self::RenameEdits,
            NormalizedOutcome::Ranges(_) => Self::Ranges,
            NormalizedOutcome::Symbols(_) => Self::Symbols,
            NormalizedOutcome::InlayHints(_) => Self::InlayHints,
            NormalizedOutcome::Hover { present: true } => Self::HoverPresent,
            NormalizedOutcome::Hover { present: false } => Self::HoverAbsent,
            NormalizedOutcome::MalformedSuccess { .. } => Self::MalformedSuccess,
            NormalizedOutcome::Error { .. } => Self::Error,
            NormalizedOutcome::Timeout => Self::Timeout,
            NormalizedOutcome::TransportFailure { .. } => Self::TransportFailure,
        }
    }

    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Locations => "locations",
            Self::PrepareRenames => "prepare_renames",
            Self::RenameEdits => "rename_edits",
            Self::Ranges => "ranges",
            Self::Symbols => "symbols",
            Self::InlayHints => "inlay_hints",
            Self::HoverPresent => "hover_present",
            Self::HoverAbsent => "hover_absent",
            Self::MalformedSuccess => "malformed",
            Self::Error => "error",
            Self::Timeout => "timeout",
            Self::TransportFailure => "transport_failure",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct Ratio {
    numerator: usize,
    denominator: usize,
}

impl Ratio {
    pub(super) fn new(numerator: usize, denominator: usize) -> Option<Self> {
        (denominator > 0).then_some(Self {
            numerator,
            denominator,
        })
    }

    pub(super) fn percent(self) -> f64 {
        (self.numerator as f64 / self.denominator as f64) * 100.0
    }
}
