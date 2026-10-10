//! Location-result comparison and aggregation.

use std::collections::BTreeSet;

use super::metrics::{
    AggregateSummaryMetrics, MappedSetAggregateMetrics, MappedSetComparisonMetrics,
    SetComparisonMetrics,
};
use crate::compare_lsp::{
    comparison::{QueryComparison, QueryComparisonResult},
    normalization::{NormalizedLocation, NormalizedLocationSet},
};

#[derive(Debug)]
pub(crate) struct LocationComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedLocation>,
    missing: Vec<NormalizedLocation>,
    extra: Vec<NormalizedLocation>,
    suprnova_lsp_unmapped_count: usize,
    rust_analyzer_unmapped_count: usize,
    suprnova_lsp_unmapped: Vec<String>,
    rust_analyzer_unmapped: Vec<String>,
}

impl LocationComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedLocationSet,
        rust_analyzer: &NormalizedLocationSet,
    ) -> Self {
        let suprnova_lsp_locations = suprnova_lsp
            .locations()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        let rust_analyzer_locations = rust_analyzer
            .locations()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();

        // The normalized lists are already deterministic, but using set operations here makes the
        // scoring rules explicit and keeps missing/extra details ready for the report slice.
        let matched = suprnova_lsp_locations
            .intersection(&rust_analyzer_locations)
            .cloned()
            .collect();
        let missing = rust_analyzer_locations
            .difference(&suprnova_lsp_locations)
            .cloned()
            .collect();
        let extra = suprnova_lsp_locations
            .difference(&rust_analyzer_locations)
            .cloned()
            .collect();

        Self {
            suprnova_lsp_count: suprnova_lsp_locations.len(),
            rust_analyzer_count: rust_analyzer_locations.len(),
            matched,
            missing,
            extra,
            suprnova_lsp_unmapped_count: suprnova_lsp.unmapped_count(),
            rust_analyzer_unmapped_count: rust_analyzer.unmapped_count(),
            suprnova_lsp_unmapped: suprnova_lsp.unmapped_summaries(),
            rust_analyzer_unmapped: rust_analyzer.unmapped_summaries(),
        }
    }

    pub(crate) fn metrics(&self) -> MappedSetComparisonMetrics {
        MappedSetComparisonMetrics {
            set: SetComparisonMetrics::new(
                self.suprnova_lsp_count,
                self.rust_analyzer_count,
                self.matched.len(),
                self.missing.len(),
                self.extra.len(),
            ),
            suprnova_lsp_unmapped_count: self.suprnova_lsp_unmapped_count,
            rust_analyzer_unmapped_count: self.rust_analyzer_unmapped_count,
            suprnova_lsp_unmapped: self.suprnova_lsp_unmapped.clone(),
            rust_analyzer_unmapped: self.rust_analyzer_unmapped.clone(),
        }
    }

    #[cfg(test)]
    pub(super) fn matched(&self) -> &[NormalizedLocation] {
        &self.matched
    }

    pub(super) fn missing(&self) -> &[NormalizedLocation] {
        &self.missing
    }

    pub(super) fn extra(&self) -> &[NormalizedLocation] {
        &self.extra
    }
}

#[derive(Debug, Default)]
pub(crate) struct LocationAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_locations: usize,
    rust_analyzer_locations: usize,
    matched_locations: usize,
    missing_locations: usize,
    extra_locations: usize,
    suprnova_lsp_unmapped_locations: usize,
    rust_analyzer_unmapped_locations: usize,
}

impl LocationAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::Locations(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_locations += comparison.suprnova_lsp_count;
                self.rust_analyzer_locations += comparison.rust_analyzer_count;
                self.matched_locations += comparison.matched.len();
                self.missing_locations += comparison.missing.len();
                self.extra_locations += comparison.extra.len();
                self.suprnova_lsp_unmapped_locations += comparison.suprnova_lsp_unmapped_count;
                self.rust_analyzer_unmapped_locations += comparison.rust_analyzer_unmapped_count;
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

    pub(crate) fn metrics(&self) -> MappedSetAggregateMetrics {
        MappedSetAggregateMetrics {
            set: SetComparisonMetrics::new(
                self.suprnova_lsp_locations,
                self.rust_analyzer_locations,
                self.matched_locations,
                self.missing_locations,
                self.extra_locations,
            ),
            suprnova_lsp_unmapped_count: self.suprnova_lsp_unmapped_locations,
            rust_analyzer_unmapped_count: self.rust_analyzer_unmapped_locations,
        }
    }
}
