//! Bounded ring buffer for agent stdout/stderr with bounded fan-out
//! to live subscribers (v2 spec §7).
//!
//! - Capture: ``BufReader::lines`` over a piped ``ChildStdout`` /
//!   ``ChildStderr``. One spawned task per stream.
//! - Storage: ``parking_lot::Mutex<VecDeque<LogLine>>`` capped at
//!   ``capacity``; oldest line dropped on overflow (FIFO).
//! - Fan-out: each subscriber owns a bounded ``mpsc::Sender``; if
//!   ``try_send`` returns ``Full`` the subscriber is **disconnected**,
//!   never blocked. A slow consumer must not stall the agent.
//! - Token mask: ``(?:ta|sv)_[a-f0-9]{32,}`` is replaced with
//!   ``<token>`` *before* a line lands in the buffer or hits any
//!   subscriber.
//! - Truncation: a single line larger than ``max_line_bytes`` is
//!   truncated with ``truncated: true`` (it is **not** dropped).

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};

use chrono::Utc;
use parking_lot::Mutex;
use regex::Regex;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::sync::mpsc;
use tracing::{debug, warn};

use crate::protocol::{LogLine, LogStream};

fn token_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?:ta|sv)_[a-f0-9]{32,}").expect("token mask regex compiles")
    })
}

const TRUNCATION_MARKER: &str = "…[truncated]";

/// Receiver side handed to a caller (e.g. the WS push task in
/// ``backend.rs``). The caller drives ``rx`` however it wants; when
/// it is dropped the next ``push`` will see a ``Closed`` error and
/// retire the subscriber.
#[derive(Debug)]
pub struct SubscriberHandle {
    pub rx: mpsc::Receiver<LogLine>,
    pub id: u64,
}

#[derive(Debug)]
struct Subscriber {
    sender: mpsc::Sender<LogLine>,
    id: u64,
}

#[derive(Debug)]
pub struct LogRing {
    capacity: usize,
    max_line_bytes: usize,
    subscriber_capacity: usize,
    buf: Mutex<VecDeque<LogLine>>,
    subscribers: Mutex<Vec<Subscriber>>,
    next_subscriber_id: AtomicU64,
}

impl LogRing {
    pub fn new(
        capacity: usize,
        max_line_bytes: usize,
        subscriber_capacity: usize,
    ) -> Arc<Self> {
        debug_assert!(capacity > 0);
        debug_assert!(max_line_bytes >= 64);
        debug_assert!(subscriber_capacity > 0);
        Arc::new(Self {
            capacity,
            max_line_bytes,
            subscriber_capacity,
            buf: Mutex::new(VecDeque::with_capacity(capacity)),
            subscribers: Mutex::new(Vec::new()),
            next_subscriber_id: AtomicU64::new(1),
        })
    }

    /// Number of buffered lines. Test helper only.
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.buf.lock().len()
    }

    /// Snapshot the most recent ``tail`` lines (or all if ``tail`` is
    /// ``None``), oldest-first.
    pub fn snapshot(&self, tail: Option<usize>) -> Vec<LogLine> {
        let buf = self.buf.lock();
        let take = tail.unwrap_or(buf.len()).min(buf.len());
        let skip = buf.len() - take;
        buf.iter().skip(skip).cloned().collect()
    }

    pub fn subscribe(&self) -> SubscriberHandle {
        let (tx, rx) = mpsc::channel(self.subscriber_capacity);
        let id = self.next_subscriber_id.fetch_add(1, Ordering::Relaxed);
        self.subscribers.lock().push(Subscriber { sender: tx, id });
        SubscriberHandle { rx, id }
    }

    pub fn unsubscribe(&self, id: u64) {
        self.subscribers.lock().retain(|s| s.id != id);
    }

    /// Append a single captured line. Token-masks, truncates if needed,
    /// then enqueues + fans out.
    pub fn push(&self, stream: LogStream, raw: &str) {
        let masked = token_re().replace_all(raw, "<token>").into_owned();
        let (text, truncated) = truncate(masked, self.max_line_bytes);
        let line = LogLine {
            ts: Utc::now(),
            stream,
            text,
            truncated,
        };

        // Buffer first — never drop a captured line on the floor just
        // because subscribers are slow.
        {
            let mut buf = self.buf.lock();
            if buf.len() == self.capacity {
                buf.pop_front();
            }
            buf.push_back(line.clone());
        }

        // Fan out without blocking. Full / Closed both retire the
        // subscriber: the documented back-pressure policy is "kick
        // the slow consumer, keep the writer cheap".
        let mut subs = self.subscribers.lock();
        let mut drop_ids: Vec<u64> = Vec::new();
        for s in subs.iter() {
            match s.sender.try_send(line.clone()) {
                Ok(()) => {}
                Err(mpsc::error::TrySendError::Full(_)) => {
                    warn!(subscriber_id = s.id, "log subscriber full — disconnecting");
                    drop_ids.push(s.id);
                }
                Err(mpsc::error::TrySendError::Closed(_)) => {
                    debug!(subscriber_id = s.id, "log subscriber closed");
                    drop_ids.push(s.id);
                }
            }
        }
        if !drop_ids.is_empty() {
            subs.retain(|s| !drop_ids.contains(&s.id));
        }
    }
}

/// Truncate a String to ``max_bytes`` while staying on a UTF-8 char
/// boundary, then append a marker. Multi-byte codepoints are never
/// split.
fn truncate(mut s: String, max_bytes: usize) -> (String, bool) {
    if s.len() <= max_bytes {
        return (s, false);
    }
    let mut cut = max_bytes;
    while cut > 0 && !s.is_char_boundary(cut) {
        cut -= 1;
    }
    s.truncate(cut);
    s.push_str(TRUNCATION_MARKER);
    (s, true)
}

