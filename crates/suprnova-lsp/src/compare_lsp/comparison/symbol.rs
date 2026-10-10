//! Symbol-result comparison and aggregation.

use std::collections::BTreeSet;

use super::metrics::{
    AggregateSummaryMetrics, MappedSetAggregateMetrics, MappedSetComparisonMetrics,
    SetComparisonMetrics,
};
use crate::compare_lsp::{
    comparison::{QueryComparison, QueryComparisonResult},
    normalization::{NormalizedSymbol, NormalizedSymbolSet},
};

#[derive(Debug)]
pub(crate) struct SymbolComparison {
    suprnova_lsp_count: usize,
    rust_analyzer_count: usize,
    matched: Vec<NormalizedSymbol>,
    compatible: Vec<(NormalizedSymbol, NormalizedSymbol)>,
    missing: Vec<NormalizedSymbol>,
    extra: Vec<NormalizedSymbol>,
    suprnova_lsp_unmapped_count: usize,
    rust_analyzer_unmapped_count: usize,
    suprnova_lsp_unmapped: Vec<String>,
    rust_analyzer_unmapped: Vec<String>,
}

impl SymbolComparison {
    pub(super) fn new(
        suprnova_lsp: &NormalizedSymbolSet,
        rust_analyzer: &NormalizedSymbolSet,
    ) -> Self {
        let suprnova_lsp_symbols = suprnova_lsp
            .symbols()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        let rust_analyzer_symbols = rust_analyzer
            .symbols()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();

        let mut matched = suprnova_lsp_symbols
            .intersection(&rust_analyzer_symbols)
            .cloned()
            .collect::<Vec<_>>();
        let mut missing = rust_analyzer_symbols
            .difference(&suprnova_lsp_symbols)
            .cloned()
            .collect::<Vec<_>>();
        let unmatched_extra = suprnova_lsp_symbols
            .difference(&rust_analyzer_symbols)
            .cloned()
            .collect::<Vec<_>>();

        let mut compatible = Vec::new();
        let mut extra = Vec::new();
        for suprnova_lsp_symbol in unmatched_extra {
            let Some(reference_index) = missing
                .iter()
                .position(|reference| suprnova_lsp_symbol.is_no_worse_match_for(reference))
            else {
                extra.push(suprnova_lsp_symbol);
                continue;
            };

            let rust_analyzer_symbol = missing.remove(reference_index);
            matched.push(suprnova_lsp_symbol.clone());
            compatible.push((suprnova_lsp_symbol, rust_analyzer_symbol));
        }
        matched.sort();

        Self {
            suprnova_lsp_count: suprnova_lsp_symbols.len(),
            rust_analyzer_count: rust_analyzer_symbols.len(),
            matched,
            compatible,
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
            set: SetComparisonMetrics::new_with_compatible_matches(
                self.suprnova_lsp_count,
                self.rust_analyzer_count,
                self.matched.len() - self.compatible.len(),
                self.compatible.len(),
                self.missing.len(),
                self.extra.len(),
            ),
            suprnova_lsp_unmapped_count: self.suprnova_lsp_unmapped_count,
            rust_analyzer_unmapped_count: self.rust_analyzer_unmapped_count,
            suprnova_lsp_unmapped: self.suprnova_lsp_unmapped.clone(),
            rust_analyzer_unmapped: self.rust_analyzer_unmapped.clone(),
        }
    }

    pub(super) fn missing(&self) -> &[NormalizedSymbol] {
        &self.missing
    }

    pub(super) fn extra(&self) -> &[NormalizedSymbol] {
        &self.extra
    }

    pub(super) fn compatible(&self) -> &[(NormalizedSymbol, NormalizedSymbol)] {
        &self.compatible
    }
}

#[derive(Debug, Default)]
pub(crate) struct SymbolAggregate {
    query_count: usize,
    comparable_count: usize,
    non_comparable_count: usize,
    suprnova_lsp_symbols: usize,
    rust_analyzer_symbols: usize,
    matched_symbols: usize,
    compatible_symbols: usize,
    missing_symbols: usize,
    extra_symbols: usize,
    suprnova_lsp_unmapped_symbols: usize,
    rust_analyzer_unmapped_symbols: usize,
}

