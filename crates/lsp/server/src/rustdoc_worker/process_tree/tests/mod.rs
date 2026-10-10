use std::{io, time::Duration};

use tokio::process::Command;

use super::OwnedProcessTree;

#[tokio::test]
async fn cleanup_reaps_a_leader_that_exited_before_drain() {
    let mut command = Command::new("python3");
    command.args(["-c", "pass"]);
    let (mut tree, mut child) = OwnedProcessTree::spawn(&mut command).unwrap();
    wait_for_unreaped_exit(tree.group).await;

    tree.drain(&mut child).await.unwrap();
    assert!(child.try_wait().unwrap().unwrap().success());
    assert_group_drained(&tree);
}

#[tokio::test]
async fn cleanup_reaps_a_term_cooperative_leader_during_grace() {
    let fixture = tempfile::tempdir().unwrap();
    let ready = fixture.path().join("ready");
    let mut command = Command::new("python3");
    command
        .args([
            "-c",
            "import pathlib,signal,sys,time; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); pathlib.Path(sys.argv[1]).touch(); time.sleep(30)",
        ])
        .arg(&ready);
    let (mut tree, mut child) = OwnedProcessTree::spawn(&mut command).unwrap();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    while !ready.exists() {
        assert!(
            tokio::time::Instant::now() < deadline,
            "leader did not start"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    }

    tree.drain(&mut child).await.unwrap();
    assert!(child.try_wait().unwrap().unwrap().success());
    assert_group_drained(&tree);
}

#[tokio::test]
async fn cleanup_kills_a_term_resistant_descendant_after_its_leader_exits() {
    let fixture = tempfile::tempdir().unwrap();
    let identity = fixture.path().join("descendant");
    let mut command = Command::new("python3");
    command
        .args([
            "-c",
            r#"
import os, signal, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.write(1,b"ready"); time.sleep(30)'], stdout=subprocess.PIPE)
assert child.stdout.read(5) == b'ready'
with open(sys.argv[1], 'w') as output:
    output.write(str(child.pid))
os._exit(0)
"#,
        ])
        .arg(&identity);
    let (mut tree, mut child) = OwnedProcessTree::spawn(&mut command).unwrap();
    wait_for_unreaped_exit(tree.group).await;
    let descendant: libc::pid_t = std::fs::read_to_string(identity).unwrap().parse().unwrap();

    tree.drain(&mut child).await.unwrap();
    assert!(child.try_wait().unwrap().unwrap().success());
    assert_group_drained(&tree);
    // SAFETY: this observes only the descendant PID published by the owned fixture.
    assert_eq!(unsafe { libc::kill(descendant, 0) }, -1);
    assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
}

async fn wait_for_unreaped_exit(pid: libc::pid_t) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    loop {
        // WNOWAIT observes the direct child's exit without consuming Tokio's wait result.
        // SAFETY: waitid writes to a valid siginfo_t and addresses only this fixture's child.
        let mut information: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let result = unsafe {
            libc::waitid(
                libc::P_PID,
                pid as libc::id_t,
                &mut information,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        assert_eq!(
            result,
            0,
            "observe child exit: {}",
            io::Error::last_os_error()
        );
        if information.si_signo == libc::SIGCHLD {
            return;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "leader did not exit"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

fn assert_group_drained(tree: &OwnedProcessTree) {
    assert!(tree.drained);
    // SAFETY: signal zero observes only the group created by this fixture.
    assert_eq!(unsafe { libc::kill(-tree.group, 0) }, -1);
    assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
}
