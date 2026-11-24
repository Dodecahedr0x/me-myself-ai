"use strict";

// Render connected text locally. Only explicit web links can navigate out of the dashboard.
const taskMarkdown = window.markdownit({html:false});
taskMarkdown.validateLink = value => {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
  } catch {
    return false;
  }
};
taskMarkdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet("target", "_blank");
  tokens[index].attrSet("rel", "noopener noreferrer");
  return renderer.renderToken(tokens, index, options);
};
taskMarkdown.renderer.rules.image = (tokens, index) => taskMarkdown.utils.escapeHtml(tokens[index].content);

// Hermes messages may also link tasks as task:<id>. A link becomes an in-app button only when the app
// verified the id (env.tasks); otherwise it stays text. See docs/specs/hermes-orchestrator.md#message-format-and-actions.
const chatMarkdown = window.markdownit({html:false});
chatMarkdown.validateLink = value => /^task:[A-Za-z0-9_-]{1,200}$/.test(value) || taskMarkdown.validateLink(value);
chatMarkdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  const href = tokens[index].attrGet("href") || "", closers = env.closers || (env.closers = []);
  if (!href.startsWith("task:")) {
    closers.push("</a>");
    return taskMarkdown.renderer.rules.link_open(tokens, index, options, env, renderer);
  }
  const id = href.slice(5);
  if (!(env.tasks || []).includes(id)) { closers.push("</span>"); return '<span class="task-ref missing">'; }
  // A link, not a button, so a long title wraps with the sentence. Enter and Space work via app.js.
  closers.push("</a>");
  return '<a class="task-ref" role="button" tabindex="0" data-action="chat-open-task" data-id="' + chatMarkdown.utils.escapeHtml(id) + '">';
};
chatMarkdown.renderer.rules.link_close = (tokens, index, options, env) => (env.closers || []).pop() || "</a>";
chatMarkdown.renderer.rules.image = taskMarkdown.renderer.rules.image;