impl SymbolAggregate {
    pub(super) fn record(&mut self, query: &QueryComparison) {
        self.query_count += 1;
        match query.result() {
            QueryComparisonResult::Symbols(comparison) => {
                self.comparable_count += 1;
                self.suprnova_lsp_symbols += comparison.suprnova_lsp_count;
                self.rust_analyzer_symbols += comparison.rust_analyzer_count;
                self.matched_symbols += comparison.matched.len();
                self.compatible_symbols += comparison.compatible.len();
                self.missing_symbols += comparison.missing.len();
                self.extra_symbols += comparison.extra.len();
                self.suprnova_lsp_unmapped_symbols += comparison.suprnova_lsp_unmapped_count;
                self.rust_analyzer_unmapped_symbols += comparison.rust_analyzer_unmapped_count;
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
            set: SetComparisonMetrics::new_with_compatible_matches(
                self.suprnova_lsp_symbols,
                self.rust_analyzer_symbols,
                self.matched_symbols - self.compatible_symbols,
                self.compatible_symbols,
                self.missing_symbols,
                self.extra_symbols,
            ),
            suprnova_lsp_unmapped_count: self.suprnova_lsp_unmapped_symbols,
            rust_analyzer_unmapped_count: self.rust_analyzer_unmapped_symbols,
        }
    }
}

#[cfg(test)]
mod tests {
    use gen_lsp_types::SymbolKind;

    use super::SymbolComparison;
    use crate::compare_lsp::normalization::{
        NormalizedRange, NormalizedSymbol, NormalizedSymbolSet,
    };

    #[test]
    fn accepts_method_as_a_more_specific_function_classification() {
        let range = NormalizedRange::test_new(8, 11, 8, 15);
        let suprnova_lsp = symbols(vec![symbol(SymbolKind::Method, range)]);
        let rust_analyzer = symbols(vec![symbol(SymbolKind::Function, range)]);

        let comparison = SymbolComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics().set;

        assert_eq!(metrics.matched_count, 1);
        assert_eq!(metrics.compatible_count, 1);
        assert_eq!(metrics.missing_count, 0);
        assert_eq!(metrics.extra_count, 0);
    }

    #[test]
    fn rejects_function_when_the_reference_knows_it_is_a_method() {
        let range = NormalizedRange::test_new(8, 11, 8, 15);
        let suprnova_lsp = symbols(vec![symbol(SymbolKind::Function, range)]);
        let rust_analyzer = symbols(vec![symbol(SymbolKind::Method, range)]);

        let comparison = SymbolComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics().set;

        assert_eq!(metrics.matched_count, 0);
        assert_eq!(metrics.compatible_count, 0);
        assert_eq!(metrics.missing_count, 1);
        assert_eq!(metrics.extra_count, 1);
    }

    #[test]
    fn keeps_broader_suprnova_lsp_symbol_ranges_as_divergences() {
        let focused = NormalizedRange::test_new(8, 5, 8, 15);
        let whole_impl = NormalizedRange::test_new(8, 0, 14, 1);
        let suprnova_lsp = symbols(vec![symbol(SymbolKind::Object, whole_impl)]);
        let rust_analyzer = symbols(vec![symbol(SymbolKind::Object, focused)]);

        let comparison = SymbolComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics().set;

        assert_eq!(metrics.matched_count, 0);
        assert_eq!(metrics.compatible_count, 0);
        assert_eq!(metrics.missing_count, 1);
        assert_eq!(metrics.extra_count, 1);
    }

    #[test]
    fn keeps_unrelated_lossy_type_kinds_as_divergences() {
        let range = NormalizedRange::test_new(8, 5, 8, 14);
        let suprnova_lsp = symbols(vec![symbol(SymbolKind::Class, range)]);
        let rust_analyzer = symbols(vec![symbol(SymbolKind::TypeParameter, range)]);

        let comparison = SymbolComparison::new(&suprnova_lsp, &rust_analyzer);
        let metrics = comparison.metrics().set;

        assert_eq!(metrics.matched_count, 0);
        assert_eq!(metrics.compatible_count, 0);
        assert_eq!(metrics.missing_count, 1);
        assert_eq!(metrics.extra_count, 1);
    }

    fn symbols(symbols: Vec<NormalizedSymbol>) -> NormalizedSymbolSet {
        NormalizedSymbolSet::test_from_symbols(symbols)
    }

    fn symbol(kind: SymbolKind, range: NormalizedRange) -> NormalizedSymbol {
        NormalizedSymbol::test_new("save", kind, Some("src/lib.rs"), Some(range))
    }
}
