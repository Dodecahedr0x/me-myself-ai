"""Provider-backed milestones for a reported bug. Every logical write has a preview."""

import difflib
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from connectors import Rejected


def repo_name(conn):
    return conn["config"]["repository"]


def issue_number(event, conn):
    if not event:
        return None
    context = event["raw"].get("context", {})
    if (
        context.get("repository", "").casefold() == repo_name(conn).casefold()
        and context.get("is_pull_request") is False
        and isinstance(context.get("number"), int)
    ):
        return context["number"]
    repo = re.escape(repo_name(conn))
    pattern = r"https://github\.com/" + repo + r"/issues/([1-9]\d*)"
    url = event["raw"].get("url", "")
    primary = re.fullmatch(pattern + r"/?(?:#[-\w]+)?", url, re.I)
    if primary:
        return int(primary[1])
    matches = set(re.findall(pattern + r"(?=$|[\s#)>,.])", event["raw"].get("body", ""), re.I))
    if len(matches) > 1:
        raise ValueError("Which linked GitHub issue records this bug?")
    return int(matches.pop()) if matches else None


def action(kind, account, destination, data, preview):
    return dict(
        operation=kind,
        account=account,
        destination=destination,
        payload=data,
        draft=preview,
        categories=["bug", kind],
        status="review",
        commitments=[],
    )


def _issue(conn, number, account, api):
    issue = api(conn, "GET", f"issues/{number}")
    if issue["state"] != "open" or "pull_request" in issue:
        raise Rejected("The linked bug must have an open issue.")
    owners = {a["login"].casefold() for a in issue.get("assignees", [])}
    return not owners or account.casefold() in owners


class CheckoutAccessError(ValueError):
    pass


def _git(worktree, *args):
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", worktree, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
    )
    if result.returncode:
        if any(message in result.stderr.lower() for message in
               (b"permission denied", b"operation not permitted", b"cannot change to")):
            raise CheckoutAccessError(f"Git cannot access {worktree}. Check that the folder exists and allow MeMyselfAI to access it, then retry.")
        raise Rejected("Could not verify the local worktree and its origin.")
    return result.stdout


def _github_repository(origin):
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([\w.-]+/[\w.-]+?)(?:\.git)?/?",
        origin,
        re.I | re.ASCII,
    )
    return match[1].casefold() if match else ""


def checkout_repository(path, *, report_access=False):
    if not path:
        return ""
    try:
        origin = _git(path, "remote", "get-url", "origin").decode().strip()
    except CheckoutAccessError:
        if report_access:
            raise
        return ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        if report_access:
            raise CheckoutAccessError(f"Git could not read the registered checkout at {path}. Check Git and allow any pending macOS folder access request, then retry.") from exc
        return ""
    except (Rejected, UnicodeError):
        return ""
    return _github_repository(origin)


def checkout_folders(name, roots, excluded):
    """Probe exact folder names in up to three levels of local project collections."""
    if not re.fullmatch(r"[\w.-]+", name, re.ASCII) or name in {".", ".."}:
        return []
    pending = [(Path(path), 0) for path in roots]
    seen, found = set(), set()
    for folder, depth in pending:
        if folder in seen or folder.is_symlink() or excluded(folder):
            continue
        seen.add(folder)
        if len(seen) > 4096:
            break
        try:
            checkout = folder if folder.name.casefold() == name.casefold() else folder / name
            if (not checkout.is_symlink() and not excluded(checkout) and
                    (checkout / ".git").exists() and not (checkout / ".git").is_symlink()):
                found.add(str(checkout))
            if depth >= 2 or (folder / ".git").exists():
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if (not entry.name.startswith(".") and entry.name not in {"node_modules", "vendor", "target", "build"}
                            and not entry.name.endswith((".app", ".bundle", ".photoslibrary"))
                            and entry.is_dir(follow_symlinks=False) and len(pending) < 4096):
                        pending.append((Path(entry.path), depth + 1))
        except OSError:
            continue
    return sorted(found)


def _remote_base(conn, api):
    info = api(conn, "GET", "")
    if info["full_name"].casefold() != repo_name(conn).casefold():
        raise Rejected("GitHub returned a different repository.")
    base = info["default_branch"]
    sha = api(conn, "GET", "git/ref/heads/" + base)["object"]["sha"]
    return base, sha


def _check_moved_base(conn, api, head, sha, files):
    """Allow a fix prepared on an older default branch revision only when the
    newer commits leave every edited path untouched, since edits carry full contents."""
    try:
        diff = api(conn, "GET", f"compare/{head}...{sha}")
        status, changed = diff["status"], diff.get("files") or []
        touched = {f["filename"] for f in changed} | {f["previous_filename"] for f in changed if f.get("previous_filename")}
    except Exception:
        raise Rejected("The worktree does not start at a published revision of the default branch.") from None
    if status != "ahead" or len(changed) >= 300:
        raise Rejected("The worktree does not start at a published revision of the default branch.")
    edited = {f["path"] for f in files}
    clash = sorted(p for p in touched if p in edited or any(p.startswith(e + "/") or e.startswith(p + "/") for e in edited))
    if clash:
        raise Rejected("The default branch changed files this fix edits since the task started: "
                       + ", ".join(clash[:5]) + ". Ask the agent to update the worktree to the current default branch and redo those edits.")


