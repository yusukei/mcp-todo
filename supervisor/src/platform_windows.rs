//! Windows-specific subprocess primitives: Job Object lifecycle and
//! ``CTRL_BREAK_EVENT`` delivery.
//!
//! The Job Object is created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
//! so that **dropping** the supervisor process — for any reason —
//! also kills the agent and any grandchildren it spawned. This is the
//! mitigation for v2 spec risk R11 (orphan grandchildren).

use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};

use anyhow::{anyhow, bail, Context, Result};
use tokio::process::Child;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::Console::{GenerateConsoleCtrlEvent, CTRL_BREAK_EVENT};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_BASIC_LIMIT_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

/// ``CreateProcess`` flag — start the primary thread suspended so the
/// caller can bind the process to a Job Object before any code runs.
/// Resume via :fn:`resume_main_thread`.
pub const CREATE_SUSPENDED: u32 = 0x0000_0004;
/// ``CreateProcess`` flag — give the child its own process group so
/// ``GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)`` only signals
/// the agent.
pub const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;

/// Owns the Job Object handle. Dropping it closes the handle, which —
/// thanks to ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` — also terminates
/// every process still bound to the job.
pub struct JobHandle {
    handle: OwnedHandle,
}

impl std::fmt::Debug for JobHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JobHandle").finish_non_exhaustive()
    }
}

fn raw_to_handle(p: RawHandle) -> HANDLE {
    HANDLE(p as _)
}

impl JobHandle {
    pub fn new() -> Result<Self> {
        // SAFETY: ``CreateJobObjectW`` with both args ``None`` requests
        // an anonymous, default-ACL job object; this is well-defined.
        let raw = unsafe { CreateJobObjectW(None, None) }.context("CreateJobObjectW failed")?;
        if raw.is_invalid() {
            return Err(anyhow!("CreateJobObjectW returned an invalid handle"));
        }

        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation = JOBOBJECT_BASIC_LIMIT_INFORMATION {
            LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            ..Default::default()
        };
        let info_size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
        // SAFETY: ``info`` is a properly initialised value of the
        // documented type for ``JobObjectExtendedLimitInformation``.
        unsafe {
            SetInformationJobObject(
                raw,
                JobObjectExtendedLimitInformation,
                &info as *const _ as _,
                info_size,
            )
        }
        .context("SetInformationJobObject(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)")?;

        // Transfer ownership of the Win32 HANDLE to OwnedHandle so
        // Drop calls CloseHandle. The Win32 HANDLE field is either
        // ``isize`` or ``*mut c_void`` depending on windows-rs
        // version; ``as _`` lets the compiler infer.
        // SAFETY: ``raw`` is a fresh, valid HANDLE we own exclusively.
        let owned = unsafe { OwnedHandle::from_raw_handle(raw.0 as _) };
        Ok(Self { handle: owned })
    }

    pub fn assign(&self, child: &mut Child) -> Result<()> {
        let raw = child
            .raw_handle()
            .ok_or_else(|| anyhow!("child has no raw handle (already exited?)"))?;
        let process = raw_to_handle(raw);
        let job = raw_to_handle(self.handle.as_raw_handle());
        // SAFETY: both handles are valid and owned by us.
        unsafe { AssignProcessToJobObject(job, process) }
            .context("AssignProcessToJobObject failed")?;
        Ok(())
    }

    pub fn terminate(&self) -> Result<()> {
        let job = raw_to_handle(self.handle.as_raw_handle());
        // Exit code 1 — supervisor-initiated termination, mirrors the
        // convention used by ``taskkill /F``.
        // SAFETY: ``job`` is a valid handle owned by us.
        unsafe { TerminateJobObject(job, 1) }.context("TerminateJobObject failed")?;
        Ok(())
    }
}

/// Best-effort delivery of ``CTRL_BREAK_EVENT`` to a process group.
///
/// The agent is spawned with ``CREATE_NEW_PROCESS_GROUP``, so passing
/// the agent's pid here as ``dwProcessGroupId`` only signals the agent
/// (and its children) — never the supervisor itself.
pub fn send_ctrl_break(pid: u32) -> Result<()> {
    // SAFETY: GenerateConsoleCtrlEvent is callable from any thread;
    // the kernel handles delivery + permission checking.
    unsafe { GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) }
        .context("GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT) failed")?;
    Ok(())
}

/// Resume the primary thread of a process spawned with
/// ``CREATE_SUSPENDED``.
///
/// This closes the well-known Job Object race: ``CreateProcess`` +
/// later ``AssignProcessToJobObject`` leaves a window during which the
/// new process can spawn grandchildren that escape the job. With
/// ``CREATE_SUSPENDED`` the child does nothing until we explicitly
/// resume it — and by that point it is already bound to the job, so
/// every descendant inherits the binding (mitigates spec risk R11
/// and the zombie-python.exe defect found during Phase Y acceptance).
///
/// We don't have direct access to the child's primary thread handle
/// from ``tokio::process::Child``, so we walk the system thread list
/// via ``CreateToolhelp32Snapshot`` and resume the lowest-tid thread
/// that belongs to ``pid``. The lowest-tid heuristic picks the
/// primary thread because it is allocated first by the OS at process
/// creation; secondary threads (if any) have higher TIDs.
pub fn resume_main_thread(pid: u32) -> Result<()> {
    // SAFETY: CreateToolhelp32Snapshot is documented to return an
    // owned snapshot handle; we close it via OwnedHandle below.
    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) }
        .context("CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD)")?;
    if snap.is_invalid() {
        bail!("CreateToolhelp32Snapshot returned an invalid handle");
    }
    // Wrap so the snapshot handle is closed even on early return.
    let _snap_guard =
        unsafe { OwnedHandle::from_raw_handle(snap.0 as RawHandle) };

    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };

    // Walk every thread; pick the one belonging to pid with the
    // smallest tid. Doing it this way (rather than "first match")
    // is robust to enumeration order changing between Windows
    // versions.
    unsafe { Thread32First(snap, &mut entry) }
        .context("Thread32First")?;
    let mut best_tid: Option<u32> = None;
    loop {
        if entry.th32OwnerProcessID == pid {
            best_tid = Some(match best_tid {
                None => entry.th32ThreadID,
                Some(prev) => prev.min(entry.th32ThreadID),
            });
        }
        if unsafe { Thread32Next(snap, &mut entry) }.is_err() {
            break;
        }
    }
    let tid = best_tid
        .ok_or_else(|| anyhow!("no thread found for pid {pid}"))?;

    // SAFETY: OpenThread returns a valid handle on success; we close
    // it explicitly below.
    let h = unsafe { OpenThread(THREAD_SUSPEND_RESUME, false, tid) }
        .with_context(|| format!("OpenThread(tid={tid})"))?;
    if h.is_invalid() {
        bail!("OpenThread returned an invalid handle for tid {tid}");
    }
    // SAFETY: ``h`` is valid; ResumeThread is documented to return
    // the previous suspend count, or u32::MAX on failure.
    let prev = unsafe { ResumeThread(h) };
    let close_result = unsafe { CloseHandle(h) };
    if let Err(e) = close_result {
        // Non-fatal — the thread was resumed regardless. Surface so a
        // handle leak shows up in ops.
        tracing::warn!(error = %e, "CloseHandle on resumed thread failed");
    }
    if prev == u32::MAX {
        bail!("ResumeThread failed (suspend count was -1) for tid {tid}");
    }
    Ok(())
}
