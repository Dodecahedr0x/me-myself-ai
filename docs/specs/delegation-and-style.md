# Decision rules, writing style, and trust

**Status** — Draft. Covers [US-06, US-10 through US-12, and US-14](../user-stories.md).

## How the user delegates decisions

Classify each decision by project and type. A single task can involve several decisions. For example, a reply giving a status and promising a Friday fix involves both a status update and a commitment. In the dashboard, the user can configure each project and decision type to allow autonomous handling, require review, or leave the decision to the user. Display the categories and rules applied to each proposed action.

Dry mode takes precedence and requires confirmation for every external write. Outside dry mode, send autonomously only when the rules authorize the full action, including its wording and every commitment it makes. If any applicable rule needs review, or the project or type is unclear, surface the decision. Recheck current rules at dispatch so a pending draft respects changes to the user's settings.

## Learning the user's writing style

Whenever the app receives user feedback or reads previous user-authored posts, an agent examines that material for reusable style guidance. Save findings in a local, human-readable style file that subsequent task agents consult when drafting posts. For example, “Please lead with the answer in review comments” can become scoped guidance for GitHub reviews. A correction to a delivery date may supply task context without implying a writing preference.

Both explicit standing instructions and preferences inferred from past writing enter the same review flow. Show the proposed wording, its scope, and what prompted the suggestion. Only the user's approval makes a change part of the shared style that agents apply to future drafts. Preserve concurrent suggestions from different task agents. Attribute examples to the actual author so quoted replies or generated drafts do not silently become evidence of the user's voice.

Whenever clarification could improve a decision, deliverable, or style inference, ask a specific question and explain what the answer would change. Continue independent work while awaiting a reply. For example, after “less formal here,” ask whether that applies to this thread, this project, or all Slack replies.

## Questions and changed commitments

Route every question to a decision agent with the project, decision type, relevant context, and steps that depend on its answer. In dry mode that agent asks the user in the dashboard. Dependent steps pause until the user answers, including steps in other tasks that need the same answer. Independent steps continue, even within a partially blocked task. Record the answer with the decision and resume each affected step once its remaining dependencies are satisfied. Outside dry mode, the decision agent follows the project's rules for that decision type.

For example, a dependency slips and the promised Friday delivery is now at risk. The task agent raises a decision about whether to update the recipient. In dry mode the decision agent asks the user what to do. The existing commitment remains visible. Work on unaffected parts can proceed. If the user chooses to propose Monday, the agent drafts the update and presents the exact outbound message for dry-mode confirmation.

## Leaving dry mode

The user decides when to enable autonomous delivery after reviewing evidence in three areas. For **coverage**, compare discovered and acted-on tasks with relevant activity the user has reviewed, including missed tasks the user identifies. For **quality**, show deliverables, completion evidence, and substantive corrections. For **style**, compare outgoing drafts with the approved style file and show wording edits the user made. Make this evidence available by project and decision type so the user can grant authority where performance warrants it. A quiet queue or a streak of unchanged drafts alone cannot establish coverage. Decision rules lists [handoff candidates](context-and-schedules.md#handoff-candidates) with their delivery and correction counts.

## Acceptance criteria

- The same type of status update can run autonomously in one project and require review in another. Switching dry mode on causes both to wait before posting.
- A project permits routine replies but requires review for commitments. Adding “I'll fix it Friday” to an otherwise routine reply routes the whole message to review. When authorized and delivered, it creates the [follow-up task](task-execution.md).
- Test both an explicit instruction (“lead with the answer in all my emails”) and the same preference inferred from past emails. In each case the approved style stays unchanged while review is pending. After approval, a fresh task agent applies the rule to its next email draft.
- A user can identify a missed task, inspect work that needed correction, and compare drafts with the approved style before enabling autonomous handling. Reaching a metric alone never changes delivery permissions.
- In dry mode, both a question about tone and a question about a slipped dependency reach the user through a decision agent. The affected wording and revised promise wait. An independent test run proceeds. Answering the tone question unblocks only the steps that depended on that answer, and sends no message.
