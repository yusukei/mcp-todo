//! Windows-specific subprocess primitives: Job Object lifecycle and
//! ``CTRL_BREAK_EVENT`` delivery.
//!
//! The Job Object is created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
//! so that **dropping** the supervisor process — for any reason —
//! also kills the agent and any grandchildren it spawned. This is the
//! mitigation for v2 spec risk R11 (orphan grandchildren).

use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};

use anyhow::{anyhow, Context, Result};
use tokio::process::Child;
use windows::Win32::Foundation::HANDLE;
use windows::Win32::System::Console::{GenerateConsoleCtrlEvent, CTRL_BREAK_EVENT};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_BASIC_LIMIT_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

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
