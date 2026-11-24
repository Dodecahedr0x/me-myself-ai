# User stories

| ID | As the dashboard user, I want to… | Example outcome |
| --- | --- | --- |
| US-01 | Connect email, Slack, and GitHub to my workspace, choose what to follow across projects, and reconnect when access expires. | I connect my personal and work Google accounts through the same browser chooser. From Home I can manage connections and open a separate activity log for each. Connecting Slack enrolls my accessible channels automatically. [Account connections](specs/connectors.md) |
| US-02 | See tasks from incoming requests and understand what triggered them. | I can distinguish two review requests with the same title by their project and GitHub repository in the queue, then open either to see the pull request and why it surfaced. [Dashboard](specs/dashboard.md) |
| US-03 | Discover useful follow-ups even when no new message arrives. | The dashboard suggests revisiting an unanswered email and links the thread. |
| US-04 | Have the agent choose how to handle a task and get started on its own. | A reply is ready when I open the dashboard. I can correct it, add context, or dismiss it. |
| US-05 | Let the agent prepare work in dry mode and decide exactly what leaves my machine as a post or update. | I can review and confirm one Slack reply while an email draft stays pending. |
| US-06 | Eventually hand off familiar work completely. | With authority to handle routine status requests, the agent answers one, checks delivery, and records the result while I am away. |
| US-07 | Have a dedicated agent work on each task. | A delegated fix receives its parent's instructions and a private clone where it can run the project’s tests. The parent resumes when the fix supplies new evidence. [Task execution](specs/task-execution.md) |
| US-08 | Rely on promises I make or delegate being picked up later. | A sent reply promising Friday's figures creates a linked follow-up. |
| US-09 | See whether a task achieved its actual outcome. | A bug stays open on the dashboard until its issue, mergeable linked PR, and reporter notification are verified. |
| US-10 | Define how each project handles each type of decision. | One project allows routine replies while requiring review for promises. |
| US-11 | Teach my writing style through feedback and past posts. | The agent asks what an ambiguous edit means and future agents apply my approved preference. |
| US-12 | Decide when I trust autonomous delivery. | I can inspect missed tasks, work quality, and style fidelity before changing a project's rules. |
| US-13 | Leave work with the person already tackling it. | The dashboard shows why the agent skipped a claimed bug. |
| US-14 | Resolve blockers in dry mode while independent work continues. | I can pick a revised date directly in the queue, open the task to compare what each choice means, or restore folder access after missing a macOS prompt. [Dashboard](specs/dashboard.md) |
| US-15 | Review everything agents do and report omissions in the app. | Reporting a missed Slack request starts an agent that recovers outstanding related requests. |
| US-16 | Have agents improve through skills and other Markdown files. | A revised discovery skill catches a similar request on the next run using the same app version. |
| US-17 | Delegate general computer work in Global and group related repositories under a named project. | A GitHub issue finds its local project from the checkout’s origin. One release can include both client and service repositories. An unrelated email stays in Global. [Task execution](specs/task-execution.md) |
| US-18 | See suggested work grounded in my connected apps and activity on my Mac. | A follow-up from a Claude or ChatGPT conversation appears with its supporting exchange. A changed budget sheet and a Slack request can also support a suggestion. [Activity suggestions](specs/activity-suggestions.md) |
| US-19 | Use MeMyselfAI to improve itself. | I run the app's workflow checks or report something I observed. I can follow a finding from its test evidence through its GitHub issue, fix and pull request review in Tasks. [Self-improvement](specs/self-improvement.md) |
| US-20 | Manage what the agent works on. | I can read a formatted issue description, exclude projects or repositories from agent work, and close a task when I have handled it myself. [Dashboard](specs/dashboard.md) · [Task workflow](specs/task-workflow.md) |

Workspace-wide Slack and Google account connections extend US-01 and US-03. [Workspace accounts](specs/account-wide-connections.md)

US-01 through US-05 are covered by the [task workflow](specs/task-workflow.md), US-07 through US-09 by [task agents and completion](specs/task-execution.md), and US-06 and US-10 through US-12 by [decision rules, writing style, and trust](specs/delegation-and-style.md).

US-13 extends the [task workflow](specs/task-workflow.md). US-14 extends [decision handling](specs/delegation-and-style.md). US-15 and US-16 are specified in [reviewing activity and learning from missed work](specs/activity-and-improvement.md).

The [desktop dashboard spec](specs/dashboard.md) maps these workflows to their user-facing controls.
