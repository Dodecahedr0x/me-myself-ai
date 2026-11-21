"""Run installed workflow fixtures in a separate process with temporary user data."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import bugflow
import connectors


REPOSITORY = "Dodecahedr0x/me-myself-ai"
PROJECT = "MeMyselfAI"
FIXTURES = ("test_http", "test_bug_integration")


def run():
    with tempfile.TemporaryDirectory(prefix="memyselfai-check-") as home:
        process = subprocess.run(
            [sys.executable, "-E", "-s", "-B", str(Path(__file__).resolve())],
            cwd=home,
            env={"HOME": home, "TMPDIR": home, "PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1"},
            capture_output=True,
            timeout=120,
        )
        if process.returncode:
            raise RuntimeError("The installed workflow checks could not run. Rebuild the app and retry.")
        return json.loads(process.stdout)


def record_results(engine, task, checks):
    findings = []
    for failure in checks["failures"]:
        test = failure["test"]
        case = next((row for row in engine.db.rows("tasks")
                     if row.get("failed_check") == test
                     and row["status"] not in {"done", "dismissed", "skipped"}), None)
        if not case:
            case = engine.new_task(
                "Workflow check failed: " + test.split(".")[-1], task["project"],
                f"Run {test} from the app's workflow checks.\nObserved: {failure['diagnostic']}",
                source="App testing", special="app_finding", kind="bug",
                parent=task["id"], failed_check=test,
            )
        if case["id"] not in findings:
            findings.append(case["id"])
    return dict(status="done", findings=findings,
                result=f"{checks['count']} checks ran, {len(checks['failures'])} failed.")


def prepare_finding(engine, task):
    sources = [c for c in engine.db.rows("connections")
               if c["kind"] == "github" and not c.get("unavailable")
               and c["config"].get("repository", "").casefold() == REPOSITORY.casefold()
               and (not task.get("github") or c["id"] == task["github"])]
    if len(sources) != 1:
        raise ValueError(f"Connect one GitHub account for {REPOSITORY}, then retry this finding.")
    github = sources[0]
    milestones = task.get("milestones", {})
    if engine.rule(task["project"], "bug") == "user" and not task["feedback"]:
        return engine.decision(task, "How should I handle this app finding?")
    if not milestones.get("issue"):
        body = task["body"]
        if task["feedback"]:
            response = engine.infer("task", {
                "instruction": "Revise the proposed issue body using the maintainer's feedback. Return the exact body as a draft.",
                "task": {"title": task["title"], "body": body, "feedback": task["feedback"]},
            }, task["project"], task["id"])
            if response["outcome"] == "question":
                return engine.decision(task, response["text"])
            if response["outcome"] != "draft":
                raise ValueError("The agent did not return a revised issue body. Retry preparation.")
            body = response["text"]
        change = bugflow.prepare(dict(task, body=body), None, github, connectors.github_request, connectors.github_identity)
        return dict(change, github=github["id"], question="", new_feedback="")
    if not milestones.get("pr"):
        paths = engine.repository_candidates(task, github)
        if task.get("repository"):
            paths = [p for p in paths if p == task["repository"]]
        if len(paths) != 1:
            raise ValueError(f"Link one checkout of {REPOSITORY} under Projects → {task['project']}, then retry.")
        return engine.prepare(dict(task, repository=paths[0], github=github["id"]))

    change = bugflow.prepare(task, None, github, connectors.github_request, connectors.github_identity)
    if change:
        return change
    patch, base = bugflow.review_patch(task, github, connectors.github_request)
    response = engine.infer("task", {
        "instruction": "Review the pull request patch against the reported behavior. Return a draft comment with concrete findings and testing gaps, or state that you found none. Cite files. Never claim tests ran without evidence.",
        "task": {"title": task["title"], "body": task["body"], "feedback": task["feedback"]},
        "files_changed": patch,
    }, task["project"], task["id"])
    if response["outcome"] == "question":
        return engine.decision(task, response["text"])
    if response["outcome"] != "draft":
        raise ValueError("The reviewer did not return a review comment. Retry preparation.")
    payload = dict(event="COMMENT", commit_id=milestones["revision"], body=response["text"])
    change = bugflow.action("submit PR review", connectors.github_identity(github),
                            milestones["url"], payload, response["text"])
    return dict(change, review_base=base, complete_after_send=True, question="", new_feedback="")


if __name__ == "__main__":
    from importlib import import_module

    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
    suite = unittest.TestSuite()
    for name in FIXTURES:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(import_module(name)))
    result = unittest.TestResult()
    suite.run(result)
    if not result.testsRun or result.skipped:
        sys.exit(1)
    failures = []
    for case, detail in result.errors + result.failures:
        diagnostic = detail.strip().splitlines()[-1].replace(os.environ["HOME"], "<temporary folder>")
        failures.append({"test": case.id(), "diagnostic": diagnostic[:1200]})
    print(json.dumps({"count": result.testsRun, "failures": failures}))
