"""Quitting stops running agents at once instead of waiting for them. See docs/specs/desktop-runtime.md."""

import subprocess
import threading
import time
import unittest

import worker


class StopAllTests(unittest.TestCase):
    def tearDown(self):
        worker.stopping.clear()

    def test_stop_all_kills_tracked_runs_and_reports_a_stop(self):
        process = subprocess.Popen(["sleep", "30"], start_new_session=True)
        outcome = {}

        def run():
            try:
                with process, worker._tracked(process):
                    process.wait()
                    if process.returncode:
                        raise worker.WorkerError("exited")
            except Exception as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.2)
        started = time.monotonic()
        worker.stop_all()
        thread.join(5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsInstance(outcome.get("error"), worker.WorkerStopped)

    def test_runs_started_after_stop_are_killed(self):
        worker.stop_all()
        process = subprocess.Popen(["sleep", "30"], start_new_session=True)
        with self.assertRaises(worker.WorkerStopped):
            with process, worker._tracked(process):
                process.wait()
        self.assertIsNotNone(process.returncode)
