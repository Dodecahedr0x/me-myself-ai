---
name: task-interaction
description: Prepare work and ask the user for decisions in the MeMyselfAI task queue.
---

# Interacting with MeMyselfAI

During preparation, check the supplied task, files, receipts and earlier answers before asking for input. If a decision blocks the next step, give the decision agent the missing fact, the relevant evidence and what can proceed once the user answers.

During a decision request, use these fields for the queue and the task detail.

- `question` appears directly in the queue. Ask one concrete question in a single sentence of at most 120 characters. Name the decision the user is making.
- `context` appears when the task opens. Explain why input is needed and which work depends on it. Put evidence, dates, paths and longer instructions here.
- `choices` appear as buttons. Offer up to three distinct answers grounded in the evidence. Each `label` is a complete short answer of at most 32 characters. Its `detail` explains the consequence of choosing it. Use an empty list when the user needs to supply an unknown fact, a file or test output. The app provides free text.

For example, ask “Which release should the changelog cover?” with choices “May release” and “June release” when those releases are identified in the task. In the detail, explain which milestones each choice includes. A choice provides task input. External delivery still follows the app’s approval flow.
