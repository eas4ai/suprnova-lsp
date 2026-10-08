//! Select work for the single analysis owner without crossing project mutations.

use std::{collections::VecDeque, sync::mpsc::Receiver};

use super::{EngineCommand, QueuedEngineCommand};

const LOOKAHEAD: usize = 32;

pub(super) struct EngineCommandQueue {
    receiver: Receiver<QueuedEngineCommand>,
    pending: VecDeque<QueuedEngineCommand>,
    oldest_next: bool,
}

impl EngineCommandQueue {
    pub(super) fn new(receiver: Receiver<QueuedEngineCommand>) -> Self {
        Self {
            receiver,
            pending: VecDeque::new(),
            oldest_next: false,
        }
    }

    pub(super) fn next(&mut self) -> Option<QueuedEngineCommand> {
        if self.pending.is_empty() {
            self.pending.push_back(self.receiver.recv().ok()?);
        }

        // A preferred query gets one turn, then the oldest command gets one.
        // This keeps decoration requests and background publication moving even
        // when the client continuously sends completion or hover requests.
        if std::mem::take(&mut self.oldest_next) {
            return self.pending.pop_front();
        }

        // Inspect a finite prefix only. Stop at the first mutation, including
        // background products/progress, so a hover never sees a later saved state
        // before an earlier command has run. Query-owned loads still release on
        // this same lane; no query executes concurrently with another command.
        while self.pending.len() < LOOKAHEAD
            && self
                .pending
                .back()
                .is_some_and(|queued| queued.command.can_bypass())
        {
            let Ok(queued) = self.receiver.try_recv() else {
                break;
            };
            self.pending.push_back(queued);
        }
        let preferred = self
            .pending
            .iter()
            .take_while(|queued| queued.command.can_bypass())
            .position(|queued| queued.command.is_interactive());
        let index = preferred.unwrap_or(0);
        self.oldest_next = preferred.is_some();
        self.pending.remove(index)
    }
}

impl EngineCommand {
    fn is_interactive(&self) -> bool {
        matches!(self, Self::Hover { .. } | Self::Completion { .. })
    }

    fn can_bypass(&self) -> bool {
        // Prefer an explicit list: adding a new command must not silently make
        // it reorderable. Save, recovery, initialization and every publication
        // command remain barriers, along with formatting and rename operations.
        matches!(
            self,
            Self::Hover { .. }
                | Self::Completion { .. }
                | Self::GotoDefinition { .. }
                | Self::GotoTypeDefinition { .. }
                | Self::GotoImplementation { .. }
                | Self::References { .. }
                | Self::DocumentHighlight { .. }
                | Self::CodeAction { .. }
                | Self::DocumentSymbol { .. }
                | Self::FoldingRange { .. }
                | Self::SemanticTokens { .. }
                | Self::InlayHint { .. }
                | Self::WorkspaceSymbol { .. }
        )
    }
}

#[cfg(test)]
mod tests {
    use std::{path::PathBuf, sync::mpsc};

    use rg_lsp_proto::{
        DocumentRevision, EditorDocumentSnapshot, GlobalPositionSnapshot, OpenDocumentSession,
        OpenDocumentsRevision,
    };
    use tokio::sync::oneshot;

    use super::EngineCommandQueue;
    use crate::engine::{EngineCommand, QueuedEngineCommand};

    fn hover(name: &str) -> EngineCommand {
        let document = EditorDocumentSnapshot::new(
            PathBuf::from(name),
            OpenDocumentSession::new(1),
            DocumentRevision::new(1),
            Some(1),
            "fn value() {}".to_owned(),
        );
        EngineCommand::Hover {
            input: GlobalPositionSnapshot::new(
                document.target().clone(),
                OpenDocumentsRevision::new(1),
                vec![document],
                gen_lsp_types::Position::new(0, 3),
            ),
            respond_to: oneshot::channel().0,
        }
    }

    fn ordinary(name: &str) -> EngineCommand {
        EngineCommand::WorkspaceSymbol {
            query: name.to_owned(),
            respond_to: oneshot::channel().0,
        }
    }

    fn name(command: QueuedEngineCommand) -> String {
        match command.command {
            EngineCommand::Hover { input, .. } => input.target().path().display().to_string(),
            EngineCommand::WorkspaceSymbol { query, .. } => query,
            EngineCommand::SavedProjectChanges { .. } => "save".to_owned(),
            EngineCommand::DeferredIndexingFinished { .. } => "background".to_owned(),
            EngineCommand::Shutdown(_) => "shutdown".to_owned(),
            EngineCommand::Completion { input, .. } => {
                input.document().path().display().to_string()
            }
            other if !other.can_bypass() => "barrier".to_owned(),
            other => panic!("unexpected queued test command: {other:?}"),
        }
    }

    fn queue(commands: Vec<EngineCommand>) -> EngineCommandQueue {
        let (sender, receiver) = mpsc::channel();
        for command in commands {
            sender
                .send(QueuedEngineCommand::new(command))
                .expect("test queue is open");
        }
        EngineCommandQueue::new(receiver)
    }

    #[test]
    fn hover_bypasses_queued_analysis_but_the_oldest_query_gets_the_next_turn() {
        let mut queue = queue(vec![ordinary("first"), ordinary("second"), hover("hover")]);
        assert_eq!(name(queue.next().expect("hover")), "hover");
        assert_eq!(name(queue.next().expect("oldest")), "first");
        assert_eq!(name(queue.next().expect("next")), "second");
        assert!(queue.next().is_none());
    }

