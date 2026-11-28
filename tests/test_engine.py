import concurrent.futures
import tempfile
import unittest
from unittest.mock import patch

from engine import Engine, digest


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.conn = self.connection("A")

    def connection(self, project):
        self.engine.add_project(project, "")
        conn = dict(
            id="source" + project,
            kind="slack",
            label=project,
            config={},
            status="Connected",
        )
        self.db.put("connections", conn)
        return conn

    def raw(self, key="1", when=None, **extra):
        return dict(
            key=key,
            timestamp=self.now if when is None else when,
            title="Status requested",
            body="Please provide a status update.",
            destination="channel/thread",
            account="my account",
            authored=False,
            owned=False,
            context={},
            **extra,
        )

    def event(self, raw=None, conn=None):
        c = conn or self.conn
        r = raw or self.raw()
        self.engine.ingest(c, r)
        return digest(c["id"], r["key"])

    def draft(self, **extra):
        key = self.event()
        task = self.engine.new_task(
            "Reply",
            "A",
            id=key,
            event=key,
            source="slack",
            account="my account",
            destination="channel/thread",
            status="review",
            draft="Ready",
            kind="status",
            **extra,
        )
        return task

    def test_connection_history_groups_child_sources_and_keeps_accounts_separate(self):
        a = self.conn
        b = self.connection("B")
        child = dict(a, id="channel-one", slack_parent=a["id"])
        github = dict(
            b, id="repo-one", kind="github", config={"repository": "org/repo"}, github_parent=b["id"]
        )
        for source in (child, github):
            self.db.put("connections", source)
            self.event(conn=source)
        self.db.log("Legacy discovery", "channel task", task=digest(child["id"], "1"))
        self.db.log("General", "unscoped")
        self.engine.connection_result(github, "Needs attention: offline")
        history = self.engine.state()["activity"]

        def kinds(source):
            return [row["kind"] for row in history if row["connection"] == source]

        self.assertCountEqual(kinds(a["id"]), ["Activity read", "Legacy discovery"])
        self.assertCountEqual(kinds(b["id"]), ["Activity read", "Connection"])
        self.assertIn("General", kinds(""))

    def test_dashboard_uses_triggering_event_for_task_origin(self):
        github = dict(self.conn, id="repo", kind="github", config={"repository": "org/client"})
        slack = dict(self.conn, id="channel", label="#release", slack_parent=self.conn["id"])
        email = dict(self.conn, id="mail", kind="email")
        cases = [
            (github, {"repository": "org/client"}, {"repository": "org/client", "source": "github"}),
            (slack, {"channel": "C123"}, {"channel": "#release", "workspace": "A", "source": "slack"}),
            (email, {"google_service": "Gmail"}, {"source": "Gmail"}),
        ]
        for connection, context, expected in cases:
            with self.subTest(kind=connection["kind"]):
                self.db.put("connections", connection)
                raw = dict(self.raw(), context=context, account="original-account")
                event = self.event(raw, connection)
                task = self.engine.new_task("Follow up", "A", event=event, account="sending-account")
                shown = next(t for t in self.engine.state()["tasks"] if t["id"] == task["id"])
                self.assertEqual(shown["account"], "sending-account")
                self.assertEqual(shown["origin"]["account"], "original-account")
                for field, value in expected.items():
                    self.assertEqual(shown["origin"][field], value)
                self.assertNotIn("origin", self.db.get("tasks", task["id"]))

    def test_provider_upgrade_preserves_review_and_delivery_mode(self):
        draft = self.draft()
        self.db.put("config", {"id": "settings", "dry": False, "model": "previous-local-model"})
        with patch("engine.worker.default_provider", return_value="codex"):
            upgraded = Engine(self.directory.name)
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.settings(), {"id": "settings", "dry": False, "provider": "codex"})
        self.assertEqual(upgraded.db.get("tasks", draft["id"]), draft)

    def test_general_tasks_and_reports_default_to_global(self):
        self.engine.action(dict(op="task", title="Plan the week"))
        self.engine.action(dict(op="report", text="Review missed messages"))
        self.assertEqual({t["project"] for t in self.db.rows("tasks")}, {"Global"})
        task = next(t for t in self.db.rows("tasks") if t["title"] == "Plan the week")
        with (
            patch("engine.worker.prepare_worktree") as worktree,
            patch(
                "engine.worker.run_agent",
                return_value=dict(outcome="done", kind="planning", text="Schedule prepared"),
            ),
        ):
            self.engine.process(task["id"])
        worktree.assert_not_called()
        self.assertEqual(self.db.get("tasks", task["id"])["status"], "done")

    def test_legacy_global_name_and_single_checkout_upgrade(self):
        legacy = {"id": "Global", "name": "Global", "repo": "/missing/old/repo"}
        self.db.put("projects", legacy)
        self.engine.action(dict(op="rule", project="Global", kind="reply", mode="auto"))
        upgraded = Engine(self.directory.name)
        self.addCleanup(upgraded.close)
        self.assertNotEqual(upgraded.global_project, "Global")
        self.assertEqual(upgraded.state()["projects"][0]["name"], upgraded.global_project)
        self.assertEqual(upgraded.repositories("Global"), [legacy["repo"]])
        self.assertEqual(upgraded.rule(upgraded.global_project, "reply"), "review")
        self.assertEqual(upgraded.rule("Global", "reply"), "auto")

    def test_upgrade_keeps_connection_ids_checkpoints_and_pending_reviews(self):
        draft = self.draft()
        legacy = dict(self.conn, project="A", cursor=self.now - 50, revision="previous")
        self.db.put("connections", legacy)
        with patch("engine.connectors.store_secret") as keychain:
            upgraded = Engine(self.directory.name)
        self.addCleanup(upgraded.close)
        keychain.assert_not_called()
        expected = {k: v for k, v in legacy.items() if k != "project"}
        self.assertEqual(upgraded.db.get("connections", legacy["id"]), expected)
        self.assertEqual(upgraded.db.get("tasks", draft["id"]), draft)
        self.assertEqual(upgraded.db.get("events", draft["event"])["connection"], legacy["id"])

    def test_tasks_from_one_account_use_distinct_projects(self):
        self.engine.add_project("B", "")
        for suggested, expected in [("A", "A"), ("B", "B"), ("unknown", "Global")]:
            key = self.event(self.raw(key=suggested))
            response = dict(relevant=True, owned=False, kind="reply", project=suggested)
            with patch("engine.worker.run_agent", return_value=response) as run:
                self.engine.discover(key)
            self.assertCountEqual(run.call_args.args[1]["projects"], ["A", "B", "Global"])
            self.assertEqual(self.db.get("tasks", key)["project"], expected)
            self.assertEqual(self.db.get("events", key)["connection"], self.conn["id"])

    def test_manual_connection_has_workspace_scope(self):
        form = dict(op="connect", kind="github", label="Platform", config={"repository": "org/platform"})
        with patch("engine.connectors.store_secret") as keychain:
            self.engine.action(dict(form, secret="test-token"))
            connection = next(c for c in self.engine.state()["connections"] if c["kind"] == "github")
            self.engine.action(dict(form, id=connection["id"], label="Renamed"))
            keychain.assert_called_once_with(connection["id"], "test-token")
        self.assertNotIn("project", connection)

    def test_provider_selection_applies_to_next_run(self):
        for provider in ("codex", "claude"):
            self.engine.action({"op": "settings", "provider": provider})
            with patch("engine.worker.run_agent", return_value={"body": "Be concise."}) as run:
                self.engine.infer("style", {"feedback": "Be concise."}, "A")
            self.assertEqual(run.call_args.args[2], provider)
        self.assertTrue(self.engine.settings()["dry"])
        with self.assertRaises(ValueError):
            self.engine.action({"op": "settings", "provider": "unknown", "dry": False})
        self.assertEqual(self.engine.settings()["provider"], "claude")
        self.assertTrue(self.engine.settings()["dry"])

    def test_exact_version_race_delivers_and_creates_one_followup(self):
        task = self.draft(commitments=[dict(title="Send figures", due=self.now + 3600)])

        def approve():
            try:
                self.engine.action(dict(op="approve", id=task["id"], version=task["version"]))
                return True
            except ValueError:
                return False

        with (
            patch("engine.connectors.send", return_value="receipt") as send,
            concurrent.futures.ThreadPoolExecutor(2) as pool,
        ):
            self.assertEqual(sorted(pool.map(lambda _: approve(), range(2))), [False, True])
            send.assert_called_once()
        rows = self.db.rows("tasks")
        self.assertEqual(
            [(t["title"], t["status"]) for t in rows], [("Reply", "done"), ("Send figures", "scheduled")]
        )

    def test_dry_mode_and_all_decision_categories(self):
        t = self.draft(categories=["reply"], commitments=[dict(title="Send figures", due=self.now)])
        for kind in ("status", "reply"):
            self.engine.action(dict(op="rule", project="A", kind=kind, mode="auto"))
        self.engine.action(dict(op="settings", dry=False))
        self.assertFalse(self.engine.auto(t))
        self.engine.action(dict(op="rule", project="A", kind="commitment", mode="auto"))
        self.assertTrue(self.engine.auto(t))
        self.engine.action(dict(op="settings", dry=True))
        with patch("engine.connectors.send") as send:
            self.engine.autosend((t["id"], t["version"]))
            send.assert_not_called()

    def test_slack_request_reads_linked_project_issue_before_drafting(self):
        source = dict(
            id="repo",
            kind="github",
            label="Project repo",
            config={"repository": "owner/project"},
        )
        self.db.put("connections", source)
        raw = self.raw()
        raw["body"] = "Could you summarize https://github.com/owner/project/issues/17?"
        key = self.event(raw)
        self.engine.new_task("Provide status", "A", raw["body"], id=key, event=key, kind="reply")

        def agent(role, payload, model, workdir):
            self.assertEqual(payload["linked_activity"][-1]["title"], "Export fix")
            return dict(outcome="draft", text="The fix is in review.", kind="reply", complete_after_send=True)

        with (
            patch(
                "engine.connectors.github_request", return_value={"title": "Export fix", "state": "open"}
            ) as read,
            patch("engine.worker.run_agent", side_effect=agent),
            patch("engine.connectors.send") as send,
        ):
            self.engine.process(key)
            read.assert_called_once_with(source, "GET", "issues/17")
            send.assert_not_called()
        self.assertEqual(self.db.get("tasks", key)["status"], "review")

    def on_linux(self, peers):
        me = self.engine.instances.me
        mac = dict(platform="darwin", task_ready=True, free=2, preference="first", can_clone=True, origins=[])
        found = [dict(id="mac", name="Mac", platform="darwin", capacity=mac)] if peers else []
        return (patch.object(self.engine.instances, "me", side_effect=lambda: {**me(), "platform": "linux"}),
                patch.object(self.engine, "fresh_peers", return_value=found))

    def test_check_needing_a_mac_moves_the_task_instead_of_asking(self):
        task = self.engine.new_task("Fix the menu bar icon", "A", "Build the app to check it.")
        seen = []

        def agent(role, payload, model, workdir):
            seen.append(payload)
            return dict(outcome="question", text="Did the app build pass on your Mac?", kind="task",
                        commitments=[], needs_platform="macos")

        linux, peers = self.on_linux(True)
        with linux, peers, patch("engine.worker.run_agent", side_effect=agent):
            self.engine.process(task["id"])
            row = self.db.get("tasks", task["id"])
            self.assertEqual(seen[0]["platform"], "linux")
            self.assertEqual((row["status"], row["needs_platform"], row["question"]), ("queued", "darwin", ""))
            self.assertIn("Moving to a Mac instance", row["reason"])
            # Only a Mac can take it, and this Linux instance holds it for the Mac.
            self.assertTrue(self.engine.can_take(row, dict(platform="darwin", can_clone=True)))
            self.assertFalse(self.engine.can_take(row, dict(platform="linux", can_clone=True)))
            self.assertTrue(self.engine.waiting_for_platform(row))
            self.now += Engine.PLATFORM_WAIT
            self.assertFalse(self.engine.waiting_for_platform(row))

            def proceed(role, payload, model, workdir):
                seen.append(payload)
                return dict(outcome="done", text="Fixed the icon.", kind="task", commitments=[],
                            unverified=["./scripts/build-app.sh produces build/MeMyselfAI.app"])

            with patch("engine.worker.run_agent", side_effect=proceed):
                self.engine.process(task["id"])
        self.assertIn("No Mac instance is reachable", seen[1]["verification_unavailable"])
        row = self.db.get("tasks", task["id"])
        self.assertEqual((row["status"], row["needs_platform"]), ("done", ""))
        self.assertTrue(row["result"].endswith(
            "Still to verify:\n- ./scripts/build-app.sh produces build/MeMyselfAI.app"))

    def test_check_needing_a_mac_with_none_reachable_continues_with_a_note(self):
        task = self.engine.new_task("Fix the menu bar icon", "A", "Build the app to check it.")
        answers = [
            dict(outcome="question", text="Did the build pass?", kind="task", commitments=[], needs_platform="macos"),
            dict(outcome="question", text="Still needs a Mac.", kind="task", commitments=[], needs_platform="macos"),
        ]
        linux, peers = self.on_linux(False)
        with linux, peers, patch("engine.worker.run_agent", side_effect=answers + [
                dict(question="Anything else?", context="", choices=[])]) as run:
            self.engine.process(task["id"])
            row = self.db.get("tasks", task["id"])
            self.assertEqual((row["status"], row["verification_unavailable"], row["question"]),
                             ("queued", "darwin", ""))
            self.assertFalse(self.engine.waiting_for_platform(row))
            self.engine.process(task["id"])
            self.assertIn("verification_unavailable", run.call_args_list[1].args[1])
        # The fallback happens once: a second request for a Mac is not acted on again.
        self.assertEqual(self.db.get("tasks", task["id"])["status"], "question")

    def test_feedback_retires_preview(self):
        t = self.draft()
        self.engine.action(dict(op="feedback", id=t["id"], text="Use the short version"))
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.engine.deliver(t["id"], t["version"])

    def test_ownership_retires_pending_action(self):
        t = self.draft()
        raw = self.raw()
        raw["owned"] = True
        self.engine.ingest(self.conn, raw)
        self.assertEqual(self.db.get("tasks", t["id"])["status"], "skipped")
        with self.assertRaises(ValueError):
            self.engine.deliver(t["id"], t["version"])

    def test_crash_and_uncertain_write_need_reconciliation(self):
        t = self.draft()
        with (
            patch("engine.connectors.send", side_effect=TimeoutError("unknown")),
            self.assertRaises(ValueError),
        ):
            self.engine.deliver(t["id"], t["version"])
        row = self.db.get("tasks", t["id"])
        self.assertEqual(row["status"], "uncertain")
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="retry", id=t["id"]))
        self.engine.action(dict(op="reconcile", id=t["id"], version=row["version"], delivered=False))
        self.assertEqual(self.db.get("tasks", t["id"])["status"], "review")
        row["status"] = "sending"
        self.db.put("tasks", row)
        restarted = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.db.get("tasks", t["id"])["status"], "uncertain")

    def test_queue_choice_is_bound_to_displayed_question_and_remains_local(self):
        task = self.engine.new_task("Prepare changelog", "A")
        choices = [dict(label="May", detail="Cover the May milestone.")]
        with patch("worker.run_agent", side_effect=[dict(outcome="question", text="Which milestone?"),
                   dict(question="Which release?", context="The changelog needs a cutoff.", choices=choices)]):
            self.engine.process(task["id"])
        row = self.db.get("tasks", task["id"])
        self.assertEqual(row["answer_choices"], choices)
        self.assertEqual(row["question_context"], "The changelog needs a cutoff.")
        action = dict(op="answer", id=task["id"], version=row["version"], choice=0)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.engine.action(dict(action, version=row["version"]-1))
        with self.assertRaisesRegex(ValueError, "displayed"):
            self.engine.action(dict(action, choice=-1))
        self.engine.action(action)
        answered = self.db.get("tasks", task["id"])
        self.assertEqual(answered["feedback"][-1], dict(question="Which release?", answer="May", detail=choices[0]["detail"]))
        self.assertEqual((answered["status"], answered["receipts"]), ("queued", []))
        self.assertNotIn("answer_choices", answered)

    def test_formatting_legacy_question_preserves_decision_and_never_answers(self):
        old = "Which date?\nThe May and June releases are both open."
        task = self.engine.new_task("Prepare changelog", "A", status="question", question=old, decision_id="shared")
        formatted = dict(question="Which release?", choices=[], context="Choose a cutoff.", answer="May")
        with patch("worker.run_agent", return_value=formatted):
            self.engine.format_existing_question((task["id"], task["version"]))
        row = self.db.get("tasks", task["id"])
        self.assertEqual((row["status"], row["decision_id"], row["feedback"]), ("question", "shared", []))
        self.assertEqual((row["question"], row["question_context"]), ("Which release?", old))

    def test_agent_questions_and_independent_work(self):
        directories = {}
        def model(role, payload, model, workdir):
            task = payload["task"]
            self.assertEqual(workdir.parent, self.engine.db.root.resolve() / "agent-workspaces")
            self.assertTrue(workdir.is_dir())
            self.assertEqual(directories.setdefault(task["id"], workdir), workdir)
            if role == "task":
                if payload["task"]["title"] == "Blocked":
                    return dict(
                        outcome="question",
                        kind="task",
                        text="What date?",
                        independent_steps=[dict(title="Summarize notes", body="Prepare a local summary")],
                    )
                return dict(outcome="done", kind="task", text="Summary complete")
            return dict(question="What date should I use?")

        with patch("engine.worker.run_agent", side_effect=model):
            a = self.engine.new_task("Blocked", "A")
            b = self.engine.new_task("Blocked", "A")
            for task in (a, b):
                self.engine.process(task["id"])
                self.assertEqual(self.db.get("tasks", task["id"])["status"], "question")
            self.assertNotEqual(directories[a["id"]], directories[b["id"]])
            child = self.db.get("tasks", digest(a["id"], "Summarize notes"))
            self.engine.process(child["id"])
            self.assertEqual(self.db.get("tasks", child["id"])["status"], "done")
            self.engine.action(dict(op="answer", id=a["id"], text="Friday"))
            self.assertEqual(self.db.get("tasks", b["id"])["status"], "queued")

    def test_recovery_is_bounded_cross_project_applied_and_reversible(self):
        self.connection("B")
        end = self.now
        report = self.engine.new_task("Missed work", "Global", "Check indirect asks", special="report")
        self.now += 3600
        seen = []

        def poll(c, start, stop):
            seen.append((c["label"], start, stop))
            return [self.raw(c["id"], start), self.raw("older", start - 1), self.raw("later", stop + 1)]

        def infer(role, payload, model, workdir):
            if role == "improvement":
                return dict(body="Remember indirect requests.", reason="A report supplied an example.")
            return dict(
                title="Follow up", relevant=True, owned=False, kind="reply", reason="Indirect request"
            )

        with (
            patch("engine.connectors.poll", side_effect=poll),
            patch("engine.worker.run_agent", side_effect=infer),
        ):
            self.engine.process(report["id"])
        self.assertEqual(seen, [("A", end - 10800, end), ("B", end - 10800, end)])
        self.assertEqual(len(self.db.rows("events")), 2)
        self.assertEqual(self.db.get("tasks", report["id"])["status"], "done")
        skill = next(x for x in self.db.rows("skills") if x["active"])
        self.assertEqual((self.db.root / "skills/workflow.md").read_text(), skill["body"])
        self.engine.action(dict(op="revert_skill", id=skill["id"]))
        self.assertNotEqual((self.db.root / "skills/workflow.md").read_text(), skill["body"])

    def test_style_requires_review(self):
        self.engine.action(dict(op="style", text="Lead with the answer"))
        task = self.db.rows("tasks")[0]
        with patch("engine.worker.run_agent", return_value=dict(body="Lead with the answer")):
            self.engine.process(task["id"])
        rev = next(s for s in self.db.rows("skills") if s["kind"] == "style")
        file = self.db.root / "skills/style.md"
        self.assertEqual(file.read_text(), "")
        self.engine.action(dict(op="accept_skill", id=rev["id"]))
        self.assertEqual(file.read_text(), "Lead with the answer")


