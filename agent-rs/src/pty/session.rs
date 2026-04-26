//! Single PTY session: spawn shell + interior reader/writer/resize/kill.
//!
//! Wraps `portable_pty::PtyPair` so the rest of the codebase doesn't
//! need to know whether we're on Unix posix_openpt or Windows ConPTY.
//! Reads happen on a blocking thread (portable-pty's `Read` impl is
//! synchronous); the manager pulls chunks off via [`PtySession::read`].

use std::io::{Read, Write};
use std::sync::Mutex;

use anyhow::{anyhow, Context, Result};
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};

pub struct PtySession {
    /// Master PTY — used for resize and as the source of `read`/`write`.
    master: Mutex<Box<dyn MasterPty + Send>>,
    /// Buffered writer over the PTY master. Held in a Mutex so multiple
    /// `terminal_input` calls serialise.
    writer: Mutex<Box<dyn Write + Send>>,
    /// Reader: take()'d into the reader task, then we set it back to None.
    reader: Mutex<Option<Box<dyn Read + Send>>>,
    /// Child shell process — used for `kill()` on `terminal_kill`.
    child: Mutex<Box<dyn Child + Send + Sync>>,
}

impl std::fmt::Debug for PtySession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PtySession").finish_non_exhaustive()
    }
}

pub fn spawn_session(
    shell: &str,
    cols: u16,
    rows: u16,
    cwd: Option<&str>,
) -> Result<PtySession> {
    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .context("openpty failed")?;

    let mut cmd = CommandBuilder::new(shell);
    cmd.env("TERM", "xterm-256color");
    cmd.env("COLORTERM", "truecolor");
    if let Some(d) = cwd {
        cmd.cwd(d);
    }

    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| anyhow!("spawn shell failed: {e}"))?;
    // The slave handle is no longer needed after spawn — drop it so
    // the child doesn't keep an extra fd open and EOF on master read
    // happens when the shell exits.
    drop(pair.slave);

    let writer = pair
        .master
        .take_writer()
        .map_err(|e| anyhow!("take_writer failed: {e}"))?;
    let reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| anyhow!("try_clone_reader failed: {e}"))?;

    Ok(PtySession {
        master: Mutex::new(pair.master),
        writer: Mutex::new(writer),
        reader: Mutex::new(Some(reader)),
        child: Mutex::new(child),
    })
}

impl PtySession {
    /// Read up to `buf.len()` bytes from the PTY. Blocks. Returns 0 on
    /// EOF (shell exited).
    pub fn read(&self, buf: &mut [u8]) -> std::io::Result<usize> {
        // We take() the reader on first call so subsequent calls don't
        // need a global lock. The reader handle is owned by the reader
        // task; the manager only ever calls this once per session.
        let mut guard = self.reader.lock().unwrap();
        let reader = guard
            .as_mut()
            .ok_or_else(|| std::io::Error::other("reader already moved out"))?;
        reader.read(buf)
    }

    pub fn write(&self, data: &[u8]) -> std::io::Result<()> {
        let mut w = self.writer.lock().unwrap();
        w.write_all(data)?;
        w.flush()?;
        Ok(())
    }

    pub fn resize(&self, cols: u16, rows: u16) -> Result<()> {
        let m = self.master.lock().unwrap();
        m.resize(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| anyhow!("resize failed: {e}"))
    }

    pub fn kill(&self) {
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// Spawn the platform default shell, write `echo hello\n`, and
    /// confirm we read "hello" back. Skipped if the shell isn't
    /// available on the test host.
    #[test]
    fn spawn_and_echo_hello() {
        let shell = if cfg!(windows) {
            std::env::var("COMSPEC").unwrap_or_else(|_| r"C:\Windows\system32\cmd.exe".into())
        } else if std::path::Path::new("/bin/sh").exists() {
            "/bin/sh".into()
        } else {
            return;
        };
        let session = match spawn_session(&shell, 80, 24, None) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("PTY spawn unavailable in test env: {e}");
                return;
            }
        };
        // Write a self-terminating command so the shell exits on its
        // own — simpler than coordinating a kill across threads.
        let cmd = if cfg!(windows) {
            b"@echo hello\r\nexit 0\r\n".as_slice()
        } else {
            b"echo hello\nexit 0\n".as_slice()
        };
        session.write(cmd).unwrap();

        // Drain output up to a deadline.
        let mut all = Vec::new();
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        let mut buf = vec![0u8; 4096];
        while std::time::Instant::now() < deadline {
            match session.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => all.extend_from_slice(&buf[..n]),
                Err(_) => break,
            }
            if all.windows(5).any(|w| w == b"hello") {
                // Found it; let the shell finish naturally.
                std::thread::sleep(Duration::from_millis(100));
                break;
            }
        }
        assert!(
            all.windows(5).any(|w| w == b"hello"),
            "expected 'hello' in output: {:?}",
            String::from_utf8_lossy(&all)
        );
    }

    #[test]
    fn spawn_with_bad_shell_errors() {
        let r = spawn_session("/this/does/not/exist/totally", 80, 24, None);
        assert!(r.is_err(), "expected spawn failure, got Ok");
    }
}
