//! Same-document range comparison and aggregation.

use std::collections::BTreeSet;

use super::metrics::{AggregateSummaryMetrics, SetComparisonMetrics};
use crate::compare_lsp::{
    comparison::{QueryComparison, QueryComparisonResult},
    normalization::{NormalizedRange, NormalizedRangeSet},
};

#[derive(Debug)]
pub(crate) struct RangeComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedRange>,
    missing: Vec<NormalizedRange>,
    extra: Vec<NormalizedRange>,
}

impl RangeComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedRangeSet,
        rust_analyzer: &NormalizedRangeSet,
    ) -> Self {
        let suprnova_lsp_ranges = suprnova_lsp
            .ranges()
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();
        let rust_analyzer_ranges = rust_analyzer
            .ranges()
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();

        // Text/Read/Write kinds are a part of protocol, but they have very minor impact on the LSP experience,
        // thus we intentionally ignore them. It is unlikely that anyone will notice, and there are way more
        // high-priority work out there. It's not a TODO, it's a deprioritized item.
        let matched = suprnova_lsp_ranges
            .intersection(&rust_analyzer_ranges)
            .copied()
            .collect();
        let missing = rust_analyzer_ranges
            .difference(&suprnova_lsp_ranges)
            .copied()
            .collect();
        let extra = suprnova_lsp_ranges
            .difference(&rust_analyzer_ranges)
            .copied()
            .collect();

        Self {
            suprnova_lsp_count: suprnova_lsp_ranges.len(),
            rust_analyzer_count: rust_analyzer_ranges.len(),
            matched,
            missing,
            extra,
        }
    }

    pub(crate) fn metrics(&self) -> SetComparisonMetrics {
        SetComparisonMetrics::new(
            self.suprnova_lsp_count,
            self.rust_analyzer_count,
            self.matched.len(),
            self.missing.len(),
            self.extra.len(),
        )
    }

    pub(super) fn missing(&self) -> &[NormalizedRange] {
        &self.missing
    }

    pub(super) fn extra(&self) -> &[NormalizedRange] {
        &self.extra
    }
}

#[derive(Debug, Default)]
pub(crate) struct RangeAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_ranges: usize,
    rust_analyzer_ranges: usize,
    matched_ranges: usize,
    missing_ranges: usize,
    extra_ranges: usize,
}

impl RangeAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::Ranges(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_ranges += comparison.suprnova_lsp_count;
                self.rust_analyzer_ranges += comparison.rust_analyzer_count;
                self.matched_ranges += comparison.matched.len();
                self.missing_ranges += comparison.missing.len();
                self.extra_ranges += comparison.extra.len();
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
            self.suprnova_lsp_ranges,
            self.rust_analyzer_ranges,
            self.matched_ranges,
            self.missing_ranges,
            self.extra_ranges,
        )
    }
}
