//! Agent subprocess management with Windows Job Object isolation
//! (v2 spec §6.2 / §6.3).
//!
//! - Spawn via ``tokio::process::Command``, which delegates to
//!   ``CreateProcessW`` on Windows. No ``cmd /c`` indirection.
//! - On Windows the child is bound to a Job Object so dropping the
//!   supervisor (or calling ``TerminateJobObject``) kills the agent
//!   plus every grandchild.
//! - Graceful kill is a 4-stage sequence:
//!   1. WS-level shutdown RPC via the caller-supplied ``ShutdownHook``
//!      (default no-op; Day 3 wires the real WS client).
//!   2. Wait up to ``graceful_timeout`` for natural exit.
//!   3. Best-effort ``CTRL_BREAK_EVENT`` (Windows only).
//!   4. ``TerminateJobObject`` — the guaranteed step.
//! - Restart loop with exponential backoff (``initial`` → ``max``)
//!   and ±N% jitter; ``consecutive_crashes`` is exposed via the
//!   status snapshot.
//!
//! End-to-end tests with a long-running dummy agent live separately;
//! the unit tests below cover only the parts that don't need a real
//! subprocess to behave deterministically.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use parking_lot::Mutex;
use rand::Rng;
use tokio::process::{Child, Command};
use tokio::sync::Notify;
use tokio::time::{sleep, timeout};
use tracing::{info, warn};

use crate::config::{AgentConfig, AgentMode, RestartConfig};
use crate::log_capture::{spawn_capture, LogRing};
use crate::protocol::{AgentState, LogStream};

#[cfg(windows)]
use crate::platform_windows::{send_ctrl_break, JobHandle};
#[cfg(not(windows))]
use crate::platform_posix::JobHandle;

/// Concrete command line, decoupled from ``AgentConfig`` so tests can
/// inject a dummy executable without going through ``uv-run``.
#[derive(Debug, Clone)]
pub struct AgentCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: HashMap<String, String>,
}

impl AgentCommand {
    pub fn from_config(cfg: &AgentConfig) -> Self {
        let mut env = HashMap::new();
        // Force UTF-8 stdio on Windows so log capture sees clean text
        // (the UM790Pro host's default code page was CP932 before
        // these were injected — ad-hoc mojibake on every line).
        env.insert("PYTHONIOENCODING".into(), "utf-8".into());
        env.insert("PYTHONUTF8".into(), "1".into());
        match cfg.mode {
            AgentMode::UvRun => Self {
                program: PathBuf::from(if cfg!(windows) { "uv.exe" } else { "uv" }),
                args: vec![
                    "run".into(),
                    "python".into(),
                    "main.py".into(),
                    "--url".into(),
                    cfg.url.clone(),
                    "--token".into(),
                    cfg.token.clone(),
                ],
                cwd: cfg.cwd.clone(),
                env,
            },
        }
    }
}

#[derive(Debug, Default, Clone)]
pub struct StatusSnapshot {
    pub state: AgentState,
    pub pid: Option<u32>,
    pub started_at: Option<DateTime<Utc>>,
    pub last_crash_at: Option<DateTime<Utc>>,
    pub last_crash_exit_code: Option<i32>,
    pub consecutive_crashes: u32,
}

#[derive(Debug, Default)]
struct StatusCell {
    snapshot: Mutex<StatusSnapshot>,
}

impl StatusCell {
    fn read(&self) -> StatusSnapshot {
        self.snapshot.lock().clone()
    }
    fn mutate<F: FnOnce(&mut StatusSnapshot)>(&self, f: F) {
        f(&mut self.snapshot.lock());
    }
}

/// Day-2 seam for stage 1 of graceful_stop. Day 3 will provide a
/// WS-backed implementation that sends a ``shutdown`` RPC to the agent.
pub trait ShutdownHook: Send + Sync + 'static {
    fn request_shutdown<'a>(
        &'a self,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + 'a>>;
}

/// No-op default — used by tests and by Day 2's standalone runtime.
#[derive(Debug, Default)]
pub struct NoShutdownHook;

impl ShutdownHook for NoShutdownHook {
    fn request_shutdown<'a>(
        &'a self,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + 'a>> {
        Box::pin(async {})
    }
}

pub struct AgentManager {
    cmd: AgentCommand,
    restart: RestartConfig,
    ring: Arc<LogRing>,
    status: Arc<StatusCell>,
    job: Arc<JobHandle>,
    shutdown_hook: Arc<dyn ShutdownHook>,
    stop_signal: Arc<Notify>,
    stopping: Arc<Mutex<bool>>,
}

impl AgentManager {
    pub fn new(
        cmd: AgentCommand,
        restart: RestartConfig,
        ring: Arc<LogRing>,
        shutdown_hook: Arc<dyn ShutdownHook>,
    ) -> Result<Self> {
        Ok(Self {
            cmd,
            restart,
            ring,
            status: Arc::new(StatusCell::default()),
            job: Arc::new(JobHandle::new()?),
            shutdown_hook,
            stop_signal: Arc::new(Notify::new()),
            stopping: Arc::new(Mutex::new(false)),
        })
    }

