"""Test the transport's authorization gate without binding a listening socket."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from io import BytesIO

from server import Handler


class DesktopHTTPTests(TestCase):
    def request(self, path, **headers):
        req = Handler.__new__(Handler)
        req.path = path
        req.server = SimpleNamespace(
            origin="http://127.0.0.1:8801", session="session", csrf="csrf", ticket="ticket", engine=Mock()
        )
        req.headers = {"Host": "127.0.0.1:8801", **headers}
        req.output = Mock()
        return req

    def test_block_cross_origin_and_missing_tokens(self):
        for invalid in [
            {"Host": "example.net"},
            {"Cookie": "other=secret"},
            {"Origin": "https://example.net"},
            {"X-App-Token": "expired"},
        ]:
            headers = {"Cookie": "memyself_session=session", "X-App-Token": "csrf", **invalid}
            req = self.request("/api/action", **headers)
            req.do_POST()
            req.output.assert_called_once_with(403)
            req.server.engine.action.assert_not_called()

    def test_state_requires_session_and_launch_ticket_is_single_use(self):
        req = self.request("/api/state")
        req.do_GET()
        req.output.assert_called_once_with(403)
        req.path = "/launch/ticket"
        req.do_GET()
        self.assertEqual(req.output.call_args.args[0], 303)
        req.do_GET()
        self.assertEqual(req.output.call_args.args[0], 403)

    def test_valid_mutation_is_handled_once(self):
        body = b'{"op":"sync"}'
        req = self.request(
            "/api/action",
            Cookie="memyself_session=session",
            **{"X-App-Token": "csrf", "Content-Length": str(len(body))},
        )
        req.rfile = BytesIO(body)
        req.server.engine.action.return_value = None
        req.do_POST()
        req.server.engine.action.assert_called_once_with({"op": "sync"})
        self.assertEqual(req.output.call_args.args[0], 200)

    def test_browser_folder_selection_only_returns_the_choice(self):
        body = b'{"op":"choose_repository"}'
        for selection, expected in [(b"/tmp/A project/\n", b"/tmp/A project"), (b"\n", b'""')]:
            req = self.request(
                "/api/action",
                Cookie="memyself_session=session",
                **{"X-App-Token": "csrf", "Content-Length": str(len(body))},
            )
            req.rfile = BytesIO(body)
            with patch("server.sys.platform", "darwin"), patch("server.subprocess.run") as picker:
                picker.return_value.stdout = selection
                req.do_POST()
            picker.assert_called_once()
            self.assertEqual(req.output.call_args.args[0], 200)
            self.assertIn(expected, req.output.call_args.args[1])
            req.server.engine.action.assert_not_called()
