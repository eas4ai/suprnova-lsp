# Suprnova LSP

An experimental Rust language server for Suprnova applications, optimized for
low idle memory and fast editor restarts. It imports compiler-produced rustdoc
JSON for generated model APIs without running rust-analyzer.

This project aims to be 90% complete, being complete enough for day-to-day work
without trying to be rust-analyzer 2.0.

This fork lives at [eas4ai/suprnova-lsp](https://github.com/eas4ai/suprnova-lsp).

## Installation

Build the server with `cargo build --release --locked -p suprnova-lsp`.
For VS Code, run `npm ci` and `npm run package:vsix` in `editors/code`, then
install the generated local VSIX. See the [extension guide](editors/code/README.md)
for configuration and development commands.

The extension ID is `eas4ai.suprnova-lsp`. Settings and commands use
`suprnova-lsp.*`; copy desired upstream settings to that prefix manually.
Automatic rustdoc exports use a two-second debounce. Explicit prepared exports
use `suprnova-lsp.rustdoc.inputs`. Server logging uses `SUPRNOVA_LSP_LOG`.

## AI use disclaimer

This project is being built with heavy use of LLMs.
LLMs are used as a tool, not as a brain replacement.

I do consider the code to be _my_, and I am spending a lot of time caring about the code
quality. So I consider it to be rather readable and maintainable. It might not be the
idiomatic example of compiler-adjacent tooling (I don't have that much domain experience),
but I am working on improving it as I work on it.

So if it is slop, then it is _my_ slop, and the best way to help is to tell me what's
wrong. This way I will be able to learn something and hopefully make the project better.

## Acknowledgements

[Rust Glancer](https://github.com/rust-glancer/rust-glancer), created by Igor
Aleksanov, provides this fork's language-server foundation. Its original author
credits and dual license are preserved.

[rust-analyzer](https://github.com/rust-lang/rust-analyzer) is an obvious inspiration, motivation, source of learning material and the place where I've hijacked a ton of ideas. Rust is lucky to have such a great LSP, and everyone working on it is awesome.

[chalk](https://github.com/rust-lang/chalk) turned out a really pleasant project to integrate, and it comes with [lovely documentation](https://rust-lang.github.io/chalk/book/).

Without [jemalloc](https://github.com/jemalloc/jemalloc) and [tikv-jemallocator](https://github.com/tikv/jemallocator) this project wouldn't have been possible. Not only it's awesome as an allocator, but it's also saved me hundreds of hours profiling allocations.

On the same note, [mimalloc](https://github.com/microsoft/mimalloc) and [mimalloc_rust](https://github.com/purpleprotocol/mimalloc_rust) turned out to work even better for the purpose of this project.

## Contributing

See [CONTRIBUTING.md](docs/src/intro/CONTRIBUTING.md).

## License

Licensed under either of:
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option.