DIFF_LINES = 400  # per file; the full proposed content stays in the preview beside it


def _file_diff(worktree, head, item, tracked):
    """A unified diff of one proposed replacement against the file at the base revision."""
    path = item["path"]
    entry = dict(path=path, mode=item["mode"], status="modified" if tracked else "added",
                 additions=0, deletions=0, diff=[], truncated=False)
    before = ""
    if tracked:
        try:
            before = _git(worktree, "cat-file", "blob", f"{head}:{path}").decode()
        except UnicodeDecodeError:
            return dict(entry, status="binary")
    lines = []
    for line in difflib.unified_diff(before.splitlines(keepends=True), item["content"].splitlines(keepends=True),
                                     "a/" + path if tracked else "/dev/null", "b/" + path):
        lines += [line[:-1]] if line.endswith("\n") else [line, "\\ No newline at end of file"]
    body = lines[2:]
    entry["additions"] = sum(line.startswith("+") for line in body)
    entry["deletions"] = sum(line.startswith("-") for line in body)
    if not lines:
        entry["status"] = "unchanged"
    entry["truncated"] = len(body) > DIFF_LINES
    entry["diff"] = lines[:2 + DIFF_LINES]
    return entry


def _branch_data(t, conn, api, number):
    repo = repo_name(conn)
    origin = _git(t["worktree"], "remote", "get-url", "origin").decode().strip()
    if _github_repository(origin) != repo.casefold():
        raise Rejected("The worktree origin differs from the configured GitHub repository.")
    base, sha = _remote_base(conn, api)
    head = _git(t["worktree"], "rev-parse", "HEAD").decode().strip()
    files = t.get("changes", [])
    if head != sha:
        _check_moved_base(conn, api, head, sha, files)
    if not files:
        raise ValueError("A proposed fix is required before publishing a branch.")
    # The worktree's own HEAD: the newer base may be absent locally, and the
    # edited paths are identical in both when the base moved.
    tracked = _git(t["worktree"], "ls-tree", "-rz", head, "--", *(f["path"] for f in files))
    modes = {}
    for entry in tracked.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            modes[path.decode()] = metadata.split()[0].decode()
    prepared = []
    for edit in files:
        path = edit["path"]
        mode = modes.get(path, "100644")
        if mode not in {"100644", "100755"} or any(k.startswith(path + "/") for k in modes):
            raise Rejected("A proposed edit replaces a directory or a non-regular Git entry.")
        prepared.append(dict(path=path, mode=mode, content=edit["content"]))
    # Diffed against the worktree's HEAD: when the base moved, the edited paths are identical at both.
    diffs = [_file_diff(t["worktree"], head, item, item["path"] in modes) for item in prepared]
    return diffs, dict(
        files=prepared,
        sha=sha,
        branch="memyselfai/fix-" + t["id"][:12],
        base=base,
        issue=number,
        message=t["title"],
    )


def verify_fix(t, conn, api):
    """Return the canonical URL only for an open, linked and mergeable approved fix."""
    m = t["milestones"]
    repo = repo_name(conn)
    if m.get("repository", repo).casefold() != repo.casefold():
        raise Rejected("The configured repository changed.")
    issue = api(conn, "GET", f"issues/{m['issue']}")
    if issue["state"] != "open" or "pull_request" in issue:
        raise Rejected("The bug issue is no longer open.")
    owners = {a["login"].casefold() for a in issue.get("assignees", [])}
    if owners and m.get("github_account", "").casefold() not in owners:
        raise Rejected("Another person is assigned to the bug.")
    pr = api(conn, "GET", f"pulls/{m['pr']}")
    if pr.get("state") != "open" or pr.get("mergeable") is False:
        raise Rejected("The linked PR is closed or has conflicts.")
    for side, branch in (("head", m["branch"]), ("base", m["base"])):
        ref = pr[side]
        if (
            ref["ref"] != branch
            or (ref.get("repo") or {}).get("full_name", "").casefold() != repo.casefold()
        ):
            raise Rejected("The pull request branches differ from the reviewed fix.")
    if not m.get("revision") or pr["head"]["sha"] != m["revision"]:
        raise Rejected("The pull request no longer contains the reviewed revision.")
    if not re.search(r"(?im)^fixes\s+#" + str(m["issue"]) + r"\s*$", pr.get("body") or ""):
        raise Rejected("The PR no longer links the original issue.")
    url = f"https://github.com/{repo}/pull/{m['pr']}"
    if pr["html_url"].casefold() != url.casefold():
        raise Rejected("The pull request URL differs from the configured repository.")
    return pr["html_url"] if pr.get("mergeable") is True else None