    pub fn status(&self) -> StatusSnapshot {
        self.status.read()
    }

    pub fn ring(&self) -> Arc<LogRing> {
        self.ring.clone()
    }

    /// Spawn one agent instance (no restart). The returned ``Child``
    /// has stdout/stderr already attached to the log ring; callers
    /// only need to ``.wait()`` on it.
    pub fn spawn_once(&self) -> Result<Child> {
        let mut command = Command::new(&self.cmd.program);
        command
            .args(&self.cmd.args)
            .current_dir(&self.cmd.cwd)
            .envs(&self.cmd.env)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null())
            .kill_on_drop(true);

        // On Windows, putting the child in its own process group lets
        // ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)`` target
        // only the agent without also signalling the supervisor.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            command.creation_flags(CREATE_NEW_PROCESS_GROUP);
        }

        self.status.mutate(|s| {
            s.state = AgentState::Starting;
        });

        let mut child = command
            .spawn()
            .with_context(|| format!("failed to spawn agent: {}", self.cmd.program.display()))?;
        let pid = child.id();

        // Bind to the Job Object. If the child has already exited
        // (very short-lived test commands can race) the assign will
        // fail; log and keep going — ``kill_on_drop`` is the fallback.
        if let Err(e) = self.job.assign(&mut child) {
            warn!(pid, error = %e, "AssignProcessToJobObject failed; child not job-bound");
        }

        if let Some(stdout) = child.stdout.take() {
            spawn_capture(stdout, LogStream::Stdout, self.ring.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            spawn_capture(stderr, LogStream::Stderr, self.ring.clone());
        }

        self.status.mutate(|s| {
            s.state = AgentState::Running;
            s.pid = pid;
            s.started_at = Some(Utc::now());
        });
        info!(pid, "agent spawned");
        Ok(child)
    }

    /// Run forever: spawn → wait → backoff → respawn. Returns when
    /// ``stop()`` has been observed.
    pub async fn run_supervised(self: Arc<Self>) -> Result<()> {
        let mut backoff_ms = self.restart.backoff_initial_ms;
        loop {
            if *self.stopping.lock() {
                break;
            }
            let mut child = match self.spawn_once() {
                Ok(c) => c,
                Err(e) => {
                    warn!(error = %e, "spawn failed; backing off");
                    self.bump_crash(None);
                    self.sleep_backoff(&mut backoff_ms).await;
                    continue;
                }
            };

            tokio::select! {
                exit = child.wait() => {
                    let code = exit.ok().and_then(|s| s.code());
                    self.bump_crash(code);
                    if *self.stopping.lock() {
                        break;
                    }
                    warn!(?code, backoff_ms, "agent exited; restarting after backoff");
                    self.sleep_backoff(&mut backoff_ms).await;
                }
                _ = self.stop_signal.notified() => {
                    self.graceful_kill_inner(&mut child).await;
                    break;
                }
            }
        }
        self.status.mutate(|s| s.state = AgentState::Stopped);
        Ok(())
    }

    fn bump_crash(&self, code: Option<i32>) {
        self.status.mutate(|s| {
            s.state = AgentState::Crashed;
            s.last_crash_at = Some(Utc::now());
            s.last_crash_exit_code = code;
            s.consecutive_crashes = s.consecutive_crashes.saturating_add(1);
            s.pid = None;
            s.started_at = None;
        });
    }

    async fn sleep_backoff(&self, current_ms: &mut u64) {
        let jitter_pct = self.restart.backoff_jitter_pct as f64 / 100.0;
        let jitter = if jitter_pct > 0.0 {
            let factor = rand::thread_rng().gen_range(-jitter_pct..=jitter_pct);
            (*current_ms as f64 * factor) as i64
        } else {
            0
        };
        let wait = (*current_ms as i64 + jitter).max(0) as u64;
        sleep(Duration::from_millis(wait)).await;
        *current_ms = current_ms.saturating_mul(2).min(self.restart.backoff_max_ms);
    }

    /// Ask ``run_supervised`` to stop. Idempotent.
    pub fn stop(&self) {
        *self.stopping.lock() = true;
        self.stop_signal.notify_waiters();
    }

    /// 4-stage graceful kill of the currently-running child.
    async fn graceful_kill_inner(&self, child: &mut Child) {
        self.status.mutate(|s| s.state = AgentState::Stopping);
        let pid = child.id();
        let timeout_ms = self.restart.graceful_timeout_ms;

        // Stage 1: WS-level shutdown RPC (best-effort; no-op in Day 2).
        self.shutdown_hook.request_shutdown().await;

        // Stage 2: wait for natural exit.
        if let Ok(Ok(_)) = timeout(Duration::from_millis(timeout_ms), child.wait()).await {
            info!(pid, "agent exited gracefully");
            return;
        }

        // Stage 3: CTRL_BREAK_EVENT on Windows (best-effort).
        #[cfg(windows)]
        {
            if let Some(p) = pid {
                if let Err(e) = send_ctrl_break(p) {
                    warn!(error = %e, "CTRL_BREAK_EVENT failed");
                }
                if let Ok(Ok(_)) =
                    timeout(Duration::from_millis(2_000), child.wait()).await
                {
                    info!(pid, "agent exited after CTRL_BREAK_EVENT");
                    return;
                }
            }
        }

        // Stage 4: TerminateJobObject — the guaranteed step.
        if let Err(e) = self.job.terminate() {
            warn!(error = %e, "TerminateJobObject failed; falling back to child.kill()");
            let _ = child.start_kill();
        }
        let _ = child.wait().await;
        warn!(?pid, "agent terminated via job object");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn restart_cfg() -> RestartConfig {
        RestartConfig {
            backoff_initial_ms: 10,
            backoff_max_ms: 40,
            backoff_jitter_pct: 0,
            graceful_timeout_ms: 100,
        }
    }

    /// Short-lived dummy command that exits ~immediately on both
    /// Windows and POSIX. The race against ``AssignProcessToJobObject``
    /// is handled by ``spawn_once`` (logged, non-fatal).
    fn dummy_cmd() -> AgentCommand {
        let (program, args): (PathBuf, Vec<String>) = if cfg!(windows) {
            (
                PathBuf::from("cmd.exe"),
                vec!["/c".into(), "echo".into(), "hello".into()],
            )
        } else {
            (
                PathBuf::from("/bin/sh"),
                vec!["-c".into(), "echo hello".into()],
            )
        };
        AgentCommand {
            program,
            args,
            cwd: std::env::current_dir().unwrap(),
            env: HashMap::new(),
        }
    }

    #[test]
    fn from_config_uvrun_carries_token_and_url_and_utf8_env() {
        let cfg = AgentConfig {
            mode: AgentMode::UvRun,
            cwd: std::env::current_dir().unwrap(),
            url: "wss://example/agent/ws".into(),
            token: "ta_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz".into(),
        };
        let cmd = AgentCommand::from_config(&cfg);
        assert!(cmd.args.iter().any(|a| a == "ta_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"));
        assert!(cmd.args.iter().any(|a| a == "wss://example/agent/ws"));
        assert_eq!(
            cmd.env.get("PYTHONIOENCODING").map(String::as_str),
            Some("utf-8")
        );
        assert_eq!(cmd.env.get("PYTHONUTF8").map(String::as_str), Some("1"));
        // First three positional args are always run / python / main.py.
        assert_eq!(&cmd.args[0..3], &["run", "python", "main.py"]);
    }

    #[tokio::test]
    async fn spawn_once_marks_state_running() {
        let ring = LogRing::new(10, 4096, 16);
        let mgr = AgentManager::new(
            dummy_cmd(),
            restart_cfg(),
            ring,
            Arc::new(NoShutdownHook),
        )
        .unwrap();
        let mut child = mgr.spawn_once().expect("spawn");
        let _ = child.wait().await;
        let snap = mgr.status();
        // ``spawn_once`` itself only advances to Running. Crash
        // bookkeeping happens in ``run_supervised``.
        assert_eq!(snap.state, AgentState::Running);
        assert!(snap.pid.is_some() || snap.consecutive_crashes == 0);
        assert!(snap.started_at.is_some());
    }

    #[tokio::test]
    async fn run_supervised_exits_cleanly_on_stop() {
        let ring = LogRing::new(10, 4096, 16);
        let mgr = Arc::new(
            AgentManager::new(
                dummy_cmd(),
                restart_cfg(),
                ring,
                Arc::new(NoShutdownHook),
            )
            .unwrap(),
        );
        let h = tokio::spawn({
            let mgr = mgr.clone();
            async move { mgr.run_supervised().await }
        });
        // Let the dummy crash-restart at least once, then stop.
        tokio::time::sleep(Duration::from_millis(120)).await;
        mgr.stop();
        let join_res = tokio::time::timeout(Duration::from_secs(3), h)
            .await
            .expect("run_supervised exited within 3s");
        join_res.expect("task joined").expect("Ok(())");
        let snap = mgr.status();
        assert_eq!(snap.state, AgentState::Stopped);
        assert!(
            snap.consecutive_crashes >= 1,
            "expected at least one crash bookkeeping update, got {}",
            snap.consecutive_crashes
        );
    }

    #[tokio::test]
    async fn sleep_backoff_doubles_until_max() {
        let ring = LogRing::new(10, 4096, 16);
        let mgr = AgentManager::new(
            dummy_cmd(),
            RestartConfig {
                backoff_initial_ms: 1,
                backoff_max_ms: 8,
                backoff_jitter_pct: 0,
                graceful_timeout_ms: 100,
            },
            ring,
            Arc::new(NoShutdownHook),
        )
        .unwrap();
        let mut current = 1u64;
        for expected in [1u64, 2, 4, 8, 8, 8] {
            assert_eq!(current, expected);
            mgr.sleep_backoff(&mut current).await;
        }
    }
}
