//! Inlay-hint comparison and aggregation.

use std::collections::{BTreeMap, BTreeSet};

use super::metrics::{AggregateSummaryMetrics, SetComparisonMetrics};
use crate::compare_lsp::{
    comparison::{QueryComparison, QueryComparisonResult},
    normalization::{NormalizedInlayHint, NormalizedInlayHintSet},
};

/// Compares inlay hints by line-local label sequences.
///
/// Inlay hints are visually attached to character positions, but small position differences are
/// not very meaningful for this benchmark while suprnova-lsp is still not trying to be a perfect
/// rust-analyzer replica. For each source line, we compare only hint labels and preserve their
/// relative order, so inserted or omitted hints do not turn every later hint on the line into a
/// mismatch.
#[derive(Debug)]
pub(crate) struct InlayHintComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedInlayHint>,
    missing: Vec<NormalizedInlayHint>,
    extra: Vec<NormalizedInlayHint>,
}

impl InlayHintComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedInlayHintSet,
        rust_analyzer: &NormalizedInlayHintSet,
    ) -> Self {
        let suprnova_lsp_by_line = Self::group_by_line(suprnova_lsp.hints());
        let rust_analyzer_by_line = Self::group_by_line(rust_analyzer.hints());

        let mut matched = Vec::new();
        let mut missing = Vec::new();
        let mut extra = Vec::new();
        let lines = suprnova_lsp_by_line
            .keys()
            .chain(rust_analyzer_by_line.keys())
            .copied()
            .collect::<BTreeSet<_>>();
        let empty_hints: &[&NormalizedInlayHint] = &[];

        for line in lines {
            let suprnova_lsp_hints = suprnova_lsp_by_line
                .get(&line)
                .map(Vec::as_slice)
                .unwrap_or(empty_hints);
            let rust_analyzer_hints = rust_analyzer_by_line
                .get(&line)
                .map(Vec::as_slice)
                .unwrap_or(empty_hints);

            Self::compare_line(
                suprnova_lsp_hints,
                rust_analyzer_hints,
                &mut matched,
                &mut missing,
                &mut extra,
            );
        }

        Self {
            suprnova_lsp_count: suprnova_lsp.hints().len(),
            rust_analyzer_count: rust_analyzer.hints().len(),
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

    fn group_by_line(hints: &[NormalizedInlayHint]) -> BTreeMap<u32, Vec<&NormalizedInlayHint>> {
        let mut by_line = BTreeMap::new();
        for hint in hints {
            by_line
                .entry(hint.line())
                .or_insert_with(Vec::new)
                .push(hint);
        }

        by_line
    }

    fn compare_line(
        suprnova_lsp: &[&NormalizedInlayHint],
        rust_analyzer: &[&NormalizedInlayHint],
        matched: &mut Vec<NormalizedInlayHint>,
        missing: &mut Vec<NormalizedInlayHint>,
        extra: &mut Vec<NormalizedInlayHint>,
    ) {
        // Treat a line as a sequence of hint labels. The exact character offset is allowed to
        // drift, but the relative order of hints on that line still has to agree.
        let pairs = Self::ordered_label_matches(suprnova_lsp, rust_analyzer);
        let mut suprnova_lsp_matched = vec![false; suprnova_lsp.len()];
        let mut rust_analyzer_matched = vec![false; rust_analyzer.len()];

        for (suprnova_lsp_index, rust_analyzer_index) in pairs {
            suprnova_lsp_matched[suprnova_lsp_index] = true;
            rust_analyzer_matched[rust_analyzer_index] = true;
            matched.push((*suprnova_lsp[suprnova_lsp_index]).clone());
        }

        for (index, hint) in rust_analyzer.iter().enumerate() {
            if !rust_analyzer_matched[index] {
                missing.push((**hint).clone());
            }
        }

        for (index, hint) in suprnova_lsp.iter().enumerate() {
            if !suprnova_lsp_matched[index] {
                extra.push((**hint).clone());
            }
        }
    }

    fn ordered_label_matches(
        suprnova_lsp: &[&NormalizedInlayHint],
        rust_analyzer: &[&NormalizedInlayHint],
    ) -> Vec<(usize, usize)> {
        let mut lengths = vec![vec![0; rust_analyzer.len() + 1]; suprnova_lsp.len() + 1];

        for suprnova_lsp_index in (0..suprnova_lsp.len()).rev() {
            for rust_analyzer_index in (0..rust_analyzer.len()).rev() {
                lengths[suprnova_lsp_index][rust_analyzer_index] =
                    if suprnova_lsp[suprnova_lsp_index].label()
                        == rust_analyzer[rust_analyzer_index].label()
                    {
                        lengths[suprnova_lsp_index + 1][rust_analyzer_index + 1] + 1
                    } else {
                        lengths[suprnova_lsp_index + 1][rust_analyzer_index]
                            .max(lengths[suprnova_lsp_index][rust_analyzer_index + 1])
                    };
            }
        }

        let mut matches = Vec::new();
        let mut suprnova_lsp_index = 0;
        let mut rust_analyzer_index = 0;

        while suprnova_lsp_index < suprnova_lsp.len() && rust_analyzer_index < rust_analyzer.len() {
            if suprnova_lsp[suprnova_lsp_index].label()
                == rust_analyzer[rust_analyzer_index].label()
            {
                matches.push((suprnova_lsp_index, rust_analyzer_index));
                suprnova_lsp_index += 1;
                rust_analyzer_index += 1;
            } else if lengths[suprnova_lsp_index + 1][rust_analyzer_index]
                >= lengths[suprnova_lsp_index][rust_analyzer_index + 1]
            {
                suprnova_lsp_index += 1;
            } else {
                rust_analyzer_index += 1;
            }
        }

        matches
    }
}

#[derive(Debug, Default)]
pub(crate) struct InlayHintAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_hints: usize,
    rust_analyzer_hints: usize,
    matched_hints: usize,
    missing_hints: usize,
    extra_hints: usize,
}

impl InlayHintAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::InlayHints(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_hints += comparison.suprnova_lsp_count;
                self.rust_analyzer_hints += comparison.rust_analyzer_count;
                self.matched_hints += comparison.matched.len();
                self.missing_hints += comparison.missing.len();
                self.extra_hints += comparison.extra.len();
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
            self.suprnova_lsp_hints,
            self.rust_analyzer_hints,
            self.matched_hints,
            self.missing_hints,
            self.extra_hints,
        )
    }
}