/// Spawn a task that reads ``reader`` line by line and pushes each
/// line into ``ring``. Returns the JoinHandle so the caller (the
/// AgentManager) can decide its lifetime.
pub fn spawn_capture<R>(
    reader: R,
    stream: LogStream,
    ring: Arc<LogRing>,
) -> tokio::task::JoinHandle<()>
where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut lines = BufReader::new(reader).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => ring.push(stream, &line),
                Ok(None) => break,
                Err(e) => {
                    warn!(error = %e, ?stream, "log capture read error; ending stream");
                    break;
                }
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_drops_oldest_on_overflow() {
        let ring = LogRing::new(3, 4096, 16);
        for i in 0..5 {
            ring.push(LogStream::Stdout, &format!("line {i}"));
        }
        let lines = ring.snapshot(None);
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0].text, "line 2");
        assert_eq!(lines[2].text, "line 4");
    }

    #[test]
    fn snapshot_tail_returns_last_n_oldest_first() {
        let ring = LogRing::new(10, 4096, 16);
        for i in 0..7 {
            ring.push(LogStream::Stdout, &format!("{i}"));
        }
        let texts: Vec<_> = ring
            .snapshot(Some(3))
            .into_iter()
            .map(|l| l.text)
            .collect();
        assert_eq!(texts, vec!["4", "5", "6"]);
    }

    #[test]
    fn token_mask_redacts_known_prefixes() {
        let ring = LogRing::new(2, 4096, 16);
        ring.push(
            LogStream::Stderr,
            "auth ok ta_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa now sv_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        );
        let l = &ring.snapshot(None)[0];
        assert_eq!(l.text, "auth ok <token> now <token>");
    }

    #[test]
    fn token_mask_leaves_short_pseudo_prefixes_alone() {
        let ring = LogRing::new(2, 4096, 16);
        ring.push(LogStream::Stdout, "ta_short and sv_alsoshort");
        // Less than 32 hex chars after the prefix → not a real token.
        let l = &ring.snapshot(None)[0];
        assert_eq!(l.text, "ta_short and sv_alsoshort");
    }

    #[test]
    fn truncation_marks_flag_and_keeps_line() {
        let ring = LogRing::new(2, 64, 16);
        let big = "x".repeat(200);
        ring.push(LogStream::Stdout, &big);
        let l = &ring.snapshot(None)[0];
        assert!(l.truncated);
        assert!(l.text.starts_with("xxxx"));
        assert!(l.text.ends_with(TRUNCATION_MARKER));
        // The marker is appended after the byte cut, so the total is
        // bounded by max_line_bytes + marker length.
        assert!(l.text.len() <= 64 + TRUNCATION_MARKER.len());
    }

    #[test]
    fn truncation_respects_utf8_boundary() {
        // 60 ASCII bytes + "あいうえお" (15 bytes, 3 bytes each).
        // max=64 lands mid-"い" (bytes 63..66); the truncate walker
        // must back up to byte 63 (start of "い") so the result is
        // still valid UTF-8.
        let ring = LogRing::new(2, 64, 16);
        let mut input = "a".repeat(60);
        input.push_str("あいうえお");
        ring.push(LogStream::Stdout, &input);
        let l = &ring.snapshot(None)[0];
        assert!(l.truncated);
        // The body before the marker must be valid UTF-8 ending on a
        // char boundary (the test would have panicked above on
        // ``String::truncate`` if it did not).
        let body = l.text.trim_end_matches(TRUNCATION_MARKER);
        assert!(body.is_char_boundary(body.len()));
        assert_eq!(body.chars().filter(|c| *c == 'a').count(), 60);
        // "あ" must survive entirely — it sits at bytes 60..63.
        assert!(body.ends_with("あ"));
    }

    #[tokio::test]
    async fn full_subscriber_is_disconnected() {
        let ring = LogRing::new(100, 4096, 1);
        let _sub = ring.subscribe();
        // Don't drain rx — capacity 1 fills on the second push, the
        // third push observes Full and kicks the subscriber.
        ring.push(LogStream::Stdout, "a");
        ring.push(LogStream::Stdout, "b");
        ring.push(LogStream::Stdout, "c");
        assert!(ring.subscribers.lock().is_empty());
    }

    #[tokio::test]
    async fn closed_subscriber_is_collected_on_next_push() {
        let ring = LogRing::new(100, 4096, 4);
        let sub = ring.subscribe();
        drop(sub.rx); // close from the consumer side
        ring.push(LogStream::Stdout, "trigger");
        assert!(ring.subscribers.lock().is_empty());
    }

    #[tokio::test]
    async fn subscriber_receives_pushed_lines() {
        let ring = LogRing::new(100, 4096, 4);
        let mut sub = ring.subscribe();
        ring.push(LogStream::Stdout, "hello");
        ring.push(LogStream::Stderr, "world");
        let l1 = sub.rx.recv().await.expect("got line 1");
        let l2 = sub.rx.recv().await.expect("got line 2");
        assert_eq!(l1.text, "hello");
        assert_eq!(l1.stream, LogStream::Stdout);
        assert_eq!(l2.text, "world");
        assert_eq!(l2.stream, LogStream::Stderr);
    }

    #[tokio::test]
    async fn capture_task_reads_until_eof() {
        let ring = LogRing::new(10, 4096, 16);
        let body = b"first\nsecond\nthird\n".to_vec();
        let cursor = std::io::Cursor::new(body);
        let h = spawn_capture(cursor, LogStream::Stdout, ring.clone());
        h.await.unwrap();
        let texts: Vec<_> = ring.snapshot(None).into_iter().map(|l| l.text).collect();
        assert_eq!(texts, vec!["first", "second", "third"]);
    }
}
