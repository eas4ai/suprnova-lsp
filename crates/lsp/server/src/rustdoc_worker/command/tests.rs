use std::{path::Path, time::Duration};

use tokio::{process::Command, sync::watch};

use super::SupervisedCommand;

const HELD_TREE: &str = r#"
import json, os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'])
with open(sys.argv[1], 'w') as output:
    json.dump([os.getpid(), child.pid], output)
time.sleep(30)
"#;

#[tokio::test]
async fn cancellation_reaps_a_term_resistant_tree_and_preserves_an_unrelated_child() {
    let fixture = tempfile::tempdir().unwrap();
    let identity = fixture.path().join("children.json");
    let mut unrelated = Command::new("python3")
        .args(["-c", "import time; time.sleep(30)"])
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let (changes, mut receiver) = watch::channel(1);
    let path = identity.clone();
    let task = tokio::spawn(async move {
        let mut command = Command::new("python3");
        command.args(["-c", HELD_TREE]).arg(path);
        SupervisedCommand::run(
            &mut command,
            &mut receiver,
            1,
            Duration::from_secs(10),
            1024,
        )
        .await
    });
    let children = children(&identity).await;
    assert!(!task.is_finished());
    changes.send_replace(2);
    let result = tokio::time::timeout(Duration::from_secs(5), task)
        .await
        .unwrap()
        .unwrap();
    assert!(result.err().unwrap().to_string().contains("superseded"));
    for child in children {
        assert_reaped(child);
    }
    assert!(
        unrelated.try_wait().unwrap().is_none(),
        "cleanup must leave unrelated processes alive"
    );
    unrelated.kill().await.unwrap();
}

#[tokio::test]
async fn timeout_drains_the_entire_owned_tree() {
    let fixture = tempfile::tempdir().unwrap();
    let identity = fixture.path().join("children.json");
    let (_changes, mut receiver) = watch::channel(1);
    let mut command = Command::new("python3");
    command.args(["-c", HELD_TREE]).arg(&identity);
    let result = SupervisedCommand::run(
        &mut command,
        &mut receiver,
        1,
        Duration::from_millis(500),
        1024,
    )
    .await;
    assert!(result.err().unwrap().to_string().contains("timed out"));
    for child in children(&identity).await {
        assert_reaped(child);
    }
}

#[tokio::test]
async fn output_limits_keep_draining_both_pipes_until_the_command_exits() {
    let (_changes, mut receiver) = watch::channel(1);
    let mut command = Command::new("python3");
    command.args(["-c", "import sys; sys.stdout.write('o'*3000000); sys.stdout.flush(); sys.stderr.write('e'*3000000)"]);
    let output =
        SupervisedCommand::run(&mut command, &mut receiver, 1, Duration::from_secs(5), 1024)
            .await
            .unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout.len(), 1024);
    assert_eq!(output.stderr.len(), 32 * 1024);
    assert!(output.stdout_truncated);
    assert!(
        output
            .require_success("compiler")
            .err()
            .unwrap()
            .to_string()
            .contains("output limit")
    );
}

async fn children(path: &Path) -> Vec<libc::pid_t> {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    loop {
        match std::fs::read(path) {
            Ok(bytes) => return serde_json::from_slice(&bytes).unwrap(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => panic!("read controlled compiler identities: {error}"),
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "controlled compiler tree did not start"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

fn assert_reaped(child: libc::pid_t) {
    // SAFETY: signal zero observes only a PID returned by this test's owned compiler fixture.
    assert_eq!(
        unsafe { libc::kill(child, 0) },
        -1,
        "compiler descendant {child} survived"
    );
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ESRCH)
    );
}