def reconcile_branch(t, conn, api):
    """Recover an uncertain ref write only if its parent and full tree match the preview."""
    p, repo = t["payload"], repo_name(conn)
    if t["destination"] != repo + "/" + p["branch"]:
        raise Rejected("The repository differs from the reviewed destination.")
    sha = api(conn, "GET", "git/ref/heads/" + p["branch"])["object"]["sha"]
    revision = api(conn, "GET", "git/commits/" + sha)
    if [item["sha"] for item in revision["parents"]] != [p["sha"]] or revision["message"] != p["message"]:
        raise Rejected("The remote branch commit differs from the reviewed commit.")
    base = api(conn, "GET", "git/commits/" + p["sha"])

    def entries(tree):
        result = api(conn, "GET", "git/trees/" + tree, {"recursive": "1"})
        if result.get("truncated"):
            raise Rejected("GitHub truncated the tree needed for verification.")
        return {
            item["path"]: (item["mode"], item["sha"]) for item in result["tree"] if item["type"] != "tree"
        }

    expected = entries(base["tree"]["sha"])
    for item in p["files"]:
        content = item["content"].encode()
        blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        expected[item["path"]] = (item["mode"], blob)
    if entries(revision["tree"]["sha"]) != expected:
        raise Rejected("The published files differ from the reviewed branch.")
    return dict(
        branch=p["branch"],
        base=p["base"],
        issue=p["issue"],
        revision=sha,
        repository=repo,
        github_account=t["account"],
    )


def reporter_message(event, url):
    raw = event["raw"]
    context = raw["context"]
    author = context.get("author", "")
    salutation = ""
    if "repository" in context:
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\[bot\])?", author):
            raise Rejected("The original GitHub reporter could not be identified.")
        salutation = "@" + author + " "
    if "channel" in context:
        if not re.fullmatch(r"[UW][A-Z0-9]+", author):
            raise Rejected("The original Slack reporter could not be identified.")
        salutation = "<@" + author + "> "
    return salutation + "The fix is ready for review: " + url


def verification(ran, unverified=()):
    """The pull request's Verification section: the commands the app ran for this fix with their exit codes,
    as the app observed them, and the checks the agent could not run anywhere it could reach.
    See "Checks this machine cannot run" in task-execution.md."""
    lines = ["", "", "## Verification", ""]
    for r in ran or []:
        status = "timed out" if r.get("timed_out") else f"exit {r.get('exit_code')}"
        command = " ".join(str(r.get("command", "")).split()).replace("`", "'")
        lines.append(f"- `{command}` on {r.get('platform', 'the agent machine')}: {status}")
    if not ran:
        lines.append("- The agent ran no commands while preparing this fix.")
    if unverified:
        lines += ["", "Not verified yet:", ""] + ["- " + " ".join(str(item).split()) for item in unverified]
    lines += ["", "Recorded by MeMyselfAI."]
    return "\n".join(lines)


def prepare(t, event, conn, api, identity):
    repo, account = repo_name(conn), identity(conn)
    m = t.get("milestones", {})
    if m.get("repository", repo).casefold() != repo.casefold():
        raise Rejected("The repository for this bug has changed.")
    number = m.get("issue") or issue_number(event, conn)
    if not number:
        data = dict(title=t["title"], body=t["body"])
        return action("create issue", account, repo, data, t["title"] + "\n\n" + t["body"])
    if not _issue(conn, number, account, api):
        return dict(status="skipped", reason="Another person is assigned to this issue.")
    if not m.get("branch"):
        diffs, data = _branch_data(t, conn, api, number)
        preview = json.dumps(dict(data, repository=repo, diffs=diffs), indent=2, ensure_ascii=False)
        return action("publish branch", account, repo + "/" + data["branch"], data, preview)
    if not m.get("pr"):
        data = dict(
            head=m["branch"], base=m["base"], title=t["title"],
            body=f"Fixes #{number}\n\n" + t["result"]
            + verification(t.get("verification"), t.get("unverified") or ()),
        )
        return action(
            "create pull request", account, repo, data, json.dumps(data, indent=2, ensure_ascii=False)
        )
    if verify_fix(t, conn, api) is None:
        return dict(
            status="scheduled", due=time.time() + 45, reason="Waiting for GitHub to calculate mergeability"
        )
    return None


