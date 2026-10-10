use std::{env, io, process::Command};

fn main() -> io::Result<()> {
    let python = env::var_os("AUTOMATIC_RUSTDOC_PYTHON")
        .ok_or_else(|| io::Error::other("automatic Cargo proxy Python path is missing"))?;
    let proxy = env::var_os("AUTOMATIC_RUSTDOC_PROXY")
        .ok_or_else(|| io::Error::other("automatic Cargo proxy script path is missing"))?;
    // Rust's Windows Cargo lookup requires an executable. Forward native arguments directly
    // so paths, quotes and shell characters never pass through a command interpreter.
    let status = Command::new(python)
        .arg(proxy)
        .args(env::args_os().skip(1))
        .status()?;
    std::process::exit(status.code().unwrap_or(1));
}
