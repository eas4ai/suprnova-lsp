//! Rename workflow comparison and aggregation.

use std::collections::BTreeSet;

use super::metrics::{
    AggregateSummaryMetrics, MappedSetAggregateMetrics, MappedSetComparisonMetrics,
    SetComparisonMetrics,
};
use crate::compare_lsp::{
    comparison::{QueryComparison, QueryComparisonResult},
    normalization::{
        NormalizedPrepareRenameSet, NormalizedPrepareRenameTarget, NormalizedTextEdit,
        NormalizedTextEditSet,
    },
};

#[derive(Debug)]
pub(crate) struct PrepareRenameComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedPrepareRenameTarget>,
    compatible: Vec<(NormalizedPrepareRenameTarget, NormalizedPrepareRenameTarget)>,
    missing: Vec<NormalizedPrepareRenameTarget>,
    extra: Vec<NormalizedPrepareRenameTarget>,
}

impl PrepareRenameComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedPrepareRenameSet,
        rust_analyzer: &NormalizedPrepareRenameSet,
    ) -> Self {
        let suprnova_lsp_targets = suprnova_lsp
            .targets()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        let rust_analyzer_targets = rust_analyzer
            .targets()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();

        let mut matched = suprnova_lsp_targets
            .intersection(&rust_analyzer_targets)
            .cloned()
            .collect::<Vec<_>>();
        let mut missing = rust_analyzer_targets
            .difference(&suprnova_lsp_targets)
            .cloned()
            .collect::<Vec<_>>();
        let unmatched_extra = suprnova_lsp_targets
            .difference(&rust_analyzer_targets)
            .cloned()
            .collect::<Vec<_>>();

        // Optional response data is directional. A valid suprnova-lsp placeholder is compatible
        // with rust-analyzer returning only the same range; omitting rust-analyzer's placeholder is
        // still reported as a loss.
        let mut compatible = Vec::new();
        let mut extra = Vec::new();
        for suprnova_lsp_target in unmatched_extra {
            let Some(reference_index) = missing
                .iter()
                .position(|reference| suprnova_lsp_target.is_no_worse_match_for(reference))
            else {
                extra.push(suprnova_lsp_target);
                continue;
            };

            let rust_analyzer_target = missing.remove(reference_index);
            matched.push(suprnova_lsp_target.clone());
            compatible.push((suprnova_lsp_target, rust_analyzer_target));
        }
        matched.sort();

        Self {
            suprnova_lsp_count: suprnova_lsp_targets.len(),
            rust_analyzer_count: rust_analyzer_targets.len(),
            matched,
            compatible,
            missing,
            extra,
        }
    }

    pub(crate) fn metrics(&self) -> SetComparisonMetrics {
        SetComparisonMetrics::new_with_compatible_matches(
            self.suprnova_lsp_count,
            self.rust_analyzer_count,
            self.matched.len() - self.compatible.len(),
            self.compatible.len(),
            self.missing.len(),
            self.extra.len(),
        )
    }

    pub(super) fn compatible(
        &self,
    ) -> &[(NormalizedPrepareRenameTarget, NormalizedPrepareRenameTarget)] {
        &self.compatible
    }

    pub(super) fn missing(&self) -> &[NormalizedPrepareRenameTarget] {
        &self.missing
    }

    pub(super) fn extra(&self) -> &[NormalizedPrepareRenameTarget] {
        &self.extra
    }
}

#[derive(Debug, Default)]
pub(crate) struct PrepareRenameAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_targets: usize,
    rust_analyzer_targets: usize,
    matched_targets: usize,
    compatible_targets: usize,
    missing_targets: usize,
    extra_targets: usize,
}

impl PrepareRenameAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::PrepareRenames(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_targets += comparison.suprnova_lsp_count;
                self.rust_analyzer_targets += comparison.rust_analyzer_count;
                self.matched_targets += comparison.matched.len();
                self.compatible_targets += comparison.compatible.len();
                self.missing_targets += comparison.missing.len();
                self.extra_targets += comparison.extra.len();
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
        SetComparisonMetrics::new_with_compatible_matches(
            self.suprnova_lsp_targets,
            self.rust_analyzer_targets,
            self.matched_targets - self.compatible_targets,
            self.compatible_targets,
            self.missing_targets,
            self.extra_targets,
        )
    }
}

#[derive(Debug)]
pub(crate) struct RenameEditComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedTextEdit>,
    missing: Vec<NormalizedTextEdit>,
    extra: Vec<NormalizedTextEdit>,
    suprnova_lsp_unmapped_count: usize,
    rust_analyzer_unmapped_count: usize,
    suprnova_lsp_unmapped: Vec<String>,
    rust_analyzer_unmapped: Vec<String>,
}