    #[test]
    fn interactive_burst_does_not_starve_ordinary_analysis() {
        let mut queue = queue(vec![
            ordinary("first"),
            hover("hover-one"),
            ordinary("second"),
            hover("hover-two"),
            hover("hover-three"),
        ]);
        let actual: Vec<_> = std::iter::from_fn(|| queue.next()).map(name).collect();
        assert_eq!(
            actual,
            ["hover-one", "first", "hover-two", "second", "hover-three"]
        );
    }

    #[test]
    fn finite_lookahead_does_not_drain_an_unbounded_query_prefix() {
        let mut commands: Vec<_> = (0..super::LOOKAHEAD)
            .map(|_| ordinary("ordinary"))
            .collect();
        commands.push(hover("outside-prefix"));
        let mut queue = queue(commands);
        assert_eq!(name(queue.next().expect("bounded oldest turn")), "ordinary");
        assert_eq!(queue.pending.len(), super::LOOKAHEAD - 1);
        assert_eq!(
            name(queue.next().expect("next prefix includes hover")),
            "outside-prefix"
        );
        assert_eq!(
            name(queue.next().expect("oldest remains eligible")),
            "ordinary"
        );
    }

    #[test]
    fn hover_cannot_cross_an_earlier_save_background_completion_or_shutdown() {
        let barriers = [
            EngineCommand::RecoverStaleSource {
                path: PathBuf::from("changed.rs"),
            },
            EngineCommand::RustdocRequested {
                generation: 1,
                respond_to: oneshot::channel().0,
            },
            EngineCommand::RustdocBuildInputs {
                generation: 1,
                respond_to: oneshot::channel().0,
            },
            EngineCommand::ReindexWorkspace {
                respond_to: oneshot::channel().0,
            },
            EngineCommand::SetDeferredIndexingPriority {
                path: PathBuf::from("open.rs"),
                prioritized: true,
                respond_to: oneshot::channel().0,
            },
            EngineCommand::SavedProjectChanges {
                changes: vec![],
                respond_to: oneshot::channel().0,
            },
            EngineCommand::DeferredIndexingFinished {
                generation: 1,
                result: Ok(()),
            },
            EngineCommand::Shutdown(oneshot::channel().0),
        ];
        for barrier in barriers {
            let mut queue = queue(vec![ordinary("first"), barrier, hover("hover")]);
            assert_eq!(name(queue.next().expect("query before mutation")), "first");
            assert!(matches!(
                name(queue.next().expect("mutation")).as_str(),
                "save" | "background" | "shutdown" | "barrier"
            ));
            assert_eq!(name(queue.next().expect("query after mutation")), "hover");
        }
    }

    #[test]
    fn completion_receives_the_same_bounded_preference_as_hover() {
        let document = EditorDocumentSnapshot::new(
            PathBuf::from("completion"),
            OpenDocumentSession::new(1),
            DocumentRevision::new(1),
            Some(1),
            "fn value() {}".to_owned(),
        );
        let completion = EngineCommand::Completion {
            input: document.with_position(gen_lsp_types::Position::new(0, 3)),
            client_capabilities: rg_lsp_proto::CompletionClientCapabilities::default(),
            respond_to: oneshot::channel().0,
        };
        let mut queue = queue(vec![ordinary("first"), completion, hover("hover")]);
        assert_eq!(name(queue.next().expect("completion")), "completion");
        assert_eq!(name(queue.next().expect("oldest")), "first");
        assert_eq!(name(queue.next().expect("hover")), "hover");
    }

    #[test]
    fn replenished_interactive_requests_leave_oldest_commands_a_turn() {
        let (sender, receiver) = mpsc::channel();
        let mut queue = EngineCommandQueue::new(receiver);
        for number in 0..20 {
            for command in [
                ordinary(&format!("ordinary-{number}")),
                hover(&format!("hover-{number}")),
            ] {
                sender
                    .send(QueuedEngineCommand::new(command))
                    .expect("live queue");
            }
            let first = name(queue.next().expect("first turn"));
            sender
                .send(QueuedEngineCommand::new(hover("arriving")))
                .expect("live queue");
            let second = name(queue.next().expect("second turn"));
            let mut turns = [first, second];
            turns.sort();
            assert_eq!(
                turns,
                [format!("hover-{number}"), format!("ordinary-{number}")]
            );
            assert_eq!(name(queue.next().expect("arrival")), "arriving");
        }
        drop(sender);
        assert!(queue.next().is_none());
    }

    #[test]
    fn dropping_the_queue_releases_buffered_and_unread_request_payloads() {
        let mut buffered = hover("buffered");
        let (sender, mut buffered_response) = oneshot::channel();
        if let EngineCommand::Hover { respond_to, .. } = &mut buffered {
            *respond_to = sender;
        }
        let mut unread = hover("unread");
        let (sender, mut unread_response) = oneshot::channel();
        if let EngineCommand::Hover { respond_to, .. } = &mut unread {
            *respond_to = sender;
        }
        let mut commands = vec![hover("selected")];
        commands.extend((0..super::LOOKAHEAD - 2).map(|_| ordinary("ordinary")));
        commands.extend([buffered, unread]);
        let mut queue = queue(commands);
        assert_eq!(name(queue.next().expect("selected hover")), "selected");
        // Buffered responders belong to the bounded prefix; unread responders
        // still belong to the channel. Shutdown must release both groups.
        assert_eq!(
            buffered_response.try_recv(),
            Err(oneshot::error::TryRecvError::Empty)
        );
        assert_eq!(
            unread_response.try_recv(),
            Err(oneshot::error::TryRecvError::Empty)
        );
        drop(queue);
        assert_eq!(
            buffered_response.try_recv(),
            Err(oneshot::error::TryRecvError::Closed)
        );
        assert_eq!(
            unread_response.try_recv(),
            Err(oneshot::error::TryRecvError::Closed)
        );
    }
}
