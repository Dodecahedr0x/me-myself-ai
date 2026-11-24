"""VPS setup over SSH against a fake ssh executable. See docs/specs/guided-setup.md."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vps_setup  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

FAKE_SSH = r'''#!PYTHON
import json, os, socket, sys, threading
scenario = json.load(open(os.environ["FAKE_SSH_SCENARIO"]))
args = sys.argv[1:]
def record(**extra):
    with open(scenario["log"], "a") as f:
        f.write(json.dumps(dict(argv=args, **extra)) + "\n")
if "-G" in args:
    print("hostname " + scenario.get("hostname", "203.0.113.5")); print("user deploy"); print("port 22"); sys.exit(0)
if "-O" in args:
    record(); sys.exit(0)
if "-W" in args:
    record()
    port = int(args[args.index("-W") + 1].rsplit(":", 1)[1])
    sock = socket.create_connection(("127.0.0.1", scenario["dashboard_port"] or port))
    def up():
        while True:
            data = os.read(0, 65536)
            if not data:
                sock.shutdown(socket.SHUT_WR); return
            sock.sendall(data)
    threading.Thread(target=up, daemon=True).start()
    while True:
        data = sock.recv(65536)
        if not data:
            sys.exit(0)
        os.write(1, data)
if scenario.get("ssh_error"):
    sys.stderr.write(scenario["ssh_error"] + "\n"); sys.exit(255)
command = args[-1]
stdin = sys.stdin.buffer.read()
if command.startswith("sudo -S"):
    line = stdin.split(b"\n", 1)[0].decode()
    record(command=command, password_ok=line == scenario.get("password"))
    if line != scenario.get("password"):
        sys.stderr.write("Sorry, try again.\n"); sys.exit(1)
else:
    record(command=command, stdin_bytes=len(stdin))
if "uname -s" in command:
    print("os=" + scenario.get("os", "Linux")); print("uid=" + scenario.get("uid", "1000"))
    print("systemd=1"); print("sudo=1")
    if scenario.get("nopasswd"): print("nopasswd=1")
    sys.exit(0)
if 'printf %s "$HOME"' in command:
    sys.stdout.write("/home/deploy"); sys.exit(0)
if "launch-url" in command:
    print(open(scenario["launch_file"]).read().strip()); sys.exit(0)
if "install-vps.sh" in command:
    if scenario.get("install_fails"):
        sys.stderr.write("E: Unable to locate package nodejs\n"); sys.exit(100)
    print("ufw=opened"); sys.exit(0)
if "npm install" in command:
    print("present" if scenario.get("agent_present") else ""); sys.exit(0)
sys.exit(0)
'''


class FakePeers:
    def __init__(self, peers=(), fail=None):
        self.items = list(peers)
        self.fail = fail
        self.joined = []

    def peers(self):
        return self.items

    def join(self, address, code):
        if self.fail:
            raise self.fail
        self.joined.append((address, code))
        return dict(name="vps", platform="linux")


class VpsSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ssh = self.dir / "ssh"
        self.ssh.write_text(FAKE_SSH.replace("PYTHON", sys.executable))
        self.ssh.chmod(self.ssh.stat().st_mode | stat.S_IEXEC)
        self.log_file = self.dir / "calls.jsonl"
        self.scenario = dict(log=str(self.log_file), launch_file=str(self.dir / "launch"), dashboard_port=0)
        self.scenario_file = self.dir / "scenario.json"
        self.previous = os.environ.get("FAKE_SSH_SCENARIO")
        os.environ["FAKE_SSH_SCENARIO"] = str(self.scenario_file)
        self.logs = []

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("FAKE_SSH_SCENARIO", None)
        else:
            os.environ["FAKE_SSH_SCENARIO"] = self.previous
        self.tmp.cleanup()

    def setup(self, peers, **scenario):
        self.scenario.update(scenario)
        self.scenario_file.write_text(json.dumps(self.scenario))
        return vps_setup.VpsSetup(peers, lambda: {"provider": "claude"}, lambda kind, detail: self.logs.append((kind, detail)),
                                  ROOT, ssh=str(self.ssh), home=self.dir)

    def wait(self, setup, statuses=("done", "failed", "cancelled"), seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            run = setup.state()["run"]
            if run and run["status"] in statuses:
                return run
            time.sleep(0.05)
        self.fail(f"setup did not reach {statuses}: {setup.state()['run']}")

    def calls(self):
        return [json.loads(line) for line in self.log_file.read_text().splitlines()] if self.log_file.exists() else []

    def dashboard(self):
        """A real dashboard service, so the pairing code request goes through its session and CSRF checks."""
        import server
        from engine import Engine
        engine = Engine(self.dir / "vps-workspace")
        http = server.HTTP(engine, 0)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (http.shutdown(), http.server_close(), engine.close()))
        (self.dir / "launch").write_text(http.launch_url + "\n")
        return engine, http

    def test_hosts_from_config_and_includes_without_patterns(self):
        ssh_dir = self.dir / ".ssh"
        (ssh_dir / "conf.d").mkdir(parents=True)
        (ssh_dir / "config").write_text("Include conf.d/*\nHost *\n  ServerAliveInterval 30\n"
                                        "Host OVH-VPS builder\n  HostName 203.0.113.5\nHost web-?\nHost !bad\n")
        (ssh_dir / "conf.d" / "work").write_text("Host work-box\n  User me\nHost builder\n")
        self.assertEqual(vps_setup.ssh_hosts(self.dir), ["OVH-VPS", "builder", "work-box"])

    def test_rejects_option_like_hosts(self):
        for host in ("-oProxyCommand=x", "", "a b", "host;rm"):
            with self.assertRaises(ValueError):
                vps_setup.valid_host(host)
        self.assertEqual(vps_setup.valid_host("deploy@203.0.113.5"), "deploy@203.0.113.5")

    def test_full_run_installs_and_pairs_through_the_dashboard(self):
        vps_engine, http = self.dashboard()
        peers = FakePeers()
        setup = self.setup(peers, nopasswd=True, dashboard_port=http.server_port)
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertEqual(run["status"], "done", run)
        self.assertEqual([s["status"] for s in run["steps"]], ["done"] * len(vps_setup.STEPS))
        self.assertEqual(run["address"], "203.0.113.5:8802")
        self.assertEqual(len(peers.joined), 1)
        address, code = peers.joined[0]
        self.assertEqual(address, "203.0.113.5:8802")
        pending = vps_engine.db.get("local", "pairing")
        self.assertEqual(vps_engine.instances._norm(code), pending["code"])
        commands = [c.get("command", "") for c in self.calls()]
        order = [next(i for i, c in enumerate(commands) if key in c)
                 for key in ("uname -s", "tar -xzf", "install-vps.sh", "npm install", "is-active", "launch-url")]
        self.assertEqual(order, sorted(order))
        tar = next(c for c in self.calls() if "tar -xzf" in c.get("command", ""))
        self.assertGreater(tar["stdin_bytes"], 10_000)
        self.assertTrue(all(c.get("command", "").startswith("sudo -n") for c in self.calls()
                            if "install-vps.sh" in c.get("command", "")))
        self.assertIn("Firewall", run["steps"][2]["detail"])
        self.assertTrue(any(kind == "Server set up" for kind, _ in self.logs))

    def test_sudo_password_is_asked_retried_and_never_stored(self):
        setup = self.setup(FakePeers([dict(name="vps", address="203.0.113.5:8802", trusted=True)]),
                           password="s3cret pass")
        setup.start("OVH-VPS")
        self.wait(setup, ("needs_password",))
        setup.password("wrong")
        deadline = time.time() + 10
        while not setup.state()["run"]["password_error"] and time.time() < deadline:
            time.sleep(0.05)
        run = setup.state()["run"]
        self.assertEqual(run["status"], "needs_password")
        self.assertIn("didn't work", run["password_error"])
        setup.password("s3cret pass")
        run = self.wait(setup)
        self.assertEqual(run["status"], "done", run)
        self.assertIn("Already paired", run["steps"][-1]["detail"])
        self.assertFalse(any("launch-url" in c.get("command", "") for c in self.calls()))
        everything = json.dumps(setup.state()) + json.dumps(self.logs) + self.log_file.read_text()
        self.assertNotIn("s3cret", everything)
        self.assertFalse(any("s3cret" in " ".join(c["argv"]) for c in self.calls()))

    def test_cancel_while_waiting_for_password(self):
        setup = self.setup(FakePeers(), password="x")
        setup.start("OVH-VPS")
        self.wait(setup, ("needs_password",))
        setup.cancel()
        self.assertEqual(self.wait(setup)["status"], "cancelled")

    def test_not_linux_stops_with_docker_hint(self):
        setup = self.setup(FakePeers(), os="Darwin", nopasswd=True)
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertEqual(run["status"], "failed")
        self.assertIn("Linux with systemd", run["error"])
        self.assertEqual(run["steps"][0]["status"], "failed")

    def test_ssh_failure_explains_what_to_do(self):
        setup = self.setup(FakePeers(), ssh_error="deploy@203.0.113.5: Permission denied (publickey).")
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertIn("SSH agent", run["error"])
        setup2 = self.setup(FakePeers(), ssh_error="Host key verification failed.")
        setup2.start("OVH-VPS")
        self.assertIn("Connect once from Terminal", self.wait(setup2)["error"])
        setup3 = self.setup(FakePeers(), ssh_error="kex_exchange_identification: read: Connection reset by peer")
        setup3.start("OVH-VPS")
        self.assertIn("provider's web console", self.wait(setup3)["error"])

    def test_installer_failure_shows_its_output_and_retry_runs_again(self):
        setup = self.setup(FakePeers([dict(name="vps", address="203.0.113.5:8802", trusted=True)]),
                           nopasswd=True, install_fails=True)
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertEqual([s["status"] for s in run["steps"][:3]], ["done", "done", "failed"])
        self.assertIn("Unable to locate package", run["error"])
        self.scenario["install_fails"] = False
        self.scenario_file.write_text(json.dumps(self.scenario))
        setup.start("OVH-VPS")
        self.assertEqual(self.wait(setup)["status"], "done")

    def test_unreachable_sync_port_asks_to_open_the_firewall(self):
        _, http = self.dashboard()
        setup = self.setup(FakePeers(fail=ConnectionRefusedError()), nopasswd=True, dashboard_port=http.server_port)
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertEqual(run["status"], "failed")
        self.assertIn("Allow TCP port 8802", run["error"])

    def test_steps_record_how_long_they_took_and_the_host_is_remembered(self):
        remembered = []
        setup = self.setup(FakePeers([dict(name="vps", address="203.0.113.5:8802", trusted=True)]), nopasswd=True)
        setup.on_done = remembered.append
        setup.start("OVH-VPS")
        run = self.wait(setup)
        self.assertTrue(all(isinstance(s.get("seconds"), float) for s in run["steps"]))
        self.assertEqual(remembered, ["OVH-VPS"])
        fresh = self.setup(FakePeers(), nopasswd=True)
        self.assertIn("OVH-VPS", fresh.sign_in("OVH-VPS")["command"])
        (self.dir / ".ssh").mkdir()
        (self.dir / ".ssh" / "config").write_text("Host other\nHost OVH-VPS\n")
        self.assertEqual(fresh.host_for("203.0.113.5:8802"), "other")  # the fake resolves every host alike

    def test_sign_in_command_targets_the_service_user(self):
        setup = self.setup(FakePeers([dict(name="vps", address="203.0.113.5:8802", trusted=True)]), nopasswd=True)
        with self.assertRaises(ValueError):
            setup.sign_in()
        setup.start("OVH-VPS")
        self.wait(setup)
        result = setup.sign_in()
        self.assertEqual(result["command"], "ssh -t OVH-VPS 'sudo -u memyselfai -i claude auth login'")

    def test_firewall_opens_the_sync_port_only_where_pairing_needs_it(self):
        self.assertEqual(vps_setup.firewall_rule("100.127.118.82"),
                         ("allow in on tailscale0 to any port 8802 proto tcp", "on Tailscale only"))
        self.assertIn("tailscale0", vps_setup.firewall_rule("vps.tail1234.ts.net")[0])
        self.assertEqual(vps_setup.firewall_rule("192.168.1.48")[0], "allow from 192.168.0.0/16 to any port 8802 proto tcp")
        self.assertEqual(vps_setup.firewall_rule("203.0.113.5")[0], "allow 8802/tcp")

    def test_archive_holds_the_installer_inputs(self):
        import io
        import tarfile
        names = tarfile.open(fileobj=io.BytesIO(vps_setup.app_archive(ROOT))).getnames()
        for name in ("server.py", "engine.py", "vps_setup.py", "deploy/install-vps.sh", "ui/app.js",
                     "skills/task-interaction/SKILL.md"):
            self.assertIn(name, names)
        self.assertFalse(any("__pycache__" in n or n.startswith("tests") for n in names))


if __name__ == "__main__":
    unittest.main()


class SetupGuideTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from engine import Engine
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.engine.close)
        self.harness = {"claude": "ready", "codex": "not_installed", "hermes": "not_installed"}
        inventory = lambda refresh=False: dict(harnesses=[
            dict(id=k, label=k, state=v, sign_in=k + " login", detail="") for k, v in self.harness.items()])
        self.engine.agents.inventory = inventory
        patcher = mock.patch("github_signin.active_login", return_value="octocat")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.action({"op": "settings", "provider": "claude"})

    def steps(self):
        state = self.engine.setup_state()
        return state, {s["id"]: s for s in state["steps"]}

    def test_fresh_workspace_needs_accounts_and_offers_github_cli_login(self):
        state, steps = self.steps()
        self.assertTrue(state["first_run"])
        self.assertTrue(steps["agent"]["ready"])
        self.assertEqual(state["missing"], ["accounts"])
        self.assertEqual(steps["accounts"]["github_login"], "octocat")
        self.assertFalse(steps["server"]["ready"])
        self.assertTrue(steps["delivery"]["dry"])

    def test_agent_sign_in_needed_is_a_missing_required_step(self):
        self.harness["claude"] = "sign_in"
        state, steps = self.steps()
        self.assertEqual(state["missing"], ["agent", "accounts"])
        self.assertEqual(steps["agent"]["sign_in"], "claude login")

    def test_one_click_github_connects_the_cli_account(self):
        result = self.engine.action({"op": "setup_connect_github"})
        self.assertEqual(result["account"], "octocat")
        state, steps = self.steps()
        self.assertEqual(state["missing"], [])
        self.assertIsNone(steps["accounts"]["github_login"])
        self.assertFalse(state["first_run"])

    def test_dismiss_holds_until_another_step_breaks_and_skip_projects(self):
        self.engine.action({"op": "setup_dismiss"})
        self.assertTrue(self.engine.setup_state()["dismissed"])
        self.harness["claude"] = "sign_in"
        self.assertFalse(self.engine.setup_state()["dismissed"])
        self.engine.action({"op": "setup_skip", "step": "projects"})
        self.assertTrue(self.steps()[1]["projects"]["skipped"])
        with self.assertRaises(ValueError):
            self.engine.action({"op": "setup_skip", "step": "accounts"})

    def test_server_step_reads_the_paired_servers_task_agent(self):
        peer = dict(id="p1", name="vps", platform="linux", address="100.127.118.82:8802", trusted=True,
                    info={"capacity": {"task_ready": False}})
        self.engine.instances.peers = lambda: [peer]
        self.assertFalse(self.steps()[1]["server"]["peers"][0]["agent_ready"])
        peer["info"]["capacity"]["task_ready"] = True
        server = self.steps()[1]["server"]
        self.assertTrue(server["ready"] and server["peers"][0]["agent_ready"])

    def test_server_step_says_what_runs_where(self):
        import time as clock
        peer = dict(id="p1", name="vps", platform="linux", address="100.127.118.82:8802", trusted=True,
                    last_seen=clock.time(), state="connected",
                    info={"capacity": {"task_ready": True, "hermes_ready": False, "slots": 5, "free": 4}})
        self.engine.instances.peers = lambda: [peer]
        self.engine.action({"op": "setup_connect_github"})
        server = self.steps()[1]["server"]
        p = server["peers"][0]
        self.assertTrue(p["online"] and p["agent_ready"])
        self.assertEqual((p["slots"], p["free"], p["connections"], p["orchestrator"]), (5, 4, 0, False))
        self.assertEqual(server["placement"]["total"], 1)
        self.assertEqual(server["placement"]["local_only"], 1)  # the GitHub CLI login stays on this Mac
        peer["last_seen"] = clock.time() - 3600
        self.assertFalse(self.steps()[1]["server"]["peers"][0]["online"])

    def test_vps_actions_validate_input(self):
        with self.assertRaises(ValueError):
            self.engine.action({"op": "vps_setup", "host": "-oProxyCommand=touch /tmp/x"})
        with self.assertRaises(ValueError):
            self.engine.action({"op": "vps_password", "password": "x"})
        self.assertIn("hosts", self.engine.setup_state()["vps"])