class FinishedSourceTests(unittest.TestCase):
    """Open tasks close when their GitHub item closes after they began. See docs/specs/task-workflow.md."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.engine.add_project("A", "")

    def github(self, key, state, when, **context):
        self.db.put("events", dict(id=key, connection="gh", project="A", state="handled", raw=dict(
            key=key, timestamp=when, title="PR", body="", destination="org/repo#7", account="me",
            authored=True, owned=False, context=dict(repository="org/repo", number=7, state=state,
                                                     is_pull_request=True, **context))))

    def task(self, status="question", **fields):
        return self.engine.new_task("Get #7 reviewed", "A", destination="org/repo#7", source="github",
                                    status=status, question="Who should review it?", **fields)

    def test_merged_pr_closes_open_tasks_with_a_reason(self):
        waiting, failed = self.task(), self.task(status="failed")
        self.github("e1", "open", self.now - 5)
        self.github("e2", "closed", self.now + 60, closed_at=self.now + 50, merged=True)
        self.engine.close_finished_sources()
        for t in (waiting, failed):
            row = self.db.get("tasks", t["id"])
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["question"], "")
            self.assertIn("merged on GitHub", row["reason"])

    def test_reopened_running_and_older_closures_are_left_alone(self):
        self.github("e1", "closed", self.now - 100, closed_at=self.now - 100)
        made_for_closed = self.task()
        self.engine.close_finished_sources()
        self.assertEqual(self.db.get("tasks", made_for_closed["id"])["status"], "question")

        running = self.task(status="working")
        self.github("e2", "closed", self.now + 10, closed_at=self.now + 10)
        self.engine.close_finished_sources()
        self.assertEqual(self.db.get("tasks", running["id"])["status"], "working")
        self.assertEqual(self.db.get("tasks", made_for_closed["id"])["status"], "closed")

        reopened = self.task()
        self.github("e3", "open", self.now + 20)
        self.engine.close_finished_sources()
        self.assertEqual(self.db.get("tasks", reopened["id"])["status"], "question")

    def test_steps_close_with_their_parents_item(self):
        parent = self.task(status="closed")
        step = self.engine.new_task("Rerun CodeRabbit on #7", "A", status="question", question="Post it?",
                                    parent=parent["id"], depth=1)
        unrelated = self.engine.new_task("Something else", "A", status="question", question="?")
        self.github("e1", "closed", self.now + 10, closed_at=self.now + 10)
        self.engine.close_finished_sources()
        self.assertEqual(self.db.get("tasks", step["id"])["status"], "closed")
        self.assertEqual(self.db.get("tasks", unrelated["id"])["status"], "question")

    def test_runs_that_lost_the_app_files_are_retried_on_restart(self):
        lost = self.task(status="failed", result="[Errno 2] No such file or directory: "
                         "'/tmp/w/build/MeMyselfAI.app/Contents/Resources/skills/task-interaction/SKILL.md'")
        other = self.task(status="failed", result="The CLI exited with status 1.")
        self.engine.close()
        restarted = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.db.get("tasks", lost["id"])["status"], "queued")
        self.assertEqual(restarted.db.get("tasks", other["id"])["status"], "failed")


class ConflictTests(unittest.TestCase):
    """Overlapping and dependent tasks. See "Overlapping and dependent tasks" in docs/specs/task-workflow.md."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.engine.add_project("A", "")
        self.engine.suggestions.next_scan = self.now + 3600

    def event(self, key, when, destination="org/repo#7", connection="gh", state="handled", **context):
        if not context:
            context = dict(repository="org/repo", number=7, state="open", is_pull_request=True)
        self.db.put("events", dict(id=key, connection=connection, project="A", state=state, raw=dict(
            key=key, timestamp=when, title="Comment", body="", destination=destination, account="me",
            authored=False, owned=False, context=context)))
        return key

    def task(self, key, title, status="queued", **fields):
        return self.engine.new_task(title, "A", id=key, event=key, source="github", status=status, **fields)

    def resolve(self):
        return self.engine.resolve_conflicts(self.db.rows("tasks"), self.db.rows("events"))

    def started(self):
        with patch.object(self.engine, "process"):
            self.engine.tick()
        return {key.removeprefix("task:") for key in self.engine.jobs if key.startswith("task:")}

    def test_newer_task_on_the_same_thread_replaces_an_idle_one_and_inherits_its_work(self):
        self.db.put("connections", dict(id="chat", kind="slack", label="team", config={}, status="Connected"))
        thread = dict(destination="C1 / thread 1.0", connection="chat", thread="1.0", channel="C1")
        old = self.task(self.event("e1", self.now - 60, **thread), "Reply to the first review", status="review",
                        draft="Thanks, fixing it.", feedback=[{"question": "Tone?", "answer": "Short"}])
        new = self.task(self.event("e2", self.now, **thread), "Reply to the second review")
        self.assertEqual(self.started(), {new["id"]})
        row = self.db.get("tasks", old["id"])
        self.assertEqual((row["status"], row["draft"], row["superseded_by"]), ("closed", "", new["id"]))
        self.assertIn("Replaced by a newer task on the same thread: Reply to the second review", row["reason"])
        with self.assertRaises(Exception):
            self.engine.approve(old["id"], row["version"])  # a replaced draft can no longer be sent
        with patch("worker.run_agent", return_value=dict(outcome="done", text="Done", summary="Done")) as agent:
            try:
                self.engine.prepare(self.db.get("tasks", new["id"]))
            except Exception:
                pass
        payload = agent.call_args.args[1]
        self.assertEqual(payload["replaced_tasks"][0]["draft"], "Thanks, fixing it.")
        self.assertEqual(payload["replaced_tasks"][0]["answers"], [{"question": "Tone?", "answer": "Short"}])

    def test_newer_task_waits_while_the_older_one_is_preparing(self):
        old = self.task(self.event("e1", self.now - 60), "First", status="working")
        new = self.task(self.event("e2", self.now), "Second")
        self.assertEqual(self.started(), set())
        waiting = self.db.get("tasks", new["id"])
        self.assertEqual((waiting["status"], waiting["reason"]), ("queued", "Waiting for First to finish first"))
        self.assertEqual(self.db.get("tasks", old["id"])["status"], "working")
        row = self.db.get("tasks", old["id"])
        row.update(status="review", draft="Draft")
        self.engine.save(row, "Prepared")
        self.engine.jobs.clear()
        self.assertEqual(self.started(), {new["id"]})
        self.assertEqual(self.db.get("tasks", old["id"])["status"], "closed")

    def test_approved_or_sending_versions_are_left_to_finish(self):
        old = self.task(self.event("e1", self.now - 60), "First", status="review", draft="x")
        row = self.db.get("tasks", old["id"])
        row["approved_version"] = row["version"] + 1
        self.engine.save(row, "Approved")
        new = self.task(self.event("e2", self.now), "Second")
        closed, waiting = self.resolve()
        self.assertEqual(closed, set())
        self.assertIn(new["id"], waiting)

    def test_the_task_with_older_evidence_is_replaced_even_when_created_later(self):
        new = self.task(self.event("e2", self.now), "Newest comment")
        old = self.task(self.event("e1", self.now - 60), "Older comment")
        closed, waiting = self.resolve()
        self.assertEqual((closed, waiting), ({old["id"]}, {}))

    def test_different_threads_steps_and_local_tasks_never_conflict(self):
        self.task(self.event("e1", self.now - 60), "On #7")
        self.task(self.event("e2", self.now, destination="org/repo#8",
                             **dict(repository="org/repo", number=8, state="open", is_pull_request=True)), "On #8")
        mail = dict(subject="Invoice", thread="<a@x>", message_id="<b@x>")
        self.task(self.event("m1", self.now, destination="a@x", connection="mail", **mail), "Mail")
        self.task(self.event("m2", self.now, destination="a@x", connection="mail",
                             **dict(mail, thread="<other@x>")), "Other mail thread")
        parent = self.task(self.event("e3", self.now - 30, destination="org/repo#9",
                                      **dict(repository="org/repo", number=9, state="open", is_pull_request=False)),
                           "Fix #9")
        self.engine.new_task("Step one", "A", parent=parent["id"], depth=1, event="e3")
        self.engine.new_task("Step two", "A", parent=parent["id"], depth=1, event="e3")
        self.engine.new_task("Local", "A")
        self.engine.new_task("Local", "A")
        self.assertEqual(self.resolve(), (set(), {}))

    def test_email_replies_join_the_thread_of_the_first_message(self):
        first = self.task(self.event("m1", self.now - 60, destination="a@x", connection="mail",
                                     subject="Invoice", thread="", message_id="<root@x>"), "Invoice")
        self.task(self.event("m2", self.now, destination="a@x", connection="mail",
                             subject="Re: Invoice", thread="<root@x>", message_id="<reply@x>"), "Invoice follow-up")
        self.assertEqual(self.resolve()[0], {first["id"]})

    def test_a_newer_message_still_being_read_holds_the_thread_back(self):
        old = self.task(self.event("e1", self.now - 60), "First")
        self.event("e2", self.now, state="new")
        self.assertEqual(self.resolve(), (set(), {}))  # its connection is gone, so it will never be read
        self.db.put("connections", dict(id="gh", kind="github", label="gh", config={}, status="Connected"))
        closed, waiting = self.resolve()
        self.assertEqual(waiting[old["id"]][1], "Waiting for new activity on this thread to be read")

    def test_after_waits_for_prerequisites_and_passes_their_result(self):
        fix = self.engine.new_task("Fix the bug", "A", status="working")
        reply = self.engine.new_task("Tell the reporter", "A", after=[fix["id"]])
        self.assertNotIn(reply["id"], self.started())
        self.assertEqual(self.db.get("tasks", reply["id"])["reason"], "Waiting for Fix the bug to finish first")
        row = self.db.get("tasks", fix["id"])
        row.update(status="failed")
        self.engine.save(row, "Failed")
        self.engine.jobs.clear()
        self.assertNotIn(reply["id"], self.started())
        row = self.db.get("tasks", fix["id"])
        row.update(status="done", result="Fixed in PR #12")
        self.engine.save(row, "Done")
        self.engine.jobs.clear()
        self.assertIn(reply["id"], self.started())
        with patch("worker.run_agent", return_value=dict(outcome="done", text="Done", summary="Done")) as agent:
            try:
                self.engine.prepare(self.db.get("tasks", reply["id"]))
            except Exception:
                pass
        self.assertEqual(agent.call_args.args[1]["prerequisites"],
                         [dict(title="Fix the bug", status="done", result="Fixed in PR #12")])

    def test_explicit_replaces_closes_the_named_task_and_its_open_steps(self):
        old = self.engine.new_task("Build the Contest tab", "A", status="question", question="Which layout?")
        step = self.engine.new_task("Contest tab: API", "A", parent=old["id"], depth=1)
        new = self.engine.new_task("Drop contests, build leaderboard", "A", replaces=[old["id"]])
        closed, _ = self.resolve()
        self.assertEqual(closed, {old["id"]})
        row = self.db.get("tasks", old["id"])
        self.assertEqual((row["status"], row["question"]), ("closed", ""))
        self.assertEqual(row["reason"], "Replaced by a newer task: Drop contests, build leaderboard")
        self.assertEqual(self.db.get("tasks", step["id"])["status"], "closed")
        self.assertEqual(self.db.get("tasks", new["id"])["status"], "queued")
