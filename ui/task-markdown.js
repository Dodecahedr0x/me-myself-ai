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
