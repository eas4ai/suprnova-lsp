# Limitations

Suprnova LSP is incomplete, and has a bunch of quirks that are worth knowing about.

## Ultimate advice

If something unexpected happens: you start getting a lot of errors, index is messed up, LSP stops responding, etc:
- Try hitting `ctrl/cmd+shift+P` and sending `Suprnova LSP: Reindex workspace` command.
- Try restarting the server: click on `Suprnova LSP` on the bottom left of VS Code.
- If that doesn't help, stop the editor, remove `target/suprnova_lsp`, and start again.

If you know how to reproduce the issue, it would be great if you also [report it](https://github.com/eas4ai/suprnova-lsp/issues).

It shouldn't happen often, but you know how it is with young software.
I intentionally don't implement any sophisticated recovery mechanisms: the idea is that the LSP should never fail,
so when it does, it has to be loud. So if you meet a crash or indexing issue -- sorry, but I hope that it will
help us build a very reliable project long term.

## Dirty buffers

Frozen workspace analysis can work on each keystroke, and it is actually usable, but it falls
into a category that is workable but annoying enough to drive one insane. So to mitigate that,
dirty buffers cheat a bit: when hover/completion/etc is requested in a dirty buffer, we take
the _current entity around the cursor_ and analyze its current state against the saved project.

This is already enough for typing, and in this example (`$` denotes the cursor) you will see
the completions:

```rust
fn calculate_something() {
    let foo = SomeComplicatedType::new();
    foo.do_some$
}
```

Local variables, types, methods are also available.

However, it gets tricky with new impl blocks. We try to recover new items based on syntax,
but we don't add them to the project-wise analysis, so, for example, you will see methods from a
newly typed `impl` block as you type them, same for the trait impls, but they will only work while
you are inside of the corresponding block. If you will move to a different `impl`, it will not see
the changes from the `impl` you edited before until you save.

Module-level changes, such as adding a new import, are also not automatically resolved, they only
take effect after saving a file.

So for almost all the normal flows it should be convenient, but you will need to save the file when
you need a change you made to take effect outside of the place where you type. 

If it sounds scary, just try it -- it really isn't,
and you can get used to it pretty quickly. And thanks to that, editing experience remains smooth.

## Build scripts and compiler exports

Source-only indexing reads existing Cargo build artifacts. It does not execute
build scripts to produce missing outputs. If the project has never been built,
or its artifacts were removed, some generated imports may remain unresolved.
Run `cargo check` or `cargo build`, then reindex to pick up the outputs.

Automatic Suprnova model support uses a different path: its background worker
runs `cargo rustdoc`, which can execute build scripts and proc macros. Saved
input changes trigger a debounced refresh. Configure `suprnova-lsp.rustdoc.automatic`
to disable automatic exports, or use prepared inputs when you want to supply
compiler declarations explicitly. These declarations describe saved code;
unsaved macro or global declaration changes still need a save before export.

Cargo diagnostics are separate and remain disabled by default. Enable them
through the diagnostics settings when you want compiler error reports.
