//! Entry point for ``mcp-workspace-supervisor``.
//!
//! Day 1 keeps this thin: parse args, load + validate config, set up
//! tracing, and exit with a placeholder. The agent process manager,
//! backend client, and handlers come in subsequent commits.

use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{Context, Result};
use clap::Parser;
use tracing::{error, info};

mod config;
mod log_capture;
#[cfg(not(windows))]
mod platform_posix;
#[cfg(windows)]
mod platform_windows;
mod process;
mod protocol;

#[derive(Debug, Parser)]
#[command(name = "mcp-workspace-supervisor", version, about)]
struct Cli {
    /// Path to the TOML config file.
    #[arg(long)]
    config: PathBuf,
}

fn main() -> ExitCode {
    if let Err(e) = run() {
        eprintln!("supervisor: fatal: {e:#}");
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}

fn run() -> Result<()> {
    init_tracing();

    let cli = Cli::parse();
    let cfg = config::Config::load(&cli.config)
        .with_context(|| format!("failed to load {}", cli.config.display()))?;
    info!(
        backend = %cfg.backend.url,
        agent_cwd = %cfg.agent.cwd.display(),
        "config loaded"
    );

    // The full runtime (tokio + agent manager + backend client) is wired
    // up in the next commits. For Day 1 we stop here so the binary
    // builds, runs, and validates the config — useful for CI / packaging.
    info!("supervisor scaffolding ready; runtime wiring pending");
    Ok(())
}

fn init_tracing() {
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    if let Err(e) = tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_target(false)
        .try_init()
    {
        // Already initialised by something upstream — non-fatal.
        let _ = e;
        error!("tracing was already initialised; continuing");
    }
}
