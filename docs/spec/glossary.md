# Glossary

| Term | Meaning and code owner |
| --- | --- |
| Cargo package | A package identified through `rg_workspace`, including its crate targets and dependencies. |
| Crate target | One lib, bin, or other compilation target belonging to a Cargo package. |
| Project | A saved analysis generation owned by `rg_project::Project`. |
| DefMap | Definitions, modules, imports, and impl ownership resolved by `rg_def_map`. |
| GeneratedItemStore | Transient generated declarations used to construct Semantic IR. |
| Semantic IR | Declaration-level type, trait, and impl data owned by `rg_semantic_ir`. |
| Body IR | Expression and local binding analysis owned by `rg_body_ir`. |
| Rustdoc export | One compiler-produced JSON artifact read by `rg_rustdoc::RustdocExport`. |
| Export-local ID | An identifier meaningful only inside one rustdoc export; it is not a persistent project identity. |
| Nominal type | A struct, enum, or union selected by its fully qualified compiler path. |
| Concrete impl | A non-synthetic, non-blanket impl attached to a selected nominal type, including reference and fundamental Box wrappers. |
| Package artifact | Saved phase data managed by the existing project storage layer. |

These are descriptions of existing code identifiers, not newly Agreed behavior. [Recon and ownership evidence](../recon.md).
