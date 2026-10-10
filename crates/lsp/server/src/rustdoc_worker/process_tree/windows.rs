use std::{
    io,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    time::Duration,
};

use anyhow::Context as _;
use tokio::process::{Child, Command};
use windows_sys::Win32::{
    Foundation::INVALID_HANDLE_VALUE,
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
            QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
        },
        Threading::{CREATE_SUSPENDED, OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
    },
};

/// A kernel job owns descendants, including children that outlive the direct Cargo process.
#[derive(Debug)]
pub(crate) struct OwnedProcessTree {
    job: OwnedHandle,
}

impl OwnedProcessTree {
    pub(crate) fn spawn(command: &mut Command) -> anyhow::Result<(Self, Child)> {
        // SAFETY: null attributes request an unnamed, non-inheritable job owned by this process.
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        anyhow::ensure!(
            !handle.is_null(),
            "create compiler job: {}",
            io::Error::last_os_error()
        );
        // SAFETY: CreateJobObjectW returned a fresh owning handle.
        let tree = Self {
            job: unsafe { OwnedHandle::from_raw_handle(handle) },
        };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the pointer and length describe the matching limit-information structure.
        anyhow::ensure!(
            unsafe {
                SetInformationJobObject(
                    tree.job.as_raw_handle(),
                    JobObjectExtendedLimitInformation,
                    (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    size_of_val(&limits) as u32,
                )
            } != 0,
            "configure compiler job: {}",
            io::Error::last_os_error()
        );
        // Start suspended so no compiler child can escape ownership before job assignment.
        command.creation_flags(CREATE_SUSPENDED).kill_on_drop(true);
        let child = command
            .spawn()
            .context("spawn suspended compiler command")?;
        let process = child
            .raw_handle()
            .context("compiler command has no process handle")?;
        // SAFETY: both handles are live; the child has not started executing.
        anyhow::ensure!(
            unsafe { AssignProcessToJobObject(tree.job.as_raw_handle(), process) } != 0,
            "assign compiler process to its job: {}",
            io::Error::last_os_error()
        );
        Self::resume(&child)?;
        Ok((tree, child))
    }

    fn resume(child: &Child) -> anyhow::Result<()> {
        let process = child
            .id()
            .context("suspended compiler has no process identity")?;
        // SAFETY: this returns an owning snapshot handle and does not mutate any process.
        let handle = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        anyhow::ensure!(
            handle != INVALID_HANDLE_VALUE,
            "enumerate suspended compiler thread: {}",
            io::Error::last_os_error()
        );
        // SAFETY: the snapshot is a fresh owning handle.
        let snapshot = unsafe { OwnedHandle::from_raw_handle(handle) };
        let mut entry = THREADENTRY32 {
            dwSize: size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        // SAFETY: the snapshot is live and entry carries the required structure size.
        let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } != 0;
        while found {
            if entry.th32OwnerProcessID == process {
                // SAFETY: only the newly suspended child's thread is opened with resume rights.
                let handle = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                anyhow::ensure!(
                    !handle.is_null(),
                    "open compiler thread: {}",
                    io::Error::last_os_error()
                );
                // SAFETY: OpenThread returned an owning handle.
                let thread = unsafe { OwnedHandle::from_raw_handle(handle) };
                // SAFETY: this is the thread of our newly created and already assigned process.
                anyhow::ensure!(
                    unsafe { ResumeThread(thread.as_raw_handle()) } != u32::MAX,
                    "resume compiler thread: {}",
                    io::Error::last_os_error()
                );
                return Ok(());
            }
            // SAFETY: the live snapshot and correctly sized entry are reused for enumeration.
            found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } != 0;
        }
        anyhow::bail!("suspended compiler primary thread was not found")
    }

    pub(crate) async fn drain(&mut self, child: &mut Child) -> anyhow::Result<()> {
        // SAFETY: only the worker's owned job is terminated, including its descendants.
        anyhow::ensure!(
            unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } != 0,
            "terminate compiler job: {}",
            io::Error::last_os_error()
        );
        tokio::time::timeout(Duration::from_secs(5), child.wait())
            .await
            .context("timeout reaping compiler command")?
            .context("reap compiler command")?;
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        loop {
            let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
            // SAFETY: the buffer and size match the requested information class.
            anyhow::ensure!(
                unsafe {
                    QueryInformationJobObject(
                        self.job.as_raw_handle(),
                        JobObjectBasicAccountingInformation,
                        (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                        size_of_val(&accounting) as u32,
                        std::ptr::null_mut(),
                    )
                } != 0,
                "observe compiler cleanup: {}",
                io::Error::last_os_error()
            );
            if accounting.ActiveProcesses == 0 {
                return Ok(());
            }
            anyhow::ensure!(
                tokio::time::Instant::now() < deadline,
                "compiler descendants did not drain before cleanup deadline"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }
}
