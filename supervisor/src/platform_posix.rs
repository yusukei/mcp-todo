//! POSIX stubs so the crate compiles outside Windows.
//!
//! Phase Y only ships on Windows. These stubs exist purely so
//! ``cargo check`` on Linux/macOS passes for IDEs / contributors who
//! aren't on the deployment OS. Behaviourally they do the minimum
//! safe thing: nothing.

use anyhow::Result;
use tokio::process::Child;

pub struct JobHandle;

impl std::fmt::Debug for JobHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JobHandle").finish_non_exhaustive()
    }
}

impl JobHandle {
    pub fn new() -> Result<Self> {
        Ok(Self)
    }

    pub fn assign(&self, _child: &mut Child) -> Result<()> {
        Ok(())
    }

    pub fn terminate(&self) -> Result<()> {
        Ok(())
    }
}
