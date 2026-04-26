//! mcp-workspace-agent-rs — Rust port of the Python workspace agent.
//!
//! PoC scope (task `agent-rs/01`): WS connect + auth + `agent_info` +
//! ping/pong only. Subsequent tasks add handlers (`exec`, `read_file`,
//! `grep`, PTY, self-update).

use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{Context, Result};
use clap::Parser;
use tracing::{error, info};

mod client;
mod path_safety;
mod proto;
mod version;

#[derive(Debug, Parser)]
#[command(name = "mcp-workspace-agent", version, about)]
struct Cli {
    /// Backend WS URL (e.g. wss://example.com/api/v1/workspaces/agent/ws).
    #[arg(long, env = "MCP_AGENT_URL")]
    url: Option<String>,

    /// Agent auth token.
    #[arg(long, env = "MCP_AGENT_TOKEN")]
    token: Option<String>,

    /// JSON config file with `{"url": ..., "token": ...}`. Used when
    /// --url / --token are not given.
    #[arg(long)]
    config: Option<PathBuf>,
}

#[derive(Debug, serde::Deserialize)]
struct FileConfig {
    url: String,
    token: String,
}

fn main() -> ExitCode {
    if let Err(e) = run() {
        eprintln!("agent: fatal: {e:#}");
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}

fn run() -> Result<()> {
    init_tracing();
    init_crypto_provider();

    let cli = Cli::parse();
    let (url, token) = resolve_config(&cli).context("resolve config")?;

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .thread_name("agent-rt")
        .build()
        .context("build tokio runtime")?;

    runtime.block_on(async_main(url, token))
}

async fn async_main(url: String, token: String) -> Result<()> {
    let client = client::Client::new(url, token);
    tokio::select! {
        res = client.run() => res,
        _ = shutdown_signal() => {
            info!("shutdown signal received; exiting");
            Ok(())
        }
    }
}

#[cfg(unix)]
async fn shutdown_signal() {
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigterm =
        signal(SignalKind::terminate()).expect("install SIGTERM handler");
    let mut sigint =
        signal(SignalKind::interrupt()).expect("install SIGINT handler");
    tokio::select! {
        _ = sigterm.recv() => {},
        _ = sigint.recv() => {},
    }
}

#[cfg(windows)]
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}

/// Install the `ring` rustls crypto provider exactly once at startup.
///
/// rustls 0.23 made the global crypto provider explicit — without a
/// pre-installed provider, the first TLS handshake panics with
/// `CryptoProvider::install_default() not called`. We pull `rustls` in
/// directly with the `ring` feature and install it here so any TLS
/// path (the WS connection, the self-update HTTP client in agent-rs/07)
/// just works.
fn init_crypto_provider() {
    use rustls::crypto::CryptoProvider;
    if CryptoProvider::get_default().is_none() {
        // `install_default` returns Err if another provider was set
        // concurrently — safe to ignore (we just lost the race and the
        // other provider is what we'd have used anyway).
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
}

fn init_tracing() {
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    if let Err(e) = tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_target(false)
        .try_init()
    {
        let _ = e;
        error!("tracing was already initialised; continuing");
    }
}

fn resolve_config(cli: &Cli) -> Result<(String, String)> {
    if let (Some(url), Some(token)) = (cli.url.clone(), cli.token.clone()) {
        return Ok((url, token));
    }
    if let Some(path) = &cli.config {
        let content = std::fs::read_to_string(path)
            .with_context(|| format!("read config file {}", path.display()))?;
        let cfg: FileConfig = serde_json::from_str(&content)
            .with_context(|| format!("parse config file {}", path.display()))?;
        return Ok((cfg.url, cfg.token));
    }
    anyhow::bail!("specify --url and --token (or --config <path>)")
}