def review_patch(t, conn, api):
    """Read a complete text patch at one base and head for the review agent."""
    m = t["milestones"]
    endpoint = f"pulls/{m['pr']}"
    pr = api(conn, "GET", endpoint)
    count = pr.get("changed_files", 0)
    base = pr["base"].get("sha")
    if pr["head"]["sha"] != m["revision"] or not base or not 0 < count <= 100:
        raise Rejected("The PR revision or complete set of changed files is unavailable for review.")
    files = api(conn, "GET", endpoint + "/files", {"per_page": 100})
    if len(files) != count:
        raise Rejected("GitHub returned an incomplete list of changed files.")
    patch = []
    for item in files:
        diff = item.get("patch") or ""
        lines = diff.splitlines()
        if (not diff or sum(line.startswith("+") for line in lines) != item["additions"]
                or sum(line.startswith("-") for line in lines) != item["deletions"]):
            raise Rejected("The full text patch is unavailable for " + item["filename"])
        patch.append({"file": item["filename"], "status": item["status"], "patch": diff})
    if len(json.dumps(patch).encode()) > 96 * 1024:
        raise Rejected("The patch exceeds the reviewer's context limit.")
    current = api(conn, "GET", endpoint)
    if current["head"]["sha"] != m["revision"] or current["base"]["sha"] != base:
        raise Rejected("The pull request changed while its patch was being read.")
    return patch, base


def review_receipt(t, result):
    review_id = result.get("id")
    expected = f"{t['destination']}#pullrequestreview-{review_id}"
    if (type(review_id) is not int or review_id < 1 or result.get("html_url") != expected
            or result.get("commit_id") != t["payload"]["commit_id"] or result.get("state") != "COMMENTED"
            or result.get("body") != t["draft"]
            or (result.get("user") or {}).get("login", "").casefold() != t["account"].casefold()):
        raise ValueError("Verify whether GitHub recorded the approved review on this commit.")
    return expected


def send(t, conn, api, identity):
    p, op, repo = t["payload"], t["operation"], repo_name(conn)
    if op not in ("create issue", "create pull request", "publish branch", "submit PR review"):
        raise Rejected("Unknown GitHub operation.")
    target = repo + "/" + p["branch"] if op == "publish branch" else repo
    if op == "submit PR review":
        target = f"https://github.com/{repo}/pull/{t['milestones']['pr']}"
    try:
        if identity(conn).casefold() != t["account"].casefold() or t["destination"] != target:
            raise Rejected("The authorized GitHub account or repository has changed.")
        m = t.get("milestones", {})
        if m.get("repository", repo).casefold() != repo.casefold():
            raise Rejected("The configured repository changed.")
        if op != "create issue" and not _issue(conn, p.get("issue") or m["issue"], t["account"], api):
            raise Rejected("Another person is assigned to this issue.")
        if op == "publish branch":
            if _remote_base(conn, api) != (p["base"], p["sha"]):
                raise Rejected("The default branch changed after preparation.")
            commit = api(conn, "GET", f"git/commits/{p['sha']}")
        if op == "create pull request":
            ref = api(conn, "GET", "git/ref/heads/" + p["head"])
            if not m.get("revision") or ref["object"]["sha"] != m["revision"]:
                raise Rejected("The published fix branch changed after review.")
        if op == "submit PR review":
            if (verify_fix(t, conn, api) != target or p.get("event") != "COMMENT"
                    or p.get("body") != t["draft"] or p.get("commit_id") != m["revision"]):
                raise Rejected("The reviewed patch or comment differs from the approved preview.")
            current = api(conn, "GET", f"pulls/{m['pr']}")
            if current["head"]["sha"] != m["revision"] or current["base"]["sha"] != t["review_base"]:
                raise Rejected("The pull request changed after review preparation.")
    except Rejected:
        raise
    except Exception:
        raise Rejected("Could not verify the GitHub account, issue and branch before delivery.") from None
    if op == "create issue":
        result = api(conn, "POST", "issues", p)
        return result["html_url"], dict(issue=result["number"], repository=repo)
    if op == "submit PR review":
        result = api(conn, "POST", f"pulls/{m['pr']}/reviews", p)
        return review_receipt(t, result), {}
    if op == "publish branch":
        entries = [dict(f, type="blob") for f in p["files"]]
        tree = api(conn, "POST", "git/trees", {"base_tree": commit["tree"]["sha"], "tree": entries})
        revision = api(
            conn,
            "POST",
            "git/commits",
            {"message": p["message"], "tree": tree["sha"], "parents": [p["sha"]]},
        )
        ref = api(conn, "POST", "git/refs", {"ref": "refs/heads/" + p["branch"], "sha": revision["sha"]})
        return ref["ref"], dict(
            branch=p["branch"],
            base=p["base"],
            issue=p["issue"],
            revision=revision["sha"],
            repository=repo,
            github_account=t["account"],
        )
    result = api(conn, "POST", "pulls", p)
    return result["html_url"], dict(pr=result["number"], url=result["html_url"], repository=repo)