impl RenameEditComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedTextEditSet,
        rust_analyzer: &NormalizedTextEditSet,
    ) -> Self {
        let suprnova_lsp_edits = suprnova_lsp
            .edits()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        let rust_analyzer_edits = rust_analyzer
            .edits()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();

        let matched = suprnova_lsp_edits
            .intersection(&rust_analyzer_edits)
            .cloned()
            .collect();
        let missing = rust_analyzer_edits
            .difference(&suprnova_lsp_edits)
            .cloned()
            .collect();
        let extra = suprnova_lsp_edits
            .difference(&rust_analyzer_edits)
            .cloned()
            .collect();

        Self {
            suprnova_lsp_count: suprnova_lsp_edits.len(),
            rust_analyzer_count: rust_analyzer_edits.len(),
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

    pub(super) fn missing(&self) -> &[NormalizedTextEdit] {
        &self.missing
    }

    pub(super) fn extra(&self) -> &[NormalizedTextEdit] {
        &self.extra
    }
}

#[derive(Debug, Default)]
pub(crate) struct RenameEditAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_edits: usize,
    rust_analyzer_edits: usize,
    matched_edits: usize,
    missing_edits: usize,
    extra_edits: usize,
    suprnova_lsp_unmapped_edits: usize,
    rust_analyzer_unmapped_edits: usize,
}

impl RenameEditAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::RenameEdits(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_edits += comparison.suprnova_lsp_count;
                self.rust_analyzer_edits += comparison.rust_analyzer_count;
                self.matched_edits += comparison.matched.len();
                self.missing_edits += comparison.missing.len();
                self.extra_edits += comparison.extra.len();
                self.suprnova_lsp_unmapped_edits += comparison.suprnova_lsp_unmapped_count;
                self.rust_analyzer_unmapped_edits += comparison.rust_analyzer_unmapped_count;
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
                self.suprnova_lsp_edits,
                self.rust_analyzer_edits,
                self.matched_edits,
                self.missing_edits,
                self.extra_edits,
            ),
            suprnova_lsp_unmapped_count: self.suprnova_lsp_unmapped_edits,
            rust_analyzer_unmapped_count: self.rust_analyzer_unmapped_edits,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::PrepareRenameComparison;
    use crate::compare_lsp::normalization::{
        NormalizedPrepareRenameSet, NormalizedPrepareRenameTarget, NormalizedRange,
    };

    #[test]
    fn accepts_a_valid_placeholder_when_the_reference_returns_only_a_range() {
        let range = Some(NormalizedRange::test_new(3, 8, 3, 17));
        let suprnova_lsp = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range,
            Some("user_name"),
            true,
        )]);
        let rust_analyzer = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range, None, true,
        )]);

        let comparison = PrepareRenameComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics();

        assert_eq!(metrics.matched_count, 1);
        assert_eq!(metrics.compatible_count, 1);
        assert_eq!(metrics.missing_count, 0);
        assert_eq!(metrics.extra_count, 0);
    }

    #[test]
    fn rejects_dropping_a_reference_placeholder() {
        let range = Some(NormalizedRange::test_new(3, 8, 3, 17));
        let suprnova_lsp = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range, None, true,
        )]);
        let rust_analyzer = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range,
            Some("user_name"),
            true,
        )]);

        let comparison = PrepareRenameComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics();

        assert_eq!(metrics.matched_count, 0);
        assert_eq!(metrics.compatible_count, 0);
        assert_eq!(metrics.missing_count, 1);
        assert_eq!(metrics.extra_count, 1);
    }

    #[test]
    fn rejects_a_placeholder_that_does_not_match_source() {
        let range = Some(NormalizedRange::test_new(3, 8, 3, 17));
        let suprnova_lsp = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range,
            Some("other_name"),
            false,
        )]);
        let rust_analyzer = targets(vec![NormalizedPrepareRenameTarget::test_new(
            range, None, true,
        )]);

        let comparison = PrepareRenameComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics();

        assert_eq!(metrics.matched_count, 0);
        assert_eq!(metrics.compatible_count, 0);
        assert_eq!(metrics.missing_count, 1);
        assert_eq!(metrics.extra_count, 1);
    }

    fn targets(targets: Vec<NormalizedPrepareRenameTarget>) -> NormalizedPrepareRenameSet {
        NormalizedPrepareRenameSet::test_from_targets(targets)
    }
}
