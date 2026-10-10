//! Hover-result comparison and aggregation.

use super::metrics::{AggregateSummaryMetrics, SetComparisonMetrics};
use crate::compare_lsp::comparison::{QueryComparison, QueryComparisonResult};

#[derive(Debug)]
pub(crate) struct HoverComparison {
    suprnova_lsp_present: bool,
    rust_analyzer_present: bool,
}

impl HoverComparison {
    pub(super) fn new(suprnova_lsp_present: bool, rust_analyzer_present: bool) -> Self {
        Self {
            suprnova_lsp_present,
            rust_analyzer_present,
        }
    }

    pub(crate) fn metrics(&self) -> SetComparisonMetrics {
        Self::metrics_for_presence(self.suprnova_lsp_present, self.rust_analyzer_present)
    }

    fn metrics_for_presence(
        suprnova_lsp_present: bool,
        rust_analyzer_present: bool,
    ) -> SetComparisonMetrics {
        let suprnova_lsp_count = usize::from(suprnova_lsp_present);
        let rust_analyzer_count = usize::from(rust_analyzer_present);
        let matched_count = usize::from(suprnova_lsp_present && rust_analyzer_present);
        let missing_count = usize::from(!suprnova_lsp_present && rust_analyzer_present);
        let extra_count = usize::from(suprnova_lsp_present && !rust_analyzer_present);

        SetComparisonMetrics::new(
            suprnova_lsp_count,
            rust_analyzer_count,
            matched_count,
            missing_count,
            extra_count,
        )
    }
}

#[derive(Debug, Default)]
pub(crate) struct HoverAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched_count: usize,
    missing_count: usize,
    extra_count: usize,
}

impl HoverAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::Hover(comparison) => {
                let metrics = comparison.metrics();
                self.comparable_count += 1;
                self.suprnova_lsp_count += metrics.suprnova_lsp_count;
                self.rust_analyzer_count += metrics.rust_analyzer_count;
                self.matched_count += metrics.matched_count;
                self.missing_count += metrics.missing_count;
                self.extra_count += metrics.extra_count;
            }
            QueryComparisonResult::NonComparable(_) => self.non_comparable_count += 1,
            _ => {}
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.query_count == 0
    }

    pub(super) fn summary(&self) -> AggregateSummaryMetrics {
        AggregateSummaryMetrics {
            query_count: self.query_count,
            comparable_count: self.comparable_count,
            non_comparable_count: self.non_comparable_count,
        }
    }

    pub(crate) fn metrics(&self) -> SetComparisonMetrics {
        SetComparisonMetrics::new(
            self.suprnova_lsp_count,
            self.rust_analyzer_count,
            self.matched_count,
            self.missing_count,
            self.extra_count,
        )
    }
}
