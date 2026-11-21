"""GitHub.com authorization for an explicit desktop connection attempt."""

import os
import re
import shutil
import signal
import subprocess
import threading
import time


DEVICE_URL = "https://github.com/login/device"
_DEVICE_CODE = re.compile(rb"(?m)^!?\s*First copy your one-time code: ([A-Z0-9]{4}-[A-Z0-9]{4})\s*$")
_TOKEN_ARGS = ["auth", "token", "--hostname", "github.com"]
_RECOVERY = "Try again or use manual setup with a GitHub personal access token."


def _run(command, env, cancel, deadline, progress=None):
    if cancel.is_set():
        raise RuntimeError("GitHub sign-in was cancelled.")
    try:
        proc = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if progress else subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise RuntimeError("Could not start GitHub CLI. " + _RECOVERY) from None
    try:
        shown = False
        while True:
            try:
                output, prompts = proc.communicate(timeout=0.2)
                finished = True
            except subprocess.TimeoutExpired as pending:
                prompts = pending.stderr
                finished = False
            if cancel.is_set():
                raise RuntimeError("GitHub sign-in was cancelled.")
            if time.monotonic() >= deadline:
                raise RuntimeError("GitHub sign-in timed out. " + _RECOVERY)
            if progress and prompts and not shown:
                code = _DEVICE_CODE.search(prompts)
                if code:
                    progress(
                        url=DEVICE_URL,
                        code=code[1].decode("ascii"),
                        message="Enter this one-time code on GitHub to finish signing in.",
                    )
                    shown = True
            if finished:
                return proc.returncode, output.decode("utf-8", errors="replace").strip()
    finally:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()


def _cli():
    env = {
        k: os.environ[k] for k in ("HOME", "TMPDIR", "GH_CONFIG_DIR", "XDG_CONFIG_HOME") if k in os.environ
    }
    env.update(
        PATH=os.pathsep.join(
            (os.defpath, "/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", ""))
        ),
        GH_PROMPT_DISABLED="1",
        GH_NO_UPDATE_NOTIFIER="1",
        GH_TELEMETRY="0",
        NO_COLOR="1",
    )
    gh = shutil.which("gh", path=env["PATH"])
    if not gh:
        raise RuntimeError(
            "Install GitHub CLI from cli.github.com or use manual setup with a personal access token."
        )
    return gh, env


def token_for(login):
    gh, env = _cli()
    status, token = _run([gh, *_TOKEN_ARGS, "--user", login], env, threading.Event(), time.monotonic() + 20)
    if status or not re.fullmatch(r"[!-~]+", token):
        raise RuntimeError("Sign in to the connected account with gh auth login, then sync again.")
    return token


def active_login():
    gh, env = _cli()
    return _login(gh, env, threading.Event(), time.monotonic() + 20)


def _login(gh, env, cancel_event, deadline):
    status, account = _run(
        [gh, "api", "--hostname", "github.com", "--method", "GET", "user", "--jq", ".login"],
        env, cancel_event, deadline,
    )
    if status or not re.fullmatch(r"[A-Za-z0-9_-]+", account):
        raise RuntimeError("Could not verify the GitHub account. Check gh auth status and retry.")
    return account


def authorize(cancel_event, progress):
    gh, env = _cli()
    deadline = time.monotonic() + 600
    status, token = _run([gh, *_TOKEN_ARGS], env, cancel_event, deadline)
    if status or not token:
        progress(message="Requesting a browser sign-in code from GitHub.")
        status, _ = _run(
            [
                gh,
                "auth",
                "login",
                "--web",
                "--hostname",
                "github.com",
                "--git-protocol",
                "https",
                "--skip-ssh-key",
            ],
            env,
            cancel_event,
            deadline,
            progress,
        )
        if status:
            raise RuntimeError("GitHub browser sign-in did not finish. " + _RECOVERY)
    progress(message="Verifying your GitHub CLI account.")
    return {"account": _login(gh, env, cancel_event, deadline), "options": []}
