"use strict";

const $ = selector => document.querySelector(selector);
const esc = (value = "") => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const plain = value => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);
const titleCase = text => String(text ?? "").replace(/[_-]/g, " ").replace(/^./, c => c.toUpperCase());
const viewNames = {inbox:"Inbox", overview:"Overview", tasks:"Tasks", activity:"Activity", connections:"Connections", projects:"Projects", context:"Context", policies:"Decision rules", skills:"Skills & style", setup:"Setup", settings:"Configuration"};
const paths = {
  ignore:'<circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/>',
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  tasks:'<rect x="5" y="4" width="15" height="17" rx="2"/><path d="M9 4V2m7 2V2M9 10h7m-7 4h5"/>',
  activity:'<path d="M2 12h5l3-7 4 14 3-7h5"/>', connections:'<path d="m9 15 6-6m-4-2 2-2a4.2 4.2 0 0 1 6 6l-3 3m-3 3-2 2a4.2 4.2 0 0 1-6-6l3-3"/>',
  projects:'<path d="M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  policies:'<path d="M4 6h4m5 0h7M4 12h9m5 0h2M4 18h2m5 0h9"/><circle cx="10.5" cy="6" r="2.2"/><circle cx="15.5" cy="12" r="2.2"/><circle cx="8.5" cy="18" r="2.2"/>',
  skills:'<path d="M3 5c4-1 6 0 9 2 3-2 5-3 9-2v14c-4-1-6 0-9 2-3-2-5-3-9-2Zm9 2v14"/>',
  settings:'<path d="m9.2 4 .7-2h4.2l.7 2 2 .9 2.1-.4 2.1 3.6-1.4 1.6.2 2.3 1.2 1.7-2.1 3.6-2-.3-2 1-.8 2h-4l-.8-2-2-1-2 .3-2.1-3.6 1.3-1.7.1-2.3-1.4-1.6L5.3 4.5l2 .4Z"/><circle cx="12" cy="11.5" r="3"/>',
  setup:'<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.5 1.5L7.5 5M3.5 12l1.5 1.5L7.5 11"/><circle cx="5" cy="18" r="1.6"/>',
  shield:'<path d="m12 2 8 3.5v6.2c0 5-3.9 8-8 10.3-4.1-2.3-8-5.3-8-10.3V5.5Z"/><path d="m8.8 11.6 2.2 2.1 4.4-4.6"/>',
  add:'<path d="M12 5v14M5 12h14"/>', arrow:'<path d="M4 12h15m-6-6 6 6-6 6"/>', chevron:'<path d="m9 5 7 7-7 7"/>',
  menu:'<path d="M3 5h18M3 12h18M3 19h18"/>', close:'<path d="m5 5 14 14m0-14L5 19"/>',
  check:'<path d="m4 12 5 5L20 6"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  review:'<path d="M3 5h18v12H9l-5 4v-4H3Z"/><path d="M7 9h10m-10 4h7"/>',
  reply:'<path d="m10 5-7 6 7 6v-4c5 0 8 1 11 5-1-7-4-10-11-10Z"/>',
  inspect:'<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  working:'<path d="m13 2-9 12h7l-1 8 10-13h-7Z"/>', done:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  github:'<path fill="currentColor" stroke="none" d="M12 .3a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.24c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.74.08-.74 1.21.09 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.5.99.11-.78.42-1.3.76-1.6-2.67-.31-5.47-1.34-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.48 11.48 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.6-2.81 5.62-5.49 5.92.43.37.82 1.1.82 2.22v3.32c0 .32.22.7.83.58A12 12 0 0 0 12 .3Z"/>',
  google:'<path stroke="none" fill="currentColor" d="M21.8 12.2c0-.7-.1-1.4-.2-2.1H12v4h5.5a4.8 4.8 0 0 1-2.1 3.2 6.2 6.2 0 1 1 1-9.8l3-2.9A10.4 10.4 0 1 0 12 22.4c5.8 0 9.8-4.1 9.8-10.2Z"/>',
  repository:'<path d="M5 18.5V5a2 2 0 0 1 2-2h12v18H7a2.5 2.5 0 0 1 0-5h12M9 6v6l2-1.5 2 1.5V6"/>',
  git:'<circle cx="7" cy="5" r="2.5"/><circle cx="7" cy="19" r="2.5"/><circle cx="17" cy="7" r="2.5"/><path d="M7 7.5v9m10-7c0 4-4 5-10 5"/>',
  file:'<path d="M5 3h9l5 5v13H5zM14 3v6h5"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>',
  slack:'<path d="M9 3v10m6-2v10M3 9h10m-2 6h10M6 3v2M3 18h2m13 3v-2m3-13h-2"/>',
  email:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 7 9-7"/>',
  sync:'<path d="M20 5v5h-5m-11 9v-5h5M5.5 9a7.5 7.5 0 0 1 12.8-3l1.7 3M4 15l1.7 3A7.5 7.5 0 0 0 18.5 15"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  stack:'<path d="m3 8 9-5 9 5-9 5Zm0 5 9 5 9-5M3 17l9 5 9-5"/>',
  inbox:'<path d="M3 13h5l1.5 3h5l1.5-3h5"/><path d="M5.5 5h13L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Z"/>',
  home:'<path d="M3 11 12 4l9 7"/><path d="M5 10v10h5v-6h4v6h5V10"/>',
  alert:'<path d="M12 3.5 2.5 20h19Z"/><path d="M12 10v4.5m0 2.5v.01"/>', question:'<circle cx="12" cy="12" r="9"/><path d="M9.6 9.2a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6m0 3v.01"/>',
  pause:'<circle cx="12" cy="12" r="9"/><path d="M10 9v6m4-6v6"/>', x:'<path d="m7 7 10 10M17 7 7 17"/>', user:'<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
  play:'<path d="M7 4.5v15l12-7.5Z"/>', send:'<path d="M4 12 20 4l-5 16-3-7Z"/><path d="m12 13 8-9"/>', bars:'<path d="M6 20v-6m6 6V5m6 15v-9"/>',
  more:'<path fill="currentColor" d="M5 13.3a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6Zm7 0a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6Zm7 0a1.3 1.3 0 1 0 0-2.6 1.3 1.3 0 0 0 0 2.6Z"/>',
  up:'<path d="m6 15 6-6 6 6"/>', down:'<path d="m6 9 6 6 6-6"/>', back:'<path d="M20 12H5m6-6-6 6 6 6"/>', expand:'<path d="M14 4h6v6m0-6-7 7M10 20H4v-6m0 6 7-7"/>',
  context:'<circle cx="12" cy="12" r="3"/><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="20" r="2"/><path d="m7 7 3 3m7-3-3 3m-2 2v5"/>',
  flag:'<path d="M5 22V4m0 0c6-4 8 4 15 0v11c-7 4-9-4-15 0"/>', dot:'<circle cx="12" cy="12" r="4"/>', monitor:'<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8m-4-3v3"/>'
};
function icon(name, size = 18) {
  return '<svg aria-hidden="true" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">'+(paths[name] || paths.dot)+'</svg>';
}
const sourceIcons = {github:"github",slack:"slack",email:"email",gmail:"email",google:"google",calendar:"calendar",git:"git",file:"file",local:"monitor","mac app":"monitor"};
function namedIcon(glyph,label,size=16) {
  return '<span class="named-icon" role="img" aria-label="'+esc(label)+'" title="'+esc(label)+'">'+icon(glyph,size)+'</span>';
}
function sourceMark(name,size=16) {
  const key=String(name).toLowerCase();
  return Object.hasOwn(sourceIcons,key)?namedIcon(sourceIcons[key],key==="github"?"GitHub":titleCase(name),size):esc(name);
}
const groups = {attention:["review","question","failed","uncertain"], progress:["queued","working","sending","scheduled"], completed:["done"]};
const statusNames = {queued:"Queued",working:"In progress",review:"Awaiting approval",question:"Needs an answer",done:"Completed",failed:"Failed",skipped:"Skipped",dismissed:"Dismissed",closed:"Closed",paused:"Ignored",uncertain:"Delivery uncertain",sending:"Sending",scheduled:"Scheduled"};
const filters = {open:"Open",all:"All tasks",attention:"Needs attention",progress:"In progress",completed:"Completed",closed:"Closed",inactive:"Skipped & dismissed"};
const compactDate = stamp => stamp ? new Date(Number(stamp)*1000).toLocaleDateString(undefined,{month:"short",day:"numeric"}) : "";
const fullDate = stamp => stamp ? new Date(Number(stamp)*1000).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}) : "";
function relativeDate(stamp) {
  if (!stamp) return "";
  const mins = Math.floor((Date.now()/1000-Number(stamp))/60);
  return mins < 0 ? compactDate(stamp) : mins < 1 ? "Just now" : mins < 60 ? mins+"m ago" : mins < 1440 ? Math.floor(mins/60)+"h ago" : compactDate(stamp);
}
const state = {tasks:[],activity:[],connections:[],rules:[],skills:[],projects:[],settings:{dry:true}};
let schemas = {}, loaded = false, refreshing = false, running = false, dialogState = null, invoker = null, noticeTimer;
let filter = "open", projectFilter = "", query = "", activeView = "";
let activityProject = "", activityQuery = "", activityConnection = "";
const providerNames = {claude:"Claude Code", codex:"Codex CLI"};
let providers = null, providersPending = false, providersChecked = 0;
let agentSetup = null, agentsPending = false, agentsChecked = 0;
let setup = null, setupPending = false, setupChecked = 0, setupOpened = false, hostDraft = "";
const agentStates = {ready:"Ready",sign_in:"Sign in needed",not_installed:"Not installed",unsupported_version:"Unsupported version",unknown:"Status unavailable"};
const subscriptionNames = {claude:"Claude subscription",codex:"ChatGPT subscription"};
const button = (label, action, glyph, style = "secondary", attrs = "") => '<button type="button" class="btn '+style+'" data-action="'+action+'" '+attrs+'>'+ (glyph ? icon(glyph,16) : "") +esc(label)+'</button>';
const link = (label, view) => '<a class="text-link" href="#'+view+'">'+esc(label)+icon("arrow",15)+'</a>';
const status = value => '<span class="status '+(groups.attention.includes(value)?"attention":groups.progress.includes(value)?"progress":value==="done"?"complete":"neutral")+'"><i></i>'+esc(statusNames[value] || titleCase(value))+'</span>';
const mark = '<span class="brandmark" aria-hidden="true"><i></i><i></i><i></i></span>';
function setIfChanged(el, html) {
  if (el.rendered === html) return;
  const active = el.contains(document.activeElement) ? document.activeElement : null;
  const index = active ? Array.from(el.querySelectorAll("a,button,input,select")).indexOf(active) : -1;
  el.innerHTML = html; el.rendered = html;
  if (active && index >= 0) {
    const controls = [...el.querySelectorAll("a,button,input,select")];
    const match = controls.find(n => n.id && n.id===active.id || n.dataset.id && n.dataset.id===active.dataset.id &&
      n.dataset.action===active.dataset.action && n.dataset.choice===active.dataset.choice && n.dataset.version===active.dataset.version ||
      n.getAttribute("href") && n.getAttribute("href")===active.getAttribute("href"));
    const restored=match || (active.closest(".task-entry") && controls.find(n=>n.dataset.action==="detail" && n.dataset.id===active.dataset.id));
    if (restored) restored.focus({preventScroll:true});
  }
}
const recent = (list,key) => [...list].sort((a,b) => Number(b[key]||0)-Number(a[key]||0));
function shell(view) {
  const glyphs={overview:"home"};
  const waiting=state.tasks.filter(t=>inboxKind(t)).length;
  const nav = key => '<a href="#'+key+'" '+(view===key?'aria-current="page"':'')+'>'+icon(glyphs[key]||key,18)+'<span>'+esc(viewNames[key])+'</span>'+
    (key==="inbox" && waiting?'<b class="badge" aria-label="'+waiting+' need you">'+waiting+'</b>':"")+'</a>';
  const who=providerNames[state.settings.provider]||"Choose an agent";
  const account='<a class="account" href="#settings" aria-label="Configuration: '+esc(who)+'"><span class="avatar" aria-hidden="true">'+esc(who[0])+'</span><span><strong>'+esc(who)+'</strong><small>'+(loaded?(state.settings.dry?"Dry mode":"Live mode"):"Connecting…")+'</small></span>'+icon("chevron",14)+'</a>';
  setIfChanged($("#sidebar"), '<div class="brand">'+mark+'<span><strong>MeMyselfAI</strong><small>AI agent workspace</small></span></div><nav><div class="navgroup">'+["overview","inbox","tasks","activity","projects","connections"].map(nav).join("")+'</div><div class="navgroup">'+["context","policies","skills","setup","settings"].map(nav).join("")+'</div></nav><div class="sidebar-foot">'+account+'</div>');
  document.body.dataset.view=view;
  const buttons = {
    overview:["New task","task","add"], tasks:["New task","task","add"],
    activity:["Report missed work","report","flag"], projects:["Add project","project","add"],
    policies:["Add rule","rule","add"], skills:["Suggest style","style","add"]
  };
  const primary = buttons[view] ? button(...buttons[view],"primary") :
    view==="connections" && state.connections.length ? button("Sync now","sync","sync") : "";
  const delivery = loaded ? '<a class="mode '+(state.settings.dry?'':'live')+'" href="#settings">'+icon("shield",13)+(state.settings.dry?"Dry mode":"Live mode")+'</a>' : "";
  const provider = providerNames[state.settings.provider] || "Choose provider";
  const actions = '<div class="toolbar-actions"><a class="provider-link" href="#settings" aria-label="Choose an agent provider">'+esc(provider)+'</a>'+delivery+(loaded?primary:"")+'</div>';
  setIfChanged($("#bar"), '<div class="toolbar-title"><button type="button" class="iconbtn menu-toggle" data-action="menu" aria-label="Open navigation" aria-controls="sidebar" aria-expanded="false">'+icon("menu")+'</button><h1>'+esc(viewNames[view])+'</h1></div>'+actions);
}
function empty(message, action = "") {
  return '<div class="empty"><span>'+esc(message)+'</span>'+action+'</div>';
}
function taskRepository(t) {
  const evidence=t.evidence?.[0];
  return t.origin?.repository || t.github_repository || t.repository || evidence?.repository || (evidence?.source==="Git" && evidence.folder);
}
function taskContext(t) {
  const origin = t.origin || {};
  const evidence = t.evidence?.[0];
  const provider = origin.source || evidence?.source || t.source || "Local";
  const name = provider.toLowerCase() === "github" ? "GitHub" : titleCase(provider);
  const identity = [origin.workspace, origin.channel || origin.account].filter(Boolean).join(" · ");
  const source = [name, identity].filter(Boolean).join(" · ");
  const repository = taskRepository(t);
  return [["Project", t.project], ["Repository", repository], ["Source", source, {name,identity}], ["On", t.owner]].filter(([, value])=>value);
}
function sourceIdentity({name,identity}) { return sourceMark(name)+(identity?" "+esc(identity):""); }
function contextLine(t,inQueue=false) {
  const namedProject=state.projects.some(p=>p.name===t.project && !p.global);
  const fields = taskContext(t).map(([label,value,source])=>{
    if (source) return '<span class="context-field">'+sourceIdentity(source)+'</span>';
    const project=label==="Project", scope=project?"project":"repository";
    const shortcut=inQueue && project===namedProject?' aria-keyshortcuts="I"':"";
    return '<button type="button" class="context-field scope-link" data-action="'+scope+'-details" data-value="'+esc(value)+'" data-id="'+esc(t.id)+'"'+shortcut+' aria-haspopup="dialog" title="Open '+scope+'">'+
      icon(project?"projects":"repository",14)+' '+esc(value)+'</button>';
  });
  return '<span class="task-context">'+fields.join("")+'</span>';
}
const taskShortcuts = {
  "folder-access":["Allow folder access","projects","A"],
  review_proposal:["Review proposal","inspect","V"], answer:["Answer question","reply","A"],
  retry:["Retry preparation","sync","R"], feedback:["Give feedback","review","F"],
  reconcile:["Record delivery outcome","check","O"], close_task:["Close task","close","C"]
};
function taskActionNames(t) {
  const actions=[];
  if (t.status==="review") actions.push("review_proposal");
  if (t.status==="question") actions.push(t.checkout_inaccessible?"folder-access":"answer");
  if (t.status==="uncertain") actions.push("reconcile");
  if (["failed","question"].includes(t.status)) actions.push("retry");
  if (["queued","review","question","failed","working","scheduled"].includes(t.status)) actions.push("feedback");
  if (!["closed","sending","uncertain"].includes(t.status)) actions.push("close_task");
  return actions;
}
function answerChoices(t, detailed=false) {
  if (t.status!=="question") return "";
  if (!t.answer_choices?.length || t.checkout_inaccessible) {
    if (detailed || !t.answer_choices && !t.checkout_inaccessible) return "";
    const attrs='data-id="'+esc(t.id)+'"';
    const control=t.checkout_inaccessible?button("Allow folder access","folder-access","projects","secondary",attrs):
      t.repository_question?button("Choose checkout…","pick-checkout","projects","secondary",attrs+' data-version="'+esc(t.version)+'"'):
      button("Write an answer…","answer","reply","secondary",attrs);
    return '<div class="queue-answers">'+control+'</div>';
  }
  const choices=t.answer_choices.map((choice,index)=>{
    const attrs='data-id="'+esc(t.id)+'" data-choice="'+index+'" data-version="'+esc(t.version)+'"';
    const pick=button(choice.label,"pick-answer",null,"secondary",attrs);
    return detailed?'<div>'+pick+'<p>'+esc(choice.detail)+'</p></div>':pick;
  }).join("");
  return '<div class="'+(detailed?"answer-details":"queue-answers")+'" role="group" aria-label="Suggested answers">'+choices+'</div>';
}
function rows(tasks) {
  return tasks.map(t=>{
    const preview = (t.status==="review"?t.draft:t.status==="question"?t.question:"") || t.result || t.reason || t.body || "";
    const shortcuts=taskActionNames(t).map(action=>{
      const [label,glyph,key]=taskShortcuts[action];
      return '<button type="button" class="iconbtn" data-action="'+action+'" data-id="'+esc(t.id)+'" aria-label="'+esc(label+" · "+t.title)+'" aria-keyshortcuts="'+key+'" title="'+label+' ('+key+')">'+icon(glyph,16)+'</button>';
    }).join("");
    return '<div class="task-entry"><div class="task-row'+(groups.attention.includes(t.status)?' needs-attention':'')+'">'+
      '<div class="row-title"><button type="button" class="task-open" data-action="detail" data-id="'+esc(t.id)+'"><strong>'+esc(t.title)+'</strong></button>'+contextLine(t,true)+'<small'+(t.status==="question" && t.answer_choices?' class="queue-question"':"")+'>'+esc(plain(preview).slice(0,200))+'</small>'+answerChoices(t)+'<span class="row-mobile">'+esc(relativeDate(t.created))+'</span></div>'+
      status(t.status)+'<time class="task-age" title="'+esc(fullDate(t.created))+'">'+esc(relativeDate(t.created))+'</time></div><div class="row-shortcuts">'+shortcuts+'</div></div>';
  }).join("");
}
function taskTable(tasks) {
  return '<div class="task-heading" aria-hidden="true"><div class="task-columns"><span>Task</span><span>Status</span><span>Created</span></div><span>Actions</span></div>'+rows(tasks);
}
const logClock = new Intl.DateTimeFormat(undefined,{hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
function timeline(items) {
  const today = new Date().toDateString();
  const tasks = new Set(state.tasks.map(t=>String(t.id)));
  const events = items.map(item=>{
    const when = new Date(Number(item.at)*1000);
    const day = when.toDateString()===today ? "" : '<small>'+esc(compactDate(item.at))+'</small>';
    const time = '<time datetime="'+when.toISOString()+'" title="'+esc(fullDate(item.at))+'">'+day+esc(logClock.format(when))+'</time>';
    const viewTask = item.task && tasks.has(String(item.task)) ? '<button type="button" data-action="detail" data-id="'+esc(item.task)+'" class="inline-task">View task '+icon("arrow",12)+'</button>' : "";
    return '<li>'+time+'<div class="event-body"><div class="event-meta"><strong>'+esc(titleCase(item.kind))+'</strong>'+viewTask+'</div><p>'+esc(plain(item.detail))+'</p>'+(item.project?'<small>'+esc(item.project)+'</small>':"")+'</div></li>';
  });
  return '<ol class="timeline">'+events.join("")+'</ol>';
}
function evidenceList(records) {
  return '<ul class="suggestion-evidence">'+records.map(r=>'<li>'+sourceMark(r.source)+' '+
    (sourceReference(r.url,r.title)||esc(r.title))+' <small>'+esc([r.authored_by_user?"Written by you":"",r.destination,fullDate(r.at)].filter(Boolean).join(" · "))+'</small>'+
    (r.detail?'<p>'+esc(r.detail)+'</p>':'')+'</li>').join("")+'</ul>';
}
function taskSuggestions(showSources = true) {
  const data=state.suggestions;
  if (!data) return "";
  const items=data.pending.map(s=>'<div class="suggested-task"><div><strong>'+esc(s.title)+'</strong>'+contextLine(s)+'<p>'+esc(s.reason)+'</p>'+
    '<details><summary>Supporting activity ('+s.evidence.length+')</summary><p>'+esc(s.body)+'</p>'+evidenceList(s.evidence)+'</details></div><div class="entry-actions">'+
    button("Start task","suggestion_start",null,"secondary",'data-id="'+esc(s.id)+'"')+
    button("Dismiss","suggestion_dismiss",null,"quiet",'data-id="'+esc(s.id)+'"')+'</div></div>').join("");
  const gaps=(data.scan.gaps||[]).map(g=>'<p class="form-error">'+esc(g)+'</p>').join("");
  return section("Suggested tasks",button("Check activity","suggestion_scan","sync","quiet"),items+gaps||
    empty(data.scan.at?"No new suggestions · "+relativeDate(data.scan.at):"No suggestions yet.",showSources?link("Activity sources","connections"):""));
}
// Overview dashboard: stats, an activity report and a chat with Hermes. See docs/specs/dashboard.md#overview-dashboard.
const reportPeriods = {today:["Today",1], week:["7 days",7], month:["30 days",30]};
let reportPeriod = Object.hasOwn(reportPeriods,localStorage.getItem("report-period"))?localStorage.getItem("report-period"):"week";
let chatDraft = "", chatSending = false;
const chatPrompts = [
  ["What's on my plate?","What's on my plate right now? List what needs me first."],
  ["What's stuck?","Which tasks are stuck or failing, and why?"],
];
function reportPrompt() {
  const span=reportPeriod==="today"?"today":"over the last "+reportPeriods[reportPeriod][0];
  return "Report on my activity "+span+": what came in, what you dispatched, what finished, what failed and what is waiting for me. Link each task and lead with what I need to do.";
}
function dayStart(offset=0) {
  const d=new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()-offset); return d.getTime()/1000;
}
function connectionName(id) {
  if (!id) return "MeMyselfAI";
  const c=state.connections.find(c=>c.id===id);
  return c?(c.identity && c.label!==c.identity?c.label+" · "+c.identity:c.label):{"desktop:mac":"This Mac","desktop:claude":"Claude","desktop:chatgpt":"ChatGPT"}[id]||"Disconnected source";
}
function activityReport() {
  const [label,days]=reportPeriods[reportPeriod], since=dayStart(days-1), events=lastEvents();
  const inPeriod=state.activity.filter(a=>Number(a.at)>=since);
  // Hourly bars for today, daily bars otherwise.
  const buckets=days===1?Array.from({length:24},(_,h)=>[since+h*3600,since+(h+1)*3600,String(h).padStart(2,"0")+":00"]):
    Array.from({length:days},(_,i)=>{const from=dayStart(days-1-i);return [from,dayStart(days-2-i),compactDate(from)];});
  const counts=buckets.map(([from,to])=>inPeriod.filter(a=>Number(a.at)>=from && Number(a.at)<to).length);
  const peak=Math.max(1,...counts);
  const bars=buckets.map(([,,name],i)=>'<li style="--h:'+(counts[i]/peak*100).toFixed(1)+'%" data-tip="'+esc(name+": "+counts[i]+(counts[i]===1?" event":" events"))+'" title="'+esc(name+": "+counts[i])+'"><i></i></li>').join("");
  const axis=[buckets[0][2],buckets[Math.floor(buckets.length/2)][2],buckets[buckets.length-1][2]];
  const chart='<figure class="report-chart"><figcaption><strong>'+inPeriod.length+'</strong> events · '+esc(label.toLowerCase())+'</figcaption>'+
    '<ol class="bars" role="img" aria-label="'+esc("Events per "+(days===1?"hour":"day")+": "+buckets.map(([,,n],i)=>n+" "+counts[i]).join(", "))+'">'+bars+'</ol>'+
    '<div class="bar-axis" aria-hidden="true">'+axis.map(a=>'<span>'+esc(a)+'</span>').join("")+'</div></figure>';
  const touched=t=>(events.get(String(t.id))||Number(t.created||0))>=since;
  const facts=[
    ["New tasks",state.tasks.filter(t=>Number(t.created)>=since).length,"all"],
    ["Completed",state.tasks.filter(t=>t.status==="done" && touched(t)).length,"completed"],
    ["Failed",state.tasks.filter(t=>t.status==="failed" && touched(t)).length,"attention"],
    ["Waiting for you",state.tasks.filter(t=>inboxKind(t)).length,null],
  ].map(([name,n,f])=>'<div><dt>'+esc(name)+'</dt><dd>'+(f?'<button type="button" class="fact-link" data-action="filter" data-filter="'+f+'">'+n+'</button>':'<a class="fact-link" href="#inbox">'+n+'</a>')+'</dd></div>').join("");
  const top=(key,name)=>{
    const tally=new Map();
    for (const a of inPeriod) { const k=key(a); if (k!=null) tally.set(k,(tally.get(k)||0)+1); }
    const rows=[...tally].sort((a,b)=>b[1]-a[1]).slice(0,5);
    return '<div class="report-list"><h3>'+esc(name)+'</h3>'+(rows.length?'<ol>'+rows.map(([k,n])=>'<li><span>'+esc(k)+'</span><b>'+n+'</b></li>').join("")+'</ol>':'<p class="empty">Nothing yet.</p>')+'</div>';
  };
  const periods='<div class="segmented" role="group" aria-label="Report period">'+Object.entries(reportPeriods).map(([key,[name]])=>
    '<button type="button" data-action="report-period" data-period="'+key+'" aria-pressed="'+(reportPeriod===key)+'">'+esc(name)+'</button>').join("")+'</div>';
  const ask=button("Ask Hermes for a report","chat-report","review","secondary",state.orchestrator?.pending||chatSending?"disabled":"");
  return '<section class="block report"><div class="section-title"><h2>Activity report</h2><div class="entry-actions">'+periods+ask+'</div></div>'+
    '<div class="report-body">'+chart+'<dl class="report-facts">'+facts+'</dl></div>'+
    '<div class="report-lists">'+top(a=>connectionName(a.connection),"By source")+top(a=>a.project||null,"By project")+top(a=>titleCase(a.kind),"By kind")+'</div></section>';
}
function dashboardStats() {
  const events=lastEvents(), week=dayStart(6), o=state.orchestrator;
  const attention=state.tasks.filter(t=>inboxKind(t)).length;
  const progress=state.tasks.filter(t=>groups.progress.includes(t.status)).length;
  const done=state.tasks.filter(t=>t.status==="done" && (events.get(String(t.id))||Number(t.created||0))>=week).length;
  const today=state.activity.filter(a=>Number(a.at)>=dayStart()).length;
  const card=(cls,glyph,n,label,attrs)=>'<'+(attrs.startsWith("href")?'a':'button type="button"')+' class="stat '+cls+'" '+attrs+'><span class="stat-top">'+icon(glyph,22)+'<strong>'+n+'</strong></span><span>'+esc(label)+'</span></'+(attrs.startsWith("href")?'a':'button')+'>';
  return '<div class="stats dash-stats">'+card(attention?"attention":"","user",attention,"Need you",'href="#inbox"')+
    card("progress","working",progress,"In progress",'data-action="filter" data-filter="progress"')+
    card("complete","check",done,"Done in 7 days",'data-action="filter" data-filter="completed"')+
    card("progress","stack",o?o.used_today+" / "+o.budget:"–","Hermes started today",'data-action="chat-focus"')+
    card("blocked","activity",today,"Events today",'href="#activity"')+'</div>';
}
function chatHead() {
  const o=state.orchestrator;
  if (!o) return '<div class="chat-head"><h2>Hermes</h2></div>';
  const busy=o.pending||chatSending;
  const statusText=!o.enabled?"Paused":busy?"Answering…":o.status==="failed"||o.status==="waiting"?"Needs attention":titleCase(o.status||"idle");
  const tone=!o.enabled?"neutral":o.error?"attention":busy||o.status!=="idle"?"progress":"complete";
  const last=o.last_sweep?"Last sweep "+relativeDate(o.last_sweep):"No sweep yet";
  return '<div class="chat-head"><div><h2>Hermes</h2><p><span class="status '+tone+'"><i></i>'+esc(statusText)+'</span> · '+esc(last)+'</p></div><div class="entry-actions">'+
    '<button type="button" class="iconbtn plain" data-action="orchestrator-run" aria-label="Sweep now" title="Sweep now">'+icon("sync",16)+'</button>'+
    '<button type="button" class="iconbtn plain" data-action="orchestrator-toggle" aria-label="'+(o.enabled?"Pause Hermes":"Resume Hermes")+'" title="'+(o.enabled?"Pause":"Resume")+'">'+icon(o.enabled?"pause":"play",16)+'</button></div></div>'+
    (o.error?'<p class="chat-error">'+icon("alert",14)+' '+esc(o.error)+'</p>':"");
}
function chatThread() {
  const o=state.orchestrator;
  if (!o) return empty("Hermes is not available on this instance.");
  const who={user:"You",hermes:"Hermes",app:"MeMyselfAI"};
  const messages=(o.messages||[]).map(m=>'<div class="chat-msg '+esc(m.role)+'"><div class="chat-meta"><strong>'+esc(who[m.role]||m.role)+'</strong><time title="'+esc(fullDate(m.at))+'">'+esc(relativeDate(m.at))+'</time></div>'+
    (m.role==="user"?'<p class="message-text">'+esc(m.text)+'</p>':m.role==="hermes"?'<div class="markdown-body">'+chatMarkdown.render(plain(m.text),{tasks:(m.tasks||[]).map(String)})+'</div>'+chatActions(m.tasks):'<div class="markdown-body">'+taskMarkdown.render(plain(m.text))+'</div>')+'</div>').join("");
  const typing=o.pending||chatSending?'<div class="chat-msg hermes typing" aria-label="Hermes is answering"><span></span><span></span><span></span></div>':"";
  return messages+typing || '<div class="chat-empty">'+icon("review",22)+'<p>Ask Hermes what is on your plate, why a task is stuck, or for a report on your activity. It reads your connected accounts and can start tasks. Anything it prepares to send still waits for your approval.</p></div>';
}
// One card per task a Hermes message links, with its current status and primary action.
// See docs/specs/hermes-orchestrator.md#message-format-and-actions.
function chatActions(ids) {
  // Only tasks that still need the user; the rest stay as links in the text.
  const tasks=(ids||[]).map(findTask).filter(t=>t && inboxKind(t));
  if (!tasks.length) return "";
  return '<ul class="chat-actions" aria-label="Waiting for you">'+tasks.map(t=>{
    const id='data-id="'+esc(t.id)+'"', primary=taskPrimary(t);
    const question=t.status==="question" && !primary?'<p class="chat-question">'+esc(plain(t.question).slice(0,200))+'</p>'+answerChoices(t):"";
    return '<li><div class="chat-action-head"><button type="button" class="task-ref" data-action="chat-open-task" '+id+'>'+esc(t.title)+'</button>'+inboxPill(t)+'</div>'+question+
      '<div class="chat-action-buttons">'+(primary?button(primary[1],primary[0],primary[2],"primary",id):"")+button("Open","chat-open-task",null,"quiet",id)+'</div></li>';
  }).join("")+'</ul>';
}
function openTaskFromChat(id) {
  const t=findTask(id); if (!t) return;
  if (!inboxKind(t)) { showDialog("detail",t.title,detailTask(t),{task:t,signature:JSON.stringify(t)}); return; }
  inboxFilter="all";
  // The Inbox renders on hashchange, after this returns; select first so it opens on this task.
  if (activeView!=="inbox") { selectedTask=t.id; detailTab="activity"; inboxNarrowOpen=true; location.hash="inbox"; return; }
  selectInboxTask(t.id);
}
function chatComposer() {
  const busy=!!(state.orchestrator?.pending||chatSending), off=!state.orchestrator;
  const quick=chatPrompts.map(([label,text])=>'<button type="button" class="chip" data-action="chat-quick" data-text="'+esc(text)+'" '+(busy||off?"disabled":"")+'>'+esc(label)+'</button>').join("");
  return '<div class="chip-row chat-quick">'+quick+'</div><form id="chat-form"><label class="sr-only" for="chat-text">Message Hermes</label>'+
    '<textarea id="chat-text" name="text" rows="2" maxlength="4000" placeholder="'+(busy?"Hermes is answering…":"Message Hermes…")+'" '+(off?"disabled":"")+'>'+esc(chatDraft)+'</textarea>'+
    '<div class="composer-foot"><small>⌘↵ to send</small><button type="submit" class="btn primary send" aria-label="Send" '+(busy||off?"disabled":"")+'>'+icon("send",16)+'</button></div></form>';
}
function overview() {
  return '<div class="dashboard dash-grid"><div class="dash-main"><div id="ov-top"></div><div id="ov-main"></div></div>'+
    '<aside class="dash-chat" id="ov-chat" aria-label="Chat with Hermes"><div id="ov-chat-head"></div><div class="chat-thread" id="ov-thread" aria-live="polite"></div><div id="ov-composer"></div></aside></div>';
}
function overviewMain() {
  const ordered = recent(state.tasks.filter(t=>!["closed","paused"].includes(t.status)),"created");
  const attention = ordered.filter(t=>groups.attention.includes(t.status));
  const active = ordered.filter(t=>groups.progress.includes(t.status));
  const queue = attention.length+active.length ? taskTable([...attention,...active].slice(0,8)) : empty(ordered.length?"Nothing open. Completed work is in Tasks.":"No tasks yet.");
  const events = recent(state.activity,"at").slice(0,8);
  const projects = state.projects.map(p=>{
    const count = [...attention,...active].filter(t=>inProject(p.name,t.project)).length;
    return entry("projects",p.name,[count+" open",p.repos?.length?p.repos.length+(p.repos.length===1?" repository":" repositories"):""].filter(Boolean).join(" · "),projectControls(p));
  }).join("");
  const styleReviews = state.skills.filter(s=>s.kind==="style" && !s.active && s.status==="pending")
    .map(s=>'<button class="skill-row" data-action="skill" data-id="'+esc(s.id)+'"><span class="row-title"><strong>'+esc(s.body)+'</strong></span>'+icon("chevron",14)+'</button>').join("");
  const manage = '<a class="btn secondary" href="#connections">Manage connections</a>';
  return activityReport()+
    '<section class="home-queue block"><div class="queue-heading"><h2>Open tasks</h2>'+button("Manage ignore list","ignorelist",null,"quiet")+link("All tasks","tasks")+'</div>'+queue+'</section>'+
    taskSuggestions(false)+
    '<div class="overview-grid">'+section("Recent activity",link("View log","activity"),events.length?timeline(events):empty("No activity yet."))+
    '<div>'+section("Connections",manage,"")+
    (projects?section("Projects",link("View all","projects"),projects):"")+
    (styleReviews?section("Style to review",link("View all","skills"),styleReviews):"")+'</div></div>';
}
function renderOverview() {
  if (!$("#ov-top")) return;
  setIfChanged($("#ov-top"),setupCard()+dashboardStats());
  setIfChanged($("#ov-main"),overviewMain());
  setIfChanged($("#ov-chat-head"),chatHead());
  const thread=$("#ov-thread"), html=chatThread();
  if (thread.rendered!==html) {
    const pinned=!thread.rendered || thread.scrollHeight-thread.scrollTop-thread.clientHeight<60;
    setIfChanged(thread,html);
    if (pinned) thread.scrollTop=thread.scrollHeight;
  }
  const busy=!!(state.orchestrator?.pending||chatSending), box=$("#ov-composer"), signature=[busy,!!state.orchestrator].join("|");
  if (box.dataset.signature!==signature) {
    const focused=box.contains(document.activeElement) && document.activeElement.id==="chat-text";
    box.innerHTML=chatComposer(); box.dataset.signature=signature;
    if (focused) { const area=$("#chat-text"); area.focus({preventScroll:true}); area.setSelectionRange(area.value.length,area.value.length); }
  }
}
async function sendChat(text) {
  text=String(text||"").trim();
  if (!text || chatSending || state.orchestrator?.pending) return;
  chatSending=true; renderOverview();
  try {
    await request("/api/action",{op:"orchestrator_prompt",text});
    chatDraft=""; const area=$("#chat-text"); if (area) area.value="";
  } catch(e) { notify(e.message); }
  finally { chatSending=false; await refresh(); }
}
function selectOptions(values, current) {
  return values.map(([val,label])=>'<option value="'+esc(val)+'" '+(String(val)===String(current)?"selected":"")+'>'+esc(label)+'</option>').join("");
}
function projectSelect(id, value) {
  const names = [...new Set([...state.projects.map(p=>p.name),...state.tasks.map(t=>t.project),...state.activity.map(a=>a.project),value].filter(Boolean))].sort();
  return '<select aria-label="Filter by project" id="'+id+'">'+selectOptions([["","All projects"],...names.map(n=>[n,n])],value)+'</select>';
}
function searchInput(id,value,placeholder) {
  return '<label class="search">'+icon("search",16)+'<input type="search" id="'+id+'" placeholder="'+esc(placeholder)+'" aria-label="'+esc(placeholder)+'" value="'+esc(value)+'"></label>';
}
function taskList() {
  const statuses = filter==="open" ? [...groups.attention,...groups.progress] : filter==="closed" ? ["closed"] : filter==="inactive" ? ["skipped","dismissed"] : groups[filter];
  const term = query.trim().toLowerCase();
  const items = recent(state.tasks.filter(t=>{
    if ((statuses && !statuses.includes(t.status)) || !inProject(projectFilter,t.project)) return false;
    if (!term) return true;
    const context = taskContext(t).map(([,value])=>value);
    return [t.title,t.source,t.body,t.status,t.kind,t.repository,...context].some(v=>plain(v).toLowerCase().includes(term));
  }),"created");
  return items.length?taskTable(items):empty(state.tasks.length?"No matching tasks.":"No tasks yet.");
}
function tasksPage() {
  const repo=state.app_repository?.toLowerCase();
  const connected=repo && state.connections.some(c=>
    c.config?.repository?.toLowerCase()===repo || c.repositories?.some(r=>r.name.toLowerCase()===repo));
  const testing=section("Improve MeMyselfAI","",entry("tasks","App testing","Run the workflow checks or report a problem you observed.",
    button("Run checks","self_check","sync","secondary")+button("Report a finding","app_finding",null,"secondary")+
    (repo && !connected?button("Connect app repository","connect_app_repo",null,"quiet"):"")));
  return testing+'<div class="filters">'+searchInput("task-search",query,"Search tasks")+projectSelect("task-project",projectFilter)+button("Manage ignore list","ignorelist",null,"quiet")+'</div><div class="tabs">'+Object.entries(filters).map(([key,label])=>'<button type="button" data-action="filter" data-filter="'+key+'" aria-pressed="'+(filter===key)+'">'+label+'</button>').join("")+'</div><div id="task-list">'+taskList()+'</div>'+taskSuggestions();
}
function activityList() {
  const term = activityQuery.trim().toLowerCase();
  const items = recent(state.activity.filter(a=>(!activityConnection || a.connection===activityConnection)&&inProject(activityProject,a.project)&&[a.kind,a.detail,a.project].some(v=>plain(v).toLowerCase().includes(term))),"at");
  return items.length?timeline(items):empty(activityConnection && !state.activity.some(a=>a.connection===activityConnection)?"No activity recorded for this connection.":state.activity.length?"No matching events.":"No activity yet.");
}
function activityPage() {
  const choices = state.connections.map(c=>[c.id,c.identity && c.label!==c.identity?c.label+" · "+c.identity:c.label]);
  for (const [id,name] of [["desktop:mac","This Mac"],["desktop:claude","Claude"],["desktop:chatgpt","ChatGPT"]]) {
    if (id==="desktop:mac" || state.suggestions?.config?.chats?.[id.slice(8)] || state.activity.some(a=>a.connection===id)) choices.push([id,name]);
  }
  if (activityConnection && !choices.some(([id])=>id===activityConnection)) choices.push([activityConnection,"Disconnected source"]);
  const connections = '<select aria-label="Filter by connection" id="activity-connection">'+selectOptions([["","All connections"],...choices],activityConnection)+'</select>';
  return '<div class="filters">'+searchInput("activity-search",activityQuery,"Search activity")+connections+projectSelect("activity-project",activityProject)+'</div><div id="activity-list">'+activityList()+'</div>';
}
function section(title, action, content) {
  return '<section class="block"><div class="section-title"><h2>'+esc(title)+'</h2>'+action+'</div>'+content+'</section>';
}
function entry(glyph, title, detail, controls = "") {
  const mark=Object.hasOwn(sourceIcons,glyph)?sourceMark(glyph,20):icon(glyph);
  return '<div class="entry"><div class="source-icon">'+mark+'</div><div class="entry-text"><strong>'+esc(title)+'</strong>'+(detail?'<p>'+esc(detail)+'</p>':"")+'</div><div class="entry-actions">'+controls+'</div></div>';
}
function logLink(id) {
  return link("View log","activity?connection="+esc(encodeURIComponent(id)));
}
function connectionsPage() {
  const hasGoogle = state.connections.some(c=>["google","workspace_connector"].includes(c.auth_method));
  const accounts = state.connections.map(c=>{
    const id='data-id="'+esc(c.id)+'"';
    const browse=c.repositories?button("Browse work","github-work",null,"secondary",id):"";
    const row=entry(c.google_services?.length?"google":c.kind,c.label||c.identity||titleCase(c.kind),[c.auth_method==="gh_account"?"":c.identity,c.google_services?.join(", ")||(c.auth_method==="slack_workspace"?(c.channel_count||0)+" channels":""),titleCase(c.status),c.owner?"Polled by "+c.owner:"",c.reconnect_to_revoke?"Reconnect to revoke the login "+c.reconnect_to_revoke+" kept":""].filter(Boolean).join(" · "),
      logLink(c.id)+browse+button("Configure","connect",null,"secondary",id)+button("Disconnect","disconnect",null,"quiet",id));
    if (!c.repositories) return row;
    return row+'<details class="github-inventory"><summary>'+c.repositories.length+' repositories</summary>'+c.repositories.map(r=>'<p>'+sourceReference("https://github.com/"+r.name,r.name)+' · '+esc(r.status)+' '+exclusionButton("repository",r.name,{inherited:r.status==="Ignored"})+'</p>').join("")+'</details>';
  }).join("");
  const providers = Object.entries(schemas).map(([key,s])=>{
    const name=key==="email"?"Google":s.label||titleCase(key);
    const action=key==="email" && hasGoogle?"Add account":"Connect";
    return entry(key==="email"?"google":key,({email:"Workspace account",slack:"Workspace channels",github:"Repository issues and pull requests"})[key]||name,"",
      button(action,"connect","add","secondary",'data-kind="'+esc(key)+'" aria-label="'+esc(action==="Connect"?"Connect "+name:"Add "+name+" account")+'"'));
  }).join("");
  const local=state.suggestions?.config;
  const chats=local?Object.entries({claude:"Claude",chatgpt:"ChatGPT"}).map(([provider,name])=>{
    const active=local.chats?.[provider];
    const attrs='data-kind="'+provider+'"';
    return entry("activity",name,active?"Syncing local work sessions"+(active.folder?" and chat exports":""):"Local desktop chats",
      (active?logLink("desktop:"+provider):"")+button(active?"Configure":"Connect "+name,"chat-settings",null,"secondary",attrs)+(active?button("Disconnect","chat-disconnect",null,"quiet",attrs):""));
  }).join(""):"";
  const computer=local?entry("activity","This Mac",[local.folders.length+" watched folders",local.foreground?"Active app sampling on":""].filter(Boolean).join(" · "),
    logLink("desktop:mac")+button("Choose activity","suggestion-config",null,"secondary")):"";
  return (accounts?section("Connected accounts","",accounts):"")+section("Available sources","",providers||empty("Source setup unavailable.",button("Retry","reload","sync")))+(computer?section("Local activity","",chats+computer):"");
}
function includesProject(group,project) {
  if (!group || !project) return false;
  if (group.global) return true;
  for (let current=project;current;current=state.projects.find(p=>p.name===current.parent)) {
    if (current.name===group.name) return true;
  }
  return false;
}
function inProject(filter,name) {
  if (!filter || filter===name) return true;
  const group=state.projects.find(p=>p.name===filter);
  return !group?.global && includesProject(group,state.projects.find(p=>p.name===name));
}
function exclusionButton(scope,value,{inherited=false}={}) {
  if (!value) return "";
  const list=state.ignorelist||{projects:[],repositories:[]};
  const project=scope==="project"?state.projects.find(p=>p.name===value):null;
  if (scope==="project" && (!project || project.global)) return "";
  const direct=scope==="project"?list.projects.includes(value):list.repositories.includes(value.startsWith("/")?value:value.toLowerCase());
  const parent=project && list.projects.find(name=>name!==value && inProject(name,value));
  const managed=!direct && (parent || inherited);
  const label=managed?(parent?"Ignored via "+parent:"Manage exclusions"):(direct?"Unignore ":"Ignore ")+scope;
  const action=managed?"ignorelist":"set_exclusion";
  const attrs='data-scope="'+scope+'" data-value="'+esc(value)+'" data-ignored="'+(!direct)+'"';
  return button(label,action,"ignore","quiet",attrs+' title="'+esc(label+" · "+value)+'"');
}
function taskExclusions(t) {
  return exclusionButton("project",t.project)+exclusionButton("repository",taskRepository(t));
}
function projectControls(p) {
  return button("Edit","project",null,"secondary",'data-project="'+esc(p.name)+'"')+exclusionButton("project",p.name);
}
const workViews=[["assigned","Assigned issues"],["issues","My open issues"],["pulls","My open pull requests"],["reviews","Requested reviews"]];
async function browseGitHub(connection,view="assigned",page=1) {
  showDialog("github-work",connection.label,selectField("github-view","Open work",workViews,view)+
    '<div id="github-results" aria-live="polite">Loading…</div>',{connection,view,page});
  const ctx=dialogState;
  try {
    const data=await request("/api/action",{op:"github_browse",id:connection.id,view,page});
    if (!authOpen(ctx)) return;
    const monitored=new Set(connection.repositories.filter(r=>r.status!=="Ignored").map(r=>r.name.toLowerCase()));
    $("#github-results").innerHTML=data.items.map(item=>'<div class="github-item"><div>'+sourceReference(item.url,item.title)+
      '<small>'+esc(item.repo+"#"+item.number)+'</small></div>'+
      (monitored.has(item.repo.toLowerCase())?button("Review for work","github-review",null,"quiet",'data-repo="'+esc(item.repo)+'" data-number="'+esc(item.number)+'"'):"")+'</div>').join("")||
      '<p>No open items in this view.</p>';
    if (page>1 || data.more || data.total>1000) $("#github-results").insertAdjacentHTML("beforeend",'<div class="github-pages">'+
      (page>1?button("Previous","github-page",null,"secondary",'data-page="'+(page-1)+'"'):"")+
      '<span>Page '+page+' of '+Math.max(1,Math.ceil(Math.min(data.total,1000)/25))+'</span>'+
      (data.more?button("Next","github-page",null,"secondary",'data-page="'+(page+1)+'"'):"")+
      (data.total>1000?'<small>GitHub search returns the first 1,000 matches.</small>':"")+'</div>');
  } catch(e) {
    if (authOpen(ctx)) {$("#github-results").innerHTML=button("Retry","github-page",null,"secondary",'data-page="'+page+'"');showError(e.message);}
  }
}
function parentField(project) {
  if (project?.global) return "";
  const options=state.projects.filter(p=>!includesProject(project,p)).map(p=>[p.name,p.name]);
  const global=state.projects.find(p=>p.global);
  return selectField("parent","Parent project",options,project?.parent||global?.name);
}
function projectsPage() {
  return section("Projects",button("Manage ignore list","ignorelist","ignore"),state.projects.map(p=>entry("projects",p.name,[p.global?"Workspace-wide":p.parent?"Under "+p.parent:null,...(p.repos||[])].filter(Boolean).join(" · ")||"No linked repositories",projectControls(p))).join("")||empty("No projects yet."));
}
const dayNames=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
function timing(s) {
  if (s.once_at) return "Once at "+fullDate(s.once_at);
  const days=s.days?.length && s.days.length<7 ? s.days.map(d=>dayNames[d]).join(", ") : "Every day";
  return days+" at "+s.time;
}
function scheduleState(s) {
  if (s.status==="active") return s.next?"Next "+fullDate(s.next):"Active";
  if (s.status==="cancelled") return "Cancelled by "+(s.cancelled_by==="user"?"you":"Hermes")+": "+(s.cancel_reason||"");
  return "Finished";
}
function contextPage() {
  const hermes = state.hermes?.last_call ? "Hermes last worked on context and schedules "+relativeDate(state.hermes.last_call)+"." :
    "Hermes hasn't worked on context yet. Once the app's Hermes is installed and signed in (Settings → Agents), it updates context and schedules during its sweeps.";
  const rank = (active,first) => active ? (first?0:1) : 2;
  const schedules = [...(state.schedules||[])].sort((a,b)=>rank(a.status==="active")-rank(b.status==="active")).map(s=>entry("clock",s.title,
    [timing(s),s.project,(s.origin==="inferred"?"Inferred: ":"You asked: ")+s.reason,scheduleState(s)].join(" · "),
    s.status==="active"?button("Cancel","schedule_cancel",null,"secondary",'data-id="'+esc(s.id)+'"'):"")).join("");
  // Stale entries first (Hermes reviews them next), then active, then retired.
  const entries = [...(state.context||[])].sort((a,b)=>rank(a.status==="active",a.stale)-rank(b.status==="active",b.stale)).map(e=>entry("context",e.subject,
    [titleCase(e.kind),e.project,e.status==="retired"?"Retired: "+(e.retired_reason||""):e.stale?"Stale, withheld from agents until Hermes reviews it":"Review "+compactDate(e.review_at),e.body].join(" · "),
    e.status==="retired"?button("Restore","context_restore",null,"quiet",'data-id="'+esc(e.id)+'"'):button("Retire","context_retire",null,"secondary",'data-id="'+esc(e.id)+'"'))).join("");
  return '<div class="policy-note">'+icon("context")+'<span>'+esc(hermes)+'</span></div>'+
    section("Schedules","",schedules||empty("No schedules yet. Ask for recurring work in a task, for example “every Friday, summarize open pull requests”."))+
    section("What agents know","",entries||empty("No context yet."));
}
const modes = {auto:"Allow autonomous handling",review:"Require review",user:"Leave to me"};
function policiesPage() {
  const ready = (state.handoffs||[]).map(h=>entry("shield",titleCase(h.kind)+" in "+h.project,
    h.delivered+" delivered in 30 days, none corrected, no missed work reported",
    button("Allow autonomous handling","handoff",null,"secondary",'data-project="'+esc(h.project)+'" data-kind="'+esc(h.kind)+'"'))).join("");
  return '<div class="policy-note">'+icon("shield")+'<span>'+esc(state.settings.dry?"Dry mode requires approval for every external write.":"Live mode follows the rules below.")+'</span></div>'+
    (ready?section("Ready to hand off","",ready):"")+
    (state.rules.length?state.rules.map(r=>entry("policies",titleCase(r.kind),r.project, '<span class="rule-mode">'+esc(modes[r.mode]||r.mode)+'</span>'+button("Edit","rule",null,"secondary",'data-project="'+esc(r.project)+'" data-kind="'+esc(r.kind)+'"'))).join(""):empty("No decision rules yet."));
}
function skillsPage() {
  return (state.skills.length?recent(state.skills,"at").map(s=>'<button class="skill-row" data-action="skill" data-id="'+esc(s.id)+'"><span class="source-icon">'+icon("skills")+'</span><span class="row-title"><strong>'+esc(titleCase(s.kind))+'</strong><small>'+esc(plain(s.body).split("\n").find(Boolean)||"Empty revision")+'</small></span><span class="status '+(s.active?'complete':'neutral')+'">'+esc(skillState(s))+'</span>'+icon("chevron",14)+'</button>').join(""):empty("No saved revisions."));
}
function skillState(s) {
  if (s.active) return "Active";
  if (s.rejected || ["rejected","declined"].includes(s.status)) return "Declined";
  if (s.reverted || s.status==="reverted") return "Reverted";
  return s.status ? titleCase(s.status) : s.kind==="style" ? "Awaiting review" : "Inactive";
}
function installationStatus(id) {
  if (providers === null) return "Checking installation…";
  const provider = providers.find(item=>item.id===id);
  if (!provider || typeof provider.installed!=="boolean") return "Installation status unavailable";
  return provider.installed ? "Installed" : "Missing";
}
function providerLabel(id) {
  return providerNames[id]+" · "+installationStatus(id);
}
async function loadProviders(force=false) {
  if (providersPending || (!force && Date.now()-providersChecked<30000)) return;
  providersPending=true; providersChecked=Date.now();
  try {
    const result=await request("/api/providers");
    providers=Array.isArray(result.providers)?result.providers:[];
  } catch { providers=[]; }
  finally { providersPending=false; }
  const select=$("#editor select[name=provider]");
  if (select) for (const option of select.options) {
    if (Object.hasOwn(providerNames,option.value)) option.textContent=providerLabel(option.value);
  }
  if (activeView==="settings") render();
}
function providerField() {
  const selected=state.settings.provider;
  const choices=Object.keys(providerNames).map(id=>[id,providerLabel(id)]);
  return '<label class="field"><span>Agent provider</span><select name="provider" required aria-describedby="provider-help">'+
    (Object.hasOwn(providerNames,selected)?"":'<option value="" selected disabled>Choose a provider</option>')+
    selectOptions(choices,selected)+'</select></label><div class="provider-help"><p id="provider-help">Install your chosen CLI and sign in from your terminal. The CLI may send task context to its configured AI service, including connected activity and code supplied for the task.</p>'+
    button("Check again","providers","sync","quiet")+'</div>';
}
async function loadAgents(force=false) {
  if (agentsPending || (!force && Date.now()-agentsChecked<30000)) return;
  agentsPending=true; agentsChecked=Date.now();
  try { agentSetup=await request("/api/agents"+(force?"?refresh=1":"")); }
  catch { agentSetup={harnesses:[]}; }
  finally { agentsPending=false; }
  if (activeView==="settings") render();
}
// MeMyselfAI installs its own Hermes, separate from any other Hermes. See docs/specs/hermes-orchestrator.md#install.
function hermesDetail(a,detail) {
  const install=a.install||{};
  if (install.status==="installing") return a.role+" · Installing: "+(install.step||"starting")+"…";
  if (install.status==="failed" && !a.installed) return a.role+" · Install failed: "+(install.error||"unknown error");
  if (!a.installed) return a.role+" · Installing on first start…";
  return detail+" · Open it directly with "+(a.command||"memyselfai-hermes")+(install.status==="failed"?" · Update failed: "+install.error:"");
}
function hermesControls(a) {
  const install=a.install||{};
  if (install.status==="installing") return "";
  return (a.installed?button("Open dashboard","hermes-dashboard",null,"secondary"):"")+(install.status==="failed"||!a.installed?button("Retry install","hermes-install","sync","quiet"):"");
}
async function hermesAction(op) {
  try {
    const result=await request("/api/action",{op});
    if (op==="hermes_install") { notify("Installing Hermes. This takes a few minutes."); agentsChecked=0; setTimeout(()=>loadAgents(true),3000); }
    else notify(result.opened?"The Hermes dashboard is starting in Terminal. Close that window to stop it.":"Run this on this machine, then open http://127.0.0.1:9119: "+result.command);
  } catch(e) { showError(e.message); }
}
function agentsSection() {
  if (agentSetup===null) return section("Agents","",empty("Checking agents…"));
  const rows=(agentSetup.harnesses||[]).map(a=>{
    const uses=a.id==="hermes"?" · Uses your "+(subscriptionNames[a.uses]||a.uses):"";
    const detail=a.role+" · "+(agentStates[a.state]||"Status unavailable")+(a.version?" · "+a.version:"")+uses+(a.state==="sign_in"?" · Run: "+a.sign_in:"")+(a.detail?" · "+a.detail:"");
    let controls=a.state==="sign_in"?button("Sign in","agent-sign-in",null,"secondary",'data-agent="'+esc(a.id)+'"'):"";
    if (a.id==="hermes") return entry("stack",a.label,hermesDetail(a,detail),controls+hermesControls(a));
    return entry("working",a.label,detail,controls);
  }).join("");
  return section("Agents",button("Check again","agents","sync","quiet"),rows||empty("Agent status unavailable.",button("Retry","agents","sync")));
}
const shortPrint = fp => (fp||"").slice(0,16).match(/.{1,4}/g)?.join(" ")||"";
function instancesSection() {
  const inst=state.instances;
  if (!inst) return "";
  const self=entry("stack",inst.name+" (this instance)",[titleCase(inst.platform),"Fingerprint "+shortPrint(inst.fingerprint),inst.listening?"Accepts paired instances on port "+inst.listening:"Dials out to paired instances",inst.owns+" items owned",inst.paired?"Orchestrator on "+inst.orchestrator:"",inst.checkouts?.length?"Checkouts: "+inst.checkouts.join(", "):""].filter(Boolean).join(" · "),
    button("Rename","instance-rename",null,"quiet")+button("Add checkout","instance-checkout",null,"quiet")+(inst.listening?button("Add instance","instance-code","add"):"")+button("Join","instance-join",null));
  const peers=inst.peers.map(p=>{
    const id='data-peer="'+esc(p.id)+'"', cap=p.capacity||{};
    const ready=Object.entries(cap.agents||{}).filter(([,v])=>v==="ready").map(([k])=>titleCase(k)).join(", ");
    const detail=[titleCase(p.platform),silent(p)?"Silent; its work moves here after 15 minutes":(p.state||"Not contacted yet"),cap.state&&cap.state!=="active"?titleCase(cap.state):"",cap.preference?preferenceNames[cap.preference]:"",cap.battery?"On battery":"",p.last_seen?"Last seen "+relativeDate(p.last_seen):"",cap.slots?(cap.free??0)+" of "+cap.slots+" agent slots free":"",ready?"Ready: "+ready:"","Fingerprint "+shortPrint(p.fingerprint),p.owns+" items owned"].filter(Boolean).join(" · ");
    const controls=button("Remove","instance-remove",null,"quiet",id);
    return entry("sync",p.name,detail,controls);
  }).join("");
  const departed=(inst.departed||[]).map(p=>entry("sync",p.name+" (removed)",p.owns+" items it can't hand over stay with it, such as work on its local folders.")).join("");
  return section("Instances","",self+departed+(peers||'<p class="section-note">Run MeMyselfAI on a VPS too and pair it here. Paired instances share tasks, logins and work, and talk to each other directly.</p>'));
}
const silent=p=>p.trusted&&(!p.last_seen||Date.now()/1000-p.last_seen>120);
function runOn(t) {
  const inst=state.instances;
  if (!inst?.peers.length || ["done","dismissed","skipped","sending","uncertain"].includes(t.status)) return "";
  const targets=[...(t.owner?[{id:inst.id,name:inst.name+" (here)"}]:[]),...inst.peers.filter(p=>p.trusted&&p.name!==t.owner)];
  return targets.map(p=>button("Run on "+p.name,"run-on",null,"quiet",'data-id="'+esc(t.id)+'" data-peer="'+esc(p.id)+'"')).join("");
}
const preferenceNames={first:"Run here first",overflow:"Overflow only",never:"Never run agents"};
async function pairingCode() {
  try {
    const r=await request("/api/action",{op:"instance_code"});
    showDialog("pairing","Add instance",'<p>On the other instance, choose <strong>Join</strong> and enter this instance\'s address with this code. It works once, for '+Math.round(r.expires_in/60)+' minutes.</p><p class="pairing-code"><code>'+esc(r.code)+'</code></p><p class="section-note">Once it joins, both instances share work right away. Connections move to an always-on server by themselves.</p>');
    await refresh();
  } catch(e) { notify(e.message); }
}
function settingsPage() {
  return section("Agent configuration","", entry("shield","Delivery mode",state.settings.dry?"Dry mode · External writes require your approval.":"Live mode · Project decision rules govern delivery.",button("Configure","settings",null))+
      entry("working","Agent provider",Object.hasOwn(providerNames,state.settings.provider)?providerLabel(state.settings.provider):"Choose a provider to prepare tasks.",button("Choose provider","settings",null)+button("Check again","providers","sync","quiet")))+
    agentsSection()+
    instancesSection()+
    section("Permissions","",entry("projects","Folder access","Choose which repository folder to check or update its macOS permission.",button("Manage folder access","folder-access",null)))+
    section("Work exclusions","",entry("projects","Ignore list",[(state.ignorelist?.projects||[]).length+" projects",(state.ignorelist?.repositories||[]).length+" repositories"].join(" · "),button("Edit ignore list","ignorelist",null)))+
    section("Sample workspace","",entry("stack","Load sample work","Add fixture tasks and activity to explore the workspace.",button("Load sample","demo",null)));
}
// Guided setup. See docs/specs/guided-setup.md.
const stepInfo={
  agent:{title:"Task agent",glyph:"working",why:"Prepares every task: reads the context, drafts replies and edits code. It runs on this Mac with your own Claude or ChatGPT sign-in."},
  accounts:{title:"Accounts",glyph:"connections",why:"Where work comes from. The app reads new activity every minute and turns requests and follow-ups into tasks."},
  projects:{title:"Projects",glyph:"projects",why:"Groups repositories so coding tasks get their own private copy, the right rules and the right reviewers."},
  orchestrator:{title:"Orchestrator",glyph:"stack",why:"Sweeps your accounts on a schedule, starts tasks on its own and posts a daily summary."},
  server:{title:"Always-on server",glyph:"sync",why:"Keeps discovery and tasks going while this Mac sleeps or is closed. Both share one workspace."},
  delivery:{title:"Delivery",glyph:"shield",why:"Decides what may leave without you. Every send and repository write waits for your approval until you change it."}
};
const pill=(kind,label)=>'<span class="status '+kind+'"><i></i>'+esc(label)+'</span>';
const vpsActive=()=>["running","needs_password"].includes(setup?.vps?.run?.status);
const ago=stamp=>{ const text=stamp?relativeDate(stamp):"never"; return text==="Just now"?"just now":text; };
async function loadSetup(force=false) {
  const wait=vpsActive()?1500:force?0:20000;
  if (setupPending || Date.now()-setupChecked<wait) return;
  setupPending=true; setupChecked=Date.now();
  try { setup=await request("/api/setup"+(force?"?refresh=1":"")); }
  catch { /* keep the last known state */ }
  finally { setupPending=false; }
  if (!setupOpened && setup) {
    setupOpened=true;
    if (!location.hash && setup.first_run) { location.hash="setup"; return; }
  }
  if (["overview","setup","inbox"].includes(activeView)) render();
}
// One state per step, used by the progress bar, the flow and the cards.
function stepState(s) {
  if (s.id==="server" && vpsActive()) return "active";
  if (s.ready) return s.id==="server" && s.peers.some(p=>!p.agent_ready||!p.online) ? "partial" : "done";
  if (s.id==="projects" && s.skipped) return "skipped";
  return s.required ? "needed" : "optional";
}
const stateLabels={done:"Ready",partial:"Almost ready",active:"Setting up",needed:"Needs you",optional:"Not set up",skipped:"Skipped"};
const stateKinds={done:"complete",partial:"attention",active:"progress",needed:"attention",optional:"neutral",skipped:"neutral"};
function setupProgress() {
  const steps=setup.steps, required=steps.filter(s=>s.required), optional=steps.filter(s=>!s.required&&s.id!=="delivery");
  const done=list=>list.filter(s=>["done","skipped"].includes(stepState(s))).length;
  const next=steps.find(s=>s.required&&!s.ready) || steps.find(s=>["optional","partial","active"].includes(stepState(s)));
  return {required:done(required),requiredTotal:required.length,optional:done(optional),optionalTotal:optional.length,next,total:done(steps),count:steps.length};
}
function progressBar(compact=false) {
  return '<div class="setup-bar'+(compact?' compact':'')+'" role="img" aria-label="'+esc(setupProgress().total+" of "+setup.steps.length+" steps done")+'">'+
    setup.steps.map(s=>'<span class="seg '+stepState(s)+'" title="'+esc(stepInfo[s.id].title+": "+stateLabels[stepState(s)])+'"></span>').join("")+'</div>';
}
function setupCard() {
  if (!setup || !setup.missing.length || setup.dismissed) return "";
  const p=setupProgress();
  return '<section class="setup-card"><div class="entry-text"><strong>Setup · '+p.required+' of '+p.requiredTotal+' required steps done</strong>'+progressBar(true)+
    '<p>Next: '+esc(stepInfo[p.next.id].title)+'. '+esc(stepSummary(p.next))+'</p></div><div class="entry-actions">'+
    '<a class="btn primary" href="#setup">Continue setup</a>'+button("Dismiss","setup-dismiss",null,"quiet")+'</div></section>';
}
function stepSummary(s) {
  const agent=providerNames[s.agent]||s.label;
  switch (s.id) {
    case "agent": return s.ready?agent+" is installed and signed in.":s.state==="sign_in"?agent+" is installed. Sign in once.":s.state==="not_installed"?"Install "+agent+" on this Mac, then choose Check again.":agent+": "+(agentStates[s.state]||"status unavailable")+".";
    case "accounts": return s.ready?s.count+(s.count===1?" account":" accounts")+" connected.":s.github_login?"Your GitHub CLI is signed in as @"+s.github_login+". Connect it in one click.":"Connect mail, Slack or GitHub.";
    case "projects": return s.ready?s.count+(s.count===1?" project has":" projects have")+" repositories.":s.skipped?"Skipped. Tasks go to Global until you add a project.":"Add a project with its repository folders, or skip for now.";
    case "orchestrator": return s.ready?"Hermes is ready.":s.state==="sign_in"?"Hermes is installed. Sign in once.":"The app is installing its own Hermes. Until it is ready, tasks start from new activity only.";
    case "server": {
      if (vpsActive()) return "Setting up "+setup.vps.run.host+"…";
      if (!s.ready) return "Pick one of your SSH hosts. The app installs, starts and pairs it.";
      const p=s.peers[0], left=[!p.online&&"it has been silent since "+ago(p.last_seen),!p.agent_ready&&"its task agent needs a sign-in"].filter(Boolean);
      return "Paired with "+p.name+(left.length?". Left: "+left.join(", ")+".":", online and syncing.");
    }
    case "delivery": return s.dry?"Dry mode. You approve each send and repository write.":"Live mode. Project decision rules govern delivery.";
  }
  return "";
}
function stepActions(s) {
  switch (s.id) {
    case "agent": return (s.state==="sign_in"?button("Sign in","agent-sign-in",null,"primary",'data-agent="'+esc(s.agent)+'"'):"")+(s.ready?"":'<a class="btn quiet" href="#settings">Change agent</a>');
    case "accounts": return s.ready?'<a class="btn quiet" href="#connections">Manage</a>':(s.github_login?button("Connect GitHub as @"+s.github_login,"setup-github","github","primary"):"")+'<a class="btn secondary" href="#connections">Other accounts</a>';
    case "projects": return s.ready?'<a class="btn quiet" href="#projects">Manage</a>':button("Add project","project","add","secondary")+(s.skipped?"":button("Skip","setup-skip",null,"quiet",'data-step="projects"'));
    case "orchestrator": return s.state==="sign_in"?button("Sign in","agent-sign-in",null,"secondary",'data-agent="hermes"'):"";
    case "server": return s.ready&&s.peers.some(p=>!p.agent_ready)?button("Sign in on server","vps-sign-in",null,"primary"):"";
    case "delivery": return '<a class="btn quiet" href="#settings">Change</a>';
  }
  return "";
}
const check=(ok,label,detail="",action="")=>'<li class="'+(ok===true?"ok":ok===false?"todo":"info")+'"><span class="mark">'+(ok===true?icon("check",14):ok===false?"":icon("dot",10))+'</span><span><strong>'+esc(label)+'</strong>'+(detail?'<small>'+esc(detail)+'</small>':"")+'</span>'+action+'</li>';
function serverChecklist(s) {
  if (!s.ready) return "";
  const place=s.placement||{};
  return s.peers.map(p=>{
    const items=[
      check(true,"Installed and paired",p.address),
      check(p.online,p.online?"Online and syncing":"Not heard from recently","Last seen "+ago(p.last_seen)+(p.error?" · "+p.error:"")),
      check(p.agent_ready,p.agent_ready?"Task agent signed in on the server":"Sign in the task agent on the server",p.agent_ready?p.free+" of "+p.slots+" agent slots free. Queued tasks move there when this Mac is full or asleep.":"Without it the server can't run tasks.",p.agent_ready?"":button("Sign in on server","vps-sign-in",null,"secondary")),
      check(p.hermes_ready?true:null,p.orchestrator?"Orchestrator runs on the server":p.hermes_ready?"Orchestrator moves to the server":"Orchestrator runs on this Mac",p.hermes_ready||p.orchestrator?"":"Install Hermes on the server to keep sweeps going while this Mac sleeps."),
      check(null,"Polling: "+p.connections+" on the server, "+place.here+" on this Mac",place.local_only?place.local_only+(place.local_only===1?" uses a login":" use logins")+" that stay on this Mac, such as the GitHub CLI, so this Mac keeps polling "+(place.local_only===1?"it":"them")+". While it sleeps, they wait until it wakes.":"Connections move to the server by themselves.")
    ];
    return '<ul class="setup-checklist" aria-label="'+esc(p.name)+' status">'+items.join("")+'</ul>';
  }).join("");
}
function vpsForm(open) {
  const options=setup.vps.hosts.map(h=>'<option value="'+esc(h)+'"></option>').join("");
  const value=hostDraft||setup.vps.run?.host||setup.vps.hosts.find(h=>/vps|server/i.test(h))||setup.vps.hosts[0]||"";
  const form='<form class="vps-form" id="vps-form"><label class="field"><span>SSH host</span><input id="vps-host" name="host" list="ssh-hosts" autocomplete="off" spellcheck="false" required value="'+esc(value)+'" placeholder="A Host from ~/.ssh/config, or user@host"><datalist id="ssh-hosts">'+options+'</datalist>'+
    '<small>'+(setup.vps.hosts.length?setup.vps.hosts.length+" hosts in your SSH config. ":"No hosts in ~/.ssh/config. ")+'The app connects, copies itself, installs the service, opens the sync port in ufw, installs your task agent and pairs. Running it again updates the server.</small></label>'+
    '<button type="submit" class="btn primary">'+(setup.vps.run?.status==="failed"?"Retry setup":open?"Set up server":"Update or add server")+'</button></form>';
  return open?form:'<details class="vps-more"><summary>Update this server or set up another</summary>'+form+'</details>';
}
function vpsProgress() {
  const run=setup?.vps?.run;
  if (!run) return "";
  const mark={done:"complete",running:"progress",failed:"attention",pending:"neutral"};
  const names={done:"Done",running:"Working",failed:"Failed",pending:"Waiting"};
  const finished=run.steps.filter(st=>st.status==="done").length, total=run.steps.length;
  const current=run.steps.find(st=>st.status==="running"||st.status==="failed");
  const steps=run.steps.map(st=>'<li>'+pill(mark[st.status],names[st.status])+'<span><strong>'+esc(st.label)+'</strong>'+((st.detail||st.seconds!=null)?'<small>'+esc([st.detail,st.seconds!=null?st.seconds+" s":""].filter(Boolean).join(" · "))+'</small>':"")+'</span></li>').join("");
  const head={running:"Setting up "+run.host+": step "+Math.min(finished+1,total)+" of "+total+(current?", "+current.label.toLowerCase():""),needs_password:run.host+" needs your sudo password",failed:"Setup of "+run.host+" stopped at step "+(finished+1)+" of "+total,done:run.host+" is set up",cancelled:"Setup of "+run.host+" cancelled"}[run.status]||run.host;
  const password=run.status==="needs_password"?'<form class="vps-form" id="vps-password-form"><label class="field"><span>Sudo password for '+esc(run.host)+'</span><input id="vps-password" type="password" autocomplete="current-password" required>'+
    '<small>'+esc(run.password_error||"Used once for this setup and never saved.")+'</small></label><button type="submit" class="btn primary">Continue</button>'+button("Cancel","vps-cancel",null,"quiet")+'</form>':"";
  const cancel=run.status==="running"?button("Cancel","vps-cancel",null,"quiet"):"";
  const bar='<div class="run-bar"><span style="width:'+Math.round(100*finished/total)+'%"></span></div>';
  return '<div class="vps-run"><div class="section-title"><h3>'+esc(head)+'</h3>'+cancel+'</div>'+bar+(run.error?'<p class="vps-error" role="alert">'+esc(run.error)+'</p>':"")+password+'<ol class="vps-steps">'+steps+'</ol></div>';
}
function setupPage() {
  if (!setup) return empty("Checking your setup…");
  const p=setupProgress();
  const headline=setup.missing.length?p.required+" of "+p.requiredTotal+" required steps done":"Ready to work";
  const sub=setup.missing.length?"The app checks each step itself and does what it can. The rest is marked Needs you.":"Required steps are done. "+p.optional+" of "+p.optionalTotal+" optional steps are set up.";
  const next=p.next?'<div class="setup-next"><span class="eyebrow">Next</span><strong>'+esc(stepInfo[p.next.id].title)+'</strong><p>'+esc(stepSummary(p.next))+'</p><div class="entry-actions">'+(stepActions(p.next)||"")+button("Go to step","setup-jump",null,"quiet",'data-step="'+p.next.id+'"')+'</div></div>':
    '<div class="setup-next done"><span class="eyebrow">All set</span><strong>Everything is set up</strong><p>New activity becomes tasks on its own. You only approve what leaves.</p></div>';
  const flow='<ol class="setup-flow">'+setup.steps.map((s,i)=>'<li><button type="button" class="flow-step '+stepState(s)+'" data-action="setup-jump" data-step="'+s.id+'"><span class="num">'+(stepState(s)==="done"?icon("check",12):i+1)+'</span>'+esc(stepInfo[s.id].title)+(s.required||s.id==="delivery"?'':' <small>optional</small>')+'</button></li>').join("")+'</ol>';
  const header='<div class="setup-head"><div><h2>'+esc(headline)+'</h2><p class="section-note">'+esc(sub)+'</p></div>'+button("Check again","setup-refresh","sync","quiet")+'</div>'+progressBar()+flow+next;
  const cards=setup.steps.map((s,i)=>{
    const st=stepState(s);
    const extra=s.id==="server"?serverChecklist(s)+(!vpsActive()?vpsForm(!s.ready):"")+'<div id="vps-progress">'+vpsProgress()+'</div>':"";
    return '<article class="setup-step '+st+'" id="step-'+s.id+'"><div class="step-num">'+(st==="done"?icon("check",14):i+1)+'</div><div class="step-body">'+
      '<div class="step-head"><h3>'+esc(stepInfo[s.id].title)+'</h3>'+pill(stateKinds[st],stateLabels[st])+(s.required||s.id==="delivery"?"":'<span class="optional-tag">Optional</span>')+'<div class="entry-actions">'+stepActions(s)+'</div></div>'+
      '<p class="step-status">'+esc(stepSummary(s))+'</p><p class="step-why">'+esc(stepInfo[s.id].why)+'</p>'+extra+'</div></article>';
  }).join("");
  const how='<details class="setup-how"><summary>How work flows once set up</summary><ol>'+
    '<li>Connected accounts are read every minute, on this Mac or the server.</li>'+
    '<li>Requests and follow-ups become tasks, each in its project.</li>'+
    '<li>A fresh task agent prepares each one: a reply, an answer or a code change in a private copy.</li>'+
    '<li>Anything that would leave, such as a reply or a pull request, waits for your approval in dry mode.</li>'+
    '<li>The task stays open until its outcome is verified.</li></ol></details>';
  return '<section class="block setup-page">'+header+cards+how+'</section>';
}
async function vpsStart(host) {
  try { await request("/api/action",{op:"vps_setup",host}); hostDraft=""; setupChecked=0; await loadSetup(); }
  catch(e) { notify(e.message); }
}
async function vpsSignIn() {
  try {
    const r=await request("/api/action",{op:"vps_sign_in"});
    notify(r.opened?"Sign-in opened in Terminal. Finish it there; the server reports ready within a minute.":"Run this in a terminal: "+r.command);
  } catch(e) { notify(e.message); }
}
async function setupMutate(payload,message) {
  try { const r=await request("/api/action",payload); if (message) notify(typeof message==="function"?message(r):message); }
  catch(e) { notify(e.message); }
  setupChecked=0; void refresh(); void loadSetup(true);
}
// Inbox view. See docs/specs/dashboard.md#inbox-view.
let inboxFilter="all", selectedTask=null, detailTab="activity", inboxNarrowOpen=false;
let inboxSort=localStorage.getItem("inbox-sort")||"priority", autoGroup=localStorage.getItem("inbox-autogroup")!=="off";
const collapsedGroups=new Set(), collapsedCallouts=new Set(), inboxDrafts=new Map();
const inboxKinds={decision:"Decision",failed:"Failed",blocked:"Blocked"};
function inboxKind(t) {
  if (t.status==="failed") return "failed";
  if (t.status==="uncertain" || (t.status==="question" && t.checkout_inaccessible)) return "blocked";
  if (["review","question"].includes(t.status)) return "decision";
  return "";
}
const pillNames={failed:"Failed",review:"Needs approval",question:"Needs answer",uncertain:"Blocked"};
function inboxPill(t) {
  const kind=inboxKind(t);
  const label=kind==="blocked"?"Blocked":pillNames[t.status]||statusNames[t.status]||titleCase(t.status);
  return '<span class="pill '+(kind||(groups.progress.includes(t.status)?"progress":t.status==="done"?"done":"neutral"))+'">'+esc(label)+'</span>';
}
const kindGlyph={failed:"alert",decision:"question",blocked:"pause"};
function lastEvents() {
  const seen=new Map();
  for (const a of state.activity) if (a.task!=null) {
    const key=String(a.task);
    if (!seen.has(key) || Number(a.at)>seen.get(key)) seen.set(key,Number(a.at));
  }
  return seen;
}
function errorLine(t) {
  return plain(t.result||t.reason||"The agent stopped without a message.").split("\n").map(s=>s.trim()).find(Boolean)||"Unknown error";
}
function errorHead(t) {
  const line=errorLine(t), cut=line.search(/[.!?](\s|$)/);
  return cut>0 && cut<160?line.slice(0,cut+1):line.slice(0,160);
}
const errorKey=t=>errorHead(t).toLowerCase().replace(/(['"`]).*?\1|\/\S+|\d+/g,"…").slice(0,140);
function inboxTasks() {
  const all=state.tasks.filter(t=>inboxKind(t));
  const shown=all.filter(t=>inboxFilter==="all" || inboxKind(t)===inboxFilter);
  const rank={failed:0,decision:1,blocked:2};
  shown.sort((a,b)=>(inboxSort==="priority"?rank[inboxKind(a)]-rank[inboxKind(b)]:0) || Number(b.created||0)-Number(a.created||0));
  return {all,shown};
}
function inboxScope(t) {
  const repo=taskRepository(t), value=repo||t.project;
  if (!value) return "";
  const scope=repo?"repository":"project";
  const {name}=taskContext(t).find(([label])=>label==="Source")?.[2]||{name:"Local"};
  return '<span class="inbox-scope">'+sourceMark(name,14)+'<button type="button" class="scope-link" data-action="'+scope+'-details" data-value="'+esc(value)+'" data-id="'+esc(t.id)+'" aria-haspopup="dialog" title="Open '+scope+'">'+esc(value)+'</button>'+
    (repo && t.project?'<span class="muted">· '+esc(t.project)+'</span>':"")+'</span>';
}
function rowShortcuts(t) {
  return taskActionNames(t).map(action=>{
    const [label,glyph,key]=taskShortcuts[action];
    return '<button type="button" class="iconbtn" data-action="'+action+'" data-id="'+esc(t.id)+'" aria-label="'+esc(label+" · "+t.title)+'" aria-keyshortcuts="'+key+'" title="'+label+' ('+key+')">'+icon(glyph,15)+'</button>';
  }).join("");
}
function inboxRow(t,grouped=false) {
  const kind=inboxKind(t), selected=String(selectedTask)===String(t.id);
  return '<div class="task-entry inbox-row '+kind+(grouped?" grouped":"")+(selected?" selected":"")+'" data-row="'+esc(t.id)+'">'+
    (grouped?"":'<span class="row-icon">'+icon(kindGlyph[kind],18)+'</span>')+
    '<div class="row-main"><button type="button" class="task-open" data-action="inbox-select" data-id="'+esc(t.id)+'" aria-current="'+selected+'"><strong>'+esc(t.title)+'</strong></button>'+
    inboxScope(t)+(t.status==="question" && t.answer_choices?'<p class="row-question">'+esc(plain(t.question).slice(0,200))+'</p>':"")+answerChoices(t)+'</div>'+
    '<div class="row-side">'+inboxPill(t)+'<div class="row-meta"><div class="row-shortcuts">'+rowShortcuts(t)+'</div><time title="'+esc(fullDate(t.created))+'">'+esc(relativeDate(t.created))+'</time></div></div></div>';
}
// Auto-group: same-error failures first (they share a cause and a Retry all), then the
// remaining rows that share a repository, or a project when the task names no repository.
const repoKey=t=>{const place=taskRepository(t)||t.project; return place?"repo:"+String(place).toLowerCase():"";};
function inboxGroups(shown) {
  const byError=new Map(), byRepo=new Map(), owner=new Map();
  if (!autoGroup) return owner;
  for (const t of shown) if (inboxKind(t)==="failed") { const k="error:"+errorKey(t); byError.set(k,[...(byError.get(k)||[]),t]); }
  for (const [k,members] of byError) if (members.length>1) members.forEach(m=>owner.set(m.id,k));
  for (const t of shown) if (!owner.has(t.id) && repoKey(t)) { const k=repoKey(t); byRepo.set(k,[...(byRepo.get(k)||[]),t]); }
  for (const [k,members] of byRepo) if (members.length>1) members.forEach(m=>owner.set(m.id,k));
  return owner;
}
function inboxList(shown) {
  if (!shown.length) return '<div class="inbox-empty">'+icon("done",28)+'<strong>'+(inboxFilter==="all"?"Nothing needs you":"Nothing in "+inboxKinds[inboxFilter])+'</strong><p>Agents keep working. Tasks appear here when one needs a decision, fails or is blocked.</p></div>';
  const owner=inboxGroups(shown), done=new Set();
  const toggle=(key,open)=>'<button type="button" class="iconbtn plain" data-action="inbox-group" data-key="'+esc(key)+'" aria-expanded="'+open+'" aria-label="'+(open?"Collapse":"Expand")+' group">'+icon(open?"up":"down",16)+'</button>';
  return shown.map(t=>{
    if (done.has(t.id)) return "";
    const key=owner.get(t.id);
    if (!key) return inboxRow(t);
    const members=shown.filter(m=>owner.get(m.id)===key);
    members.forEach(m=>done.add(m.id));
    const open=!collapsedGroups.has(key), rows=open?'<div class="group-rows">'+members.map(m=>inboxRow(m,true)).join("")+'</div>':"";
    if (key.startsWith("error:")) {
      const places=[...new Set(members.map(m=>taskRepository(m)||m.project).filter(Boolean))];
      return '<section class="inbox-group failed"><div class="group-head"><span class="row-icon">'+icon("stack",18)+'</span><div class="row-main"><strong>'+members.length+' tasks failed: '+esc(errorHead(t))+'</strong>'+
        '<p>Affecting '+members.length+' tasks'+(places.length?" in "+esc(places.slice(0,3).join(", "))+(places.length>3?" and "+(places.length-3)+" more":""):"")+'.</p></div>'+
        '<div class="row-side">'+button("Retry all","inbox-retry-all","sync","secondary",'data-key="'+esc(key.slice(6))+'"')+'<span class="pill failed">Failed</span>'+toggle(key,open)+'</div></div>'+rows+'</section>';
    }
    const place=taskRepository(t)||t.project, counts={failed:0,decision:0,blocked:0};
    members.forEach(m=>counts[inboxKind(m)]++);
    const summary=[["failed","failed"],["decision","decision"],["blocked","blocked"]].filter(([k])=>counts[k]).map(([k,w])=>counts[k]+" "+w+(counts[k]>1&&k==="decision"?"s":"")).join(", ");
    return '<section class="inbox-group repo"><div class="group-head"><span class="row-icon">'+icon("stack",18)+'</span><div class="row-main"><strong>'+members.length+' tasks in '+esc(place)+'</strong>'+
      '<p>'+esc(summary)+'.</p></div><div class="row-side">'+toggle(key,open)+'</div></div>'+rows+'</section>';
  }).join("");
}
function inboxHead(all) {
  const events=lastEvents(), day=Date.now()/1000-86400;
  const progress=state.tasks.filter(t=>groups.progress.includes(t.status)).length;
  const completed=state.tasks.filter(t=>t.status==="done" && (events.get(String(t.id))||Number(t.created||0))>day).length;
  const count=kind=>all.filter(t=>inboxKind(t)===kind).length;
  const card=(cls,glyph,n,label,action,f)=>'<button type="button" class="stat '+cls+'" data-action="'+action+'" data-filter="'+f+'"><span class="stat-top">'+icon(glyph,22)+'<strong>'+n+'</strong></span><span>'+label+'</span></button>';
  const chip=(key,label,n)=>'<button type="button" class="chip '+key+'" data-action="inbox-filter" data-filter="'+key+'" aria-pressed="'+(inboxFilter===key)+'">'+(key==="all"?"":'<i></i>')+esc(label)+'<span>'+n+'</span></button>';
  return '<div class="inbox-title"><div><h1>Inbox</h1><p>Tasks that need you to keep agents moving.</p></div><div class="inbox-tools">'+
    '<label class="switch"><input type="checkbox" id="auto-group" '+(autoGroup?"checked":"")+'><span aria-hidden="true"></span>Auto-group</label>'+
    '<details class="more-menu"><summary class="iconbtn plain" aria-label="More inbox actions">'+icon("more",18)+'</summary><div class="menu">'+
    button("New task","task","add","quiet")+button("Manage ignore list","ignorelist","ignore","quiet")+'<a class="btn quiet" href="#tasks">'+icon("tasks",16)+'All tasks</a></div></details></div></div>'+
    '<div class="stats">'+card("attention","alert",all.length,"Needs attention","inbox-filter","all")+card("progress","sync",progress,"In progress","filter","progress")+
    card("complete","check",completed,"Completed (24h)","filter","completed")+card("blocked","bars",count("blocked"),"Blocked","inbox-filter","blocked")+'</div>'+
    '<div class="chips"><div class="chip-row">'+chip("all","All",all.length)+chip("decision","Decision",count("decision"))+chip("failed","Failed",count("failed"))+chip("blocked","Blocked",count("blocked"))+'</div>'+
    '<label class="sort">Sort by: <select id="inbox-sort">'+selectOptions([["priority","Priority"],["newest","Newest"]],inboxSort)+'</select></label></div>';
}
function prNumber(t) {
  const m=String(t.url||"").match(/\/(?:pull|issues)\/(\d+)/) || String(t.title||"").match(/#(\d+)\b/);
  return m?m[1]:"";
}
function eventGlyph(kind) {
  const k=String(kind).toLowerCase();
  if (/fail|error|reject|denied/.test(k)) return ["x","bad"];
  if (/approv|deliver|sent|done|complete|verified|receipt/.test(k)) return ["check","good"];
  if (/question|answer|waiting|feedback|user/.test(k)) return ["user","wait"];
  if (/start|created|discover|queued|retry/.test(k)) return ["play","good"];
  return ["settings","neutral"];
}
function taskTimeline(t) {
  const events=recent(state.activity.filter(a=>a.task!=null && String(a.task)===String(t.id)),"at").reverse();
  const steps=events.map(a=>{
    const [glyph,tone]=eventGlyph(a.kind);
    return '<li><span class="step-icon '+tone+'">'+icon(glyph,15)+'</span><div><strong>'+esc(titleCase(a.kind))+'</strong><p>'+esc(plain(a.detail))+'</p></div><time title="'+esc(fullDate(a.at))+'">'+esc(relativeDate(a.at))+'</time></li>';
  });
  if (inboxKind(t)) steps.push('<li><span class="step-icon wait">'+icon("user",15)+'</span><div><strong>Waiting for you</strong><p>'+esc(t.status==="review"?"Review the proposal before anything leaves.":t.status==="uncertain"?"Check the destination and record what happened.":t.status==="failed"?"Retry, or instruct the agent how to proceed.":"Answer to unblock the task.")+'</p></div></li>');
  return steps.length?'<ol class="stepper">'+steps.join("")+'</ol>':empty("No activity recorded yet.");
}
function inboxCallout(t) {
  const id=String(t.id), open=!collapsedCallouts.has(id);
  const box=(cls,title,body)=>'<div class="callout '+cls+'"><div class="callout-head">'+icon(cls==="failed"?"alert":cls==="blocked"?"pause":"question",20)+'<strong>'+title+'</strong>'+
    '<button type="button" class="iconbtn plain" data-action="callout-toggle" data-id="'+esc(id)+'" aria-expanded="'+open+'" aria-label="'+(open?"Hide":"Show")+' details">'+icon(open?"up":"down",16)+'</button></div>'+(open?'<div class="callout-body">'+body+'</div>':"")+'</div>';
  if (t.status==="failed") {
    const text=plain(t.result||t.reason||"The agent stopped without a message.");
    const head=errorHead(t), rest=text.trim().slice(head.length).trim();
    return box("failed",esc(head),rest?'<p class="message-text">'+esc(rest)+'</p>':'<p>Retry, or tell the agent how to proceed below.</p>');
  }
  if (t.status==="uncertain") return box("blocked","The delivery outcome is uncertain.",'<p>Check '+esc(t.destination||"the destination")+' before recording what happened. Nothing will be resent until you do.</p>');
  if (t.status==="question") {
    const q='<div class="markdown-body">'+taskMarkdown.render(plain(t.question||"The agent needs an answer."))+'</div>'+(t.question_context?'<div class="markdown-body">'+taskMarkdown.render(t.question_context)+'</div>':"");
    return box(t.checkout_inaccessible?"blocked":"decision",t.checkout_inaccessible?"The agent can't open the checkout folder.":"The agent needs an answer.",q+(answerChoices(t,true)||answerChoices(t)));
  }
  return "";
}
const primaryActions={review_proposal:["Review & approve","inspect"],answer:["Answer","reply"],"folder-access":["Allow folder access","projects"],retry:["Retry","sync"],reconcile:["Record outcome","check"]};
function taskPrimary(t, names=taskActionNames(t)) {
  const first=names.find(n=>primaryActions[n] && !(n==="answer" && t.answer_choices?.length) && !(n==="retry" && t.status!=="failed"));
  return first?[first,...primaryActions[first]]:null;
}
function inboxActions(t) {
  const id='data-id="'+esc(t.id)+'"', names=taskActionNames(t), primary=primaryActions;
  const first=taskPrimary(t,names)?.[0];
  const buttons=names.filter(n=>n!=="feedback").map(n=>{
    const [,glyph,key]=taskShortcuts[n], [label]=n===first?primary[n]:taskShortcuts[n];
    return button(label,n,glyph,n===first?"primary":"secondary",id+' aria-keyshortcuts="'+key+'" title="'+esc(label)+' ('+key+')"');
  });
  if (names.includes("feedback")) buttons.splice(first?1:0,0,button("Instruct","inbox-instruct","review","secondary",id+' aria-keyshortcuts="F" title="Instruct (F)"'));
  const more=button("Open full details","detail","inspect","quiet",id)+runOn(t)+(taskExclusions(t)||button("Manage ignore list","ignorelist","ignore","quiet"));
  return '<div class="detail-bar">'+buttons.join("")+'<details class="more-menu"><summary class="iconbtn" aria-label="More task actions">'+icon("more",18)+'</summary><div class="menu">'+more+'</div></details></div>';
}
function inboxDetail(t) {
  if (!t) return '<div class="inbox-empty">'+icon("inbox",28)+'<strong>Select a task</strong><p>Its activity, details and controls open here.</p></div>';
  const repo=taskRepository(t), number=prNumber(t);
  const {name}=taskContext(t).find(([label])=>label==="Source")?.[2]||{name:"Local"};
  const chips='<div class="detail-chips"><span class="tag">'+sourceMark(name,15)+esc(repo||t.project||name)+'</span>'+(number?'<span class="tag">#'+esc(number)+'</span>':"")+'</div>';
  const top='<div class="detail-top"><button type="button" class="btn quiet back" data-action="inbox-back">'+icon("back",16)+'Back to inbox</button>'+chips+
    '<div class="detail-icons">'+(t.url?sourceReference(t.url,"Open source"):"")+'<button type="button" class="iconbtn plain" data-action="detail" '+'data-id="'+esc(t.id)+'" aria-label="Open full details" title="Open full details">'+icon("expand",17)+'</button></div></div>';
  const activity=recent(state.activity.filter(a=>a.task!=null&&String(a.task)===String(t.id)),"at");
  const reason=t.surfaced_reason || activity.findLast(a=>a.kind==="Discovery")?.detail || t.reason;
  const results=(present(t.result) && t.status!=="failed"?group("Result & evidence",quote(t.result)):"")+
    (t.milestones && Object.keys(t.milestones).length?group("Verified milestones",facts(Object.entries(t.milestones))):"")+
    (present(t.feedback)?group("Feedback & answers",t.feedback.map(f=>quote(f.text||[f.question,f.answer].filter(Boolean).join("\n"))).join("")):"")+
    (present(t.receipts)?group("Delivery receipts",t.receipts.map(r=>facts([["Operation",r.operation],["Recorded",fullDate(r.at)],["Receipt",r.receipt]])+quote(r.text)).join("")):"");
  const tabs=[
    ["activity","Activity",taskTimeline(t)],
    ["details","Details",facts([...taskContext(t),["Status",statusNames[t.status]||t.status],["Opened",fullDate(t.created)],["Due",fullDate(t.due)],["Decision type",t.kind],["Version",t.version]])+results],
    ...(t.worktree||t.repository?[["files","Files",facts([["Worktree",t.worktree],["Local checkout",t.repository],["Repository",repo]])]]:[]),
    ["context","Context",(present(reason)?group("Why this surfaced",quote(reason)):"")+(t.url?'<dl class="facts"><div><dt>Source address</dt><dd>'+sourceReference(t.url)+'</dd></div></dl>':"")+
      (present(t.body)?group("Task context",'<div class="markdown-body">'+taskMarkdown.render(t.body)+'</div>'):"")+(t.evidence?.length?group("Supporting activity",evidenceList(t.evidence)):"") || empty("No context recorded.")]
  ];
  if (!tabs.some(([key])=>key===detailTab)) detailTab="activity";
  const proposal=t.status==="review" || present(t.draft) && t.status!=="done" ? proposalPreview(t) : "";
  return top+'<h2 class="detail-title">'+esc(t.title)+'</h2><div class="detail-status">'+inboxPill(t)+'<time title="'+esc(fullDate(t.created))+'">'+esc(relativeDate(t.created))+'</time></div>'+
    inboxCallout(t)+inboxActions(t)+proposal+
    '<div class="detail-tabs" role="tablist" aria-label="Task sections">'+tabs.map(([key,label])=>'<button type="button" role="tab" data-action="inbox-tab" data-tab="'+key+'" aria-selected="'+(key===detailTab)+'">'+label+'</button>').join("")+'</div>'+
    '<section class="detail-panel" role="tabpanel">'+tabs.find(([key])=>key===detailTab)[2]+'</section>';
}
function composerState(t) {
  if (!t) return {allowed:false,reason:"Select a task to instruct its agent."};
  const allowed=taskActionNames(t).includes("feedback");
  const answering=t.status==="question" && !t.checkout_inaccessible && !t.repository_question && !t.calendar_question;
  const reason=allowed?"":t.status==="uncertain"?"Record the delivery outcome first.":t.status==="sending"?"The agent is sending. Wait for the receipt.":"This task is "+(statusNames[t.status]||t.status).toLowerCase()+".";
  return {allowed,answering,reason};
}
function inboxComposer(t) {
  const c=composerState(t), draft=t?inboxDrafts.get(String(t.id))||"":"";
  return '<form id="instruct-form" data-id="'+esc(t?.id??"")+'" data-version="'+esc(t?.version??"")+'" data-answering="'+!!c.answering+'"><label class="sr-only" for="instruct-text">'+(c.answering?"Answer the agent":"Instruct the agent")+'</label>'+
    '<textarea id="instruct-text" name="text" rows="2" placeholder="'+esc(c.allowed?(c.answering?"Answer the agent…":"Instruct the agent…"):c.reason)+'" '+(c.allowed?"":"disabled")+'>'+esc(draft)+'</textarea>'+
    '<div class="composer-foot"><small>'+(c.allowed?"⌘↵ to send. The agent resumes with your words.":"")+'</small><button type="submit" class="btn primary send" aria-label="Send" '+(c.allowed?"":"disabled")+'>'+icon("send",16)+'</button></div></form>';
}
const inboxSkeleton='<div class="inbox" id="inbox"><section class="inbox-list" aria-label="Tasks that need you"><div id="inbox-head"></div><div id="inbox-rows"></div></section>'+
  '<aside class="inbox-detail" aria-label="Open task"><div id="inbox-detail"></div><div id="inbox-composer"></div></aside></div>';
function renderInbox() {
  const {all,shown}=inboxTasks();
  if (selectedTask==null || !findTask(selectedTask)) selectedTask=shown[0]?.id ?? null;
  const t=selectedTask!=null?findTask(selectedTask):null;
  $("#inbox").classList.toggle("detail-open",inboxNarrowOpen && !!t);
  setIfChanged($("#inbox-head"),inboxHead(all));
  setIfChanged($("#inbox-rows"),inboxList(shown));
  setIfChanged($("#inbox-detail"),inboxDetail(t));
  const c=composerState(t), signature=[t?.id,t?.version,c.allowed,c.answering].join("|"), box=$("#inbox-composer");
  if (box.dataset.signature!==signature) {
    const focused=box.contains(document.activeElement);
    box.innerHTML=inboxComposer(t); box.dataset.signature=signature;
    if (focused && c.allowed) { const area=$("#instruct-text"); area.focus({preventScroll:true}); area.setSelectionRange(area.value.length,area.value.length); }
  }
}
function selectInboxTask(id,{focusRow=false,open=true}={}) {
  selectedTask=id; detailTab="activity"; if (open) inboxNarrowOpen=true;
  renderInbox();
  if (focusRow) document.querySelector('.inbox-row[data-row="'+CSS.escape(String(id))+'"] .task-open')?.focus();
}
async function sendInstruction(form) {
  const t=findTask(form.dataset.id), area=form.elements.text, text=area.value.trim();
  if (!t || !text || running) return;
  const answering=form.dataset.answering==="true";
  const payload=answering?{op:"answer",id:t.id,text,version:Number(form.dataset.version)}:{op:"feedback",id:t.id,text};
  const send=form.querySelector('[type="submit"]'); send.disabled=true; running=true;
  try {
    await request("/api/action",payload);
    inboxDrafts.delete(String(t.id)); area.value="";
    notify(answering?"Answer submitted. The agent resumes.":"Instruction sent. The agent resumes with it.");
  } catch(e) { notify(e.message); }
  finally { running=false; send.disabled=false; await refresh(); }
}
async function retryGroup(key) {
  const members=state.tasks.filter(t=>inboxKind(t)==="failed" && errorKey(t)===key);
  let done=0;
  for (const t of members) { try { await request("/api/action",{op:"retry",id:t.id}); done++; } catch { /* reported below */ } }
  notify(done===members.length?done+" tasks queued for retry.":done+" of "+members.length+" tasks queued. Retry the rest one by one.");
  await refresh();
}
const pages = {inbox:()=>inboxSkeleton,setup:setupPage,overview,tasks:tasksPage,activity:activityPage,connections:connectionsPage,projects:projectsPage,context:contextPage,policies:policiesPage,skills:skillsPage,settings:settingsPage};
function closeMenu() {
  document.body.classList.remove("menu-open"); $("#scrim").hidden=true;
  $(".menu-toggle")?.setAttribute("aria-expanded","false");
}
function render() {
  const [key,params] = location.hash.slice(1).split("?"), view = Object.hasOwn(pages,key)?key:"inbox";
  const scoped = view==="activity" ? new URLSearchParams(params).get("connection") || "" : "";
  if (activityConnection!==scoped) {
    activityConnection=scoped; activityProject=""; activityQuery=""; activeView="";
  }
  const switched = activeView!==view;
  shell(view);
  if (!loaded) return;
  if (activeView===view && $("#main").contains(document.activeElement) && ["INPUT","SELECT"].includes(document.activeElement.tagName)) {
    if (view==="tasks") { setIfChanged($("#task-list"),taskList()); return; }
    if (view==="activity") { setIfChanged($("#activity-list"),activityList()); return; }
    if (view==="inbox") { renderInbox(); return; }
    if (view==="overview") { renderOverview(); return; }
    if (view==="setup") { const p=$("#vps-progress"); if (p && !p.contains(document.activeElement)) setIfChanged(p,vpsProgress()); return; }
  }
  if (activeView===view && view==="inbox") { renderInbox(); return; }
  if (activeView===view && view==="overview" && $("#ov-top")) { renderOverview(); return; }
  activeView=view; setIfChanged($("#main"),pages[view]());
  if (view==="inbox") renderInbox();
  if (view==="overview") renderOverview();
  if (view==="settings") { void loadProviders(); void loadAgents(); }
  if (view==="setup" || view==="overview") void loadSetup();
  document.title=viewNames[view]+" · MeMyselfAI";
  if (switched) { closeMenu(); $("#main").scrollTop=0; window.scrollTo(0,0); }
}
function notify(message) {
  clearTimeout(noticeTimer); $("#notice").textContent=message; $("#notice").classList.add("visible");
  noticeTimer=setTimeout(()=>$("#notice").classList.remove("visible"),3600);
}
async function request(path, payload) {
  const headers = payload ? {"Content-Type":"application/json","X-App-Token":$('meta[name="app-token"]').content} : {};
  const response = await fetch(path,{method:payload?"POST":"GET",headers,body:payload?JSON.stringify(payload):undefined,cache:"no-store"});
  let data;
  try { data=await response.json(); } catch { throw new Error("The local service returned an unreadable response."); }
  if (!response.ok || data.error || (payload && !data.ok)) {
    const error=new Error(data.error || "The local service could not complete this request.");
    error.status=response.status;
    throw error;
  }
  return data;
}
let pendingRefresh=false;
async function refresh() {
  if (refreshing) { pendingRefresh=true; return; }
  refreshing=true;
  try {
    const result=await request("/api/state");
    if (!Array.isArray(result.tasks)) throw new Error("The local service returned an incomplete workspace.");
    Object.assign(state,result); loaded=true; $("#network").hidden=true; render();
    if (vpsActive() || !setupOpened) void loadSetup();
    if (dialogState?.type==="detail" || dialogState?.type==="review_proposal") {
      const fresh=state.tasks.find(t=>String(t.id)===String(dialogState.task.id));
      if (!fresh || JSON.stringify(fresh)!==dialogState.signature) {
        const stale=$("#stale");
        if (stale) { stale.hidden=false; $("#dialog [data-action=approve]")?.setAttribute("disabled",""); }
      }
    }
  } catch(e) {
    $("#network").textContent=e.message+" Reconnecting…"; $("#network").hidden=false;
  } finally {
    refreshing=false;
    if (pendingRefresh) { pendingRefresh=false; void refresh(); }
  }
}
async function getSchemas() {
  try { schemas=await request("/api/connectors"); render(); }
  catch { schemas={}; render(); }
}
const input = (name,label,options = {}) => {
  const {type="text",value="",required=false,placeholder="",help="",readonly=false}=options;
  const attrs='name="'+esc(name)+'" '+(required?"required ":"")+(readonly?"readonly ":"")+'placeholder="'+esc(placeholder)+'"';
  const field=type==="textarea"?'<textarea '+attrs+' rows="5">'+esc(value)+'</textarea>':'<input '+attrs+' type="'+esc(type)+'" autocomplete="'+(type==="password"?"new-password":"off")+'" value="'+esc(value)+'">';
  return '<label class="field"><span>'+esc(label)+'</span>'+field+(help?'<small>'+esc(help)+'</small>':'')+'</label>';
};
function selectField(name,label,choices,current) {
  return '<label class="field"><span>'+esc(label)+'</span><select name="'+esc(name)+'">'+selectOptions(choices,current)+'</select></label>';
}
function repositoryRow(path="",activity=false) {
  return '<div class="repository-picker"><input aria-label="'+(activity?"Watched folder":"Local repository")+'" name="repo" value="'+esc(path)+'" placeholder="No folder selected">'+
    button("Choose folder…","choose-repository",null,"secondary")+button("Remove","remove-repository",null,"quiet")+'</div>';
}
function repositoryFields(paths=[],activity=false) {
  return '<div class="field"><span>'+(activity?"Watched folders":"Local repositories")+'</span><div id="repository-rows">'+
    (paths.length?paths:[""]).map(p=>repositoryRow(p,activity)).join("")+'</div><div>'+
    button(activity?"Add folder":"Add repository","add-repository",null,"secondary")+'</div></div>';
}
async function pickFolder(path=null) {
  const picker=window.webkit?.messageHandlers?.chooseRepository;
  return picker?await picker.postMessage(path):(await request("/api/action",{op:"choose_repository"})).path;
}
async function answerWithFolder(id,version) {
  try {
    const path=await pickFolder();
    if (path) await mutate({op:"answer",id,version,text:path,picked_folder:true},"Checkout selected.");
  } catch(e) {
    showError(e.message || "Could not open the folder picker.");
  }
}
function folderAccess(task) {
  const scope=state.projects.find(p=>p.name===task?.project);
  const projects=scope?state.projects.filter(p=>includesProject(scope,p)):state.projects;
  const paths=[...new Set(projects.flatMap(p=>p.repos||[]))];
  const folders=paths.map(path=>{
    const attrs='data-path="'+esc(path)+'"';
    return entry("repository",path,"",button("Choose folder…","allow-folder",null,"secondary",attrs)+
      button("Check access","check-folder","sync","quiet",attrs));
  }).join("");
  showDialog("folder-access","Folder access",
    (folders||empty("Register a checkout under Projects to check its folder access."))+
    '<p class="form-intro">If you missed or denied the macOS prompt, open Files &amp; Folders and enable the folder for MeMyselfAI. Return here to check access.</p>'+
    button("Open Files & Folders settings","open-folder-settings","settings")+
    '<p role="status" id="folder-check-status"></p>');
}
async function checkFolder(el) {
  const path=el.dataset.path, ctx=dialogState, status=$("#folder-check-status");
  if (!ctx || ctx.checking) return;
  const controls=[...$("#dialog").querySelectorAll('[data-action="allow-folder"],[data-action="check-folder"]')];
  ctx.checking=true;
  controls.forEach(b=>b.disabled=true);
  $("#form-error").hidden=true;
  status.textContent="";
  try {
    if (el.dataset.action==="allow-folder") {
      const selected=await pickFolder(path);
      if (!selected || dialogState!==ctx) return;
      if (selected!==path) throw new Error("Choose the registered folder shown here: "+path);
    }
    status.textContent="Checking Git access to "+path+"…";
    await request("/api/action",{op:"check_folder_access",path});
    status.textContent="Git can read "+path+". Waiting tasks will be rechecked.";
  } catch(e) {
    status.textContent="";
    if (dialogState===ctx) showError(e.message || "Could not check folder access.");
  } finally {
    ctx.checking=false;
    controls.forEach(b=>b.disabled=false);
  }
}
async function chooseRepository(button) {
  button.disabled=true;
  try {
    const path=await pickFolder();
    if (path && button.isConnected) {
      button.closest(".repository-picker").querySelector("input").value=path;
      rememberForm();
    }
  } catch {
    if (button.isConnected) showError("Could not open the folder picker. Try again or enter the folder path.");
  } finally {
    button.disabled=false;
  }
}
function projectField(value,locked=false) {
  if (!locked && state.projects.length) return selectField("project","Project",state.projects.map(p=>[p.name,p.name]),value);
  return '<label class="field"><span>Project</span><input name="project" list="known-projects" value="'+esc(value||"")+'" required '+(locked?"readonly":"")+' placeholder="Project name"></label><datalist id="known-projects">'+state.projects.map(p=>'<option value="'+esc(p.name)+'"></option>').join("")+'</datalist>';
}
const quote = obj => '<pre class="quote">'+esc(plain(obj))+'</pre>';
function sourceReference(raw,label) {
  if (!raw) return "";
  let url;
  try { url=new URL(raw); } catch { return label?"":esc(raw); }
  if (url.protocol!=="https:" || url.username || url.password) return label?"":esc(raw);
  return '<a class="'+(label?"text-link":"source-link")+'" href="'+esc(url.href)+'" target="_blank" rel="noopener noreferrer">'+esc(label||raw)+(label?icon("arrow",15):"")+'</a>';
}
function revisionDiff(before,after) {
  const old=plain(before).split("\n"), next=plain(after).split("\n");
  let start=0,tail=0;
  while(start<old.length && start<next.length && old[start]===next[start]) start++;
  while(tail<old.length-start && tail<next.length-start && old[old.length-1-tail]===next[next.length-1-tail]) tail++;
  const removed=old.slice(start,old.length-tail),added=next.slice(start,next.length-tail);
  return (removed.length?group("Removed",quote(removed.join("\n"))):"")+(added.length?group("Added",quote(added.join("\n"))):"") || "<p>Revision text is unchanged.</p>";
}
function facts(items) {
  return '<dl class="facts">'+items.filter(([,v])=>v!=null && v!=="").map(([k,v,source])=>'<div><dt>'+esc(k)+'</dt><dd>'+(source?sourceIdentity(source):esc(plain(v)))+'</dd></div>').join("")+'</dl>';
}
function group(label,html) { return '<section class="detail-group"><h3>'+sourceMark(label)+'</h3>'+html+'</section>'; }
function draftPreview(t) {
  if (t.operation==="submit PR review") return facts([["Review type","Comment"],["Commit",t.payload?.commit_id]])+quote(t.draft);
  if (t.operation==="calendar_event" && t.calendar_payload) {
    const event=t.calendar_payload;
    return facts([["Event",event.summary],["Starts",new Date(event.start.dateTime).toLocaleString()],["Ends",new Date(event.end.dateTime).toLocaleString()]])+
      '<details><summary>Event details</summary>'+quote(t.draft)+'</details>';
  }
  return quote(t.draft);
}
const present=v=>v!=null && v!=="" && (!Array.isArray(v)||v.length>0);
function proposalPreview(t) {
  const categories = [...new Set([t.kind,...(t.categories||[]),...(present(t.commitments)?["commitment"]:[])].filter(Boolean))];
  const applicable=categories.map(k=>[titleCase(k),modes[state.rules.find(r=>r.project===t.project&&r.kind===k)?.mode]||"Require review"]);
  return group("Proposed action",facts([["Operation",t.operation||t.kind||"External write"],["Sending account",t.account||"Unavailable"],["Destination",t.destination||"Unavailable"],["Subject",t.subject],["Version",t.version]])+draftPreview(t)+
    (present(t.commitments)?group("Commitments",quote(t.commitments)):"")+
    (applicable.length?'<details><summary>Decision rules</summary>'+facts(applicable)+'</details>':""));
}
function approvalButton(t) {
  const ready=present(t.draft)&&present(t.account)&&present(t.destination)&&t.version!=null;
  return button("Approve this version","approve","check","primary",'data-id="'+esc(t.id)+'" data-version="'+esc(t.version)+'" '+(ready?"":"disabled"));
}
function staleWarning(t,action) {
  return '<div id="stale" class="policy-note" hidden>This task changed. '+button("View latest",action,"sync","secondary",'data-id="'+esc(t.id)+'"')+'</div>';
}
function detailTask(t) {
  const id='data-id="'+esc(t.id)+'"';
  const activity=recent(state.activity.filter(a=>a.task!=null&&String(a.task)===String(t.id)),"at");
  const reason=t.surfaced_reason || activity.findLast(a=>a.kind==="Discovery")?.detail || t.reason;
  const context = (present(reason)?group("Why this surfaced",quote(reason)):"")+
    facts([...taskContext(t),["Local checkout",(t.origin?.repository || t.github_repository) && t.repository],["Opened",fullDate(t.created)],["Due",fullDate(t.due)],["Decision type",t.kind],["Worktree",t.worktree]])+
    (t.url?'<dl class="facts"><div><dt>Source address</dt><dd>'+sourceReference(t.url)+'</dd></div></dl>':"")+
    (t.parent && findTask(t.parent)?group("Linked task",button(findTask(t.parent).title,"detail","arrow","secondary",'data-id="'+esc(t.parent)+'"')):"")+
    (present(t.body)?group("Task context",'<div class="markdown-body">'+taskMarkdown.render(t.body)+'</div>'):"")+
    (t.evidence?.length?group("Supporting activity",evidenceList(t.evidence)):"");
  const results = (present(t.result)?group("Result & evidence",quote(t.result)):"")+
    (t.findings?.length?group("Test findings",t.findings.map(id=>button(findTask(id)?.title||id,"detail","tasks","secondary",'data-id="'+esc(id)+'"')).join("")):"")+
    (t.special==="app_finding" && t.milestones?.issue?group("GitHub",sourceReference("https://github.com/"+t.milestones.repository+"/issues/"+t.milestones.issue,"Issue #"+t.milestones.issue)+" "+sourceReference(t.milestones.url,"Pull request #"+t.milestones.pr)+" "+sourceReference(t.receipts.find(r=>r.operation==="submit PR review")?.receipt,"Posted review")):"")+
    (t.milestones && Object.keys(t.milestones).length?group("Verified milestones",facts(Object.entries(t.milestones))):"");
  const history = (present(t.feedback)?group("Feedback & answers",t.feedback.map(f=>quote(f.text||[f.question,f.answer].filter(Boolean).join("\n"))).join("")):"")+
    (present(t.receipts)?group("Delivery receipts",t.receipts.map(r=>facts([["Operation",r.operation],["Recorded",fullDate(r.at)],["Receipt",r.receipt]])+quote(r.text)).join("")):"");
  const recovery = (Array.isArray(t.recovered)&&t.recovered.length)?group("Recovered tasks",t.recovered.map(id=>{
    const found=findTask(id); return found?button(found.title,"detail","tasks","secondary",'data-id="'+esc(found.id)+'"'):'<p>'+esc(id)+'</p>';
  }).join("")):"";
  const actionPreview = t.status==="review" || present(t.draft) ? proposalPreview(t) : "";
  const actions = taskActionNames(t).map(action=>{
    if (action==="review_proposal") return approvalButton(t);
    const [label,glyph]=taskShortcuts[action];
    return button(label,action,glyph,["answer","folder-access"].includes(action)?"primary":"secondary",id);
  }).join("")+runOn(t);
  const tabs = [
    ["actions","Actions",'<div class="detail-actions">'+status(t.status)+actions+(taskExclusions(t)||button("Manage ignore list","ignorelist","ignore","quiet"))+'</div>'+
      (present(t.question)?group("Agent question",'<div class="markdown-body">'+taskMarkdown.render(t.question)+'</div>'+
        (t.question_context?'<div class="markdown-body">'+taskMarkdown.render(t.question_context)+'</div>':"")+answerChoices(t,true)):"")+actionPreview+results+recovery],
    ["context","Why it surfaced & context",context],
    ["activity","Task activity",(activity.length?timeline(activity):"")+history || empty("No activity recorded yet.")]
  ];
  return staleWarning(t,"detail")+
    '<div class="tabs task-tabs" role="tablist" aria-label="Task details">'+tabs.map(([key,label],i)=>
      '<button type="button" role="tab" id="tab-'+key+'" data-action="task-tab" aria-controls="panel-'+key+'" aria-selected="'+(i===0)+'" tabindex="'+(i===0?0:-1)+'">'+esc(label)+'</button>').join("")+'</div>'+
    tabs.map(([key,,html],i)=>'<section class="task-panel" role="tabpanel" id="panel-'+key+'" aria-labelledby="tab-'+key+'" tabindex="0"'+(i?" hidden":"")+'>'+html+'</section>').join("");
}
function selectTaskTab(tab) {
  for (const item of tab.parentElement.querySelectorAll('[role="tab"]')) {
    const selected=item===tab;
    item.setAttribute("aria-selected",String(selected));
    item.tabIndex=selected?0:-1;
    document.getElementById(item.getAttribute("aria-controls")).hidden=!selected;
  }
  tab.focus();
}
function showDialog(type,title,body,context={},footer="") {
  const d=$("#dialog");
  if (!d.open) invoker=document.activeElement;
  dialogState={type,...context}; d.className=type==="detail"?"wide":"";
  $("#dialog-body").innerHTML='<div class="dialog-head"><h2 id="dialog-title">'+esc(title)+'</h2><button type="button" class="iconbtn" aria-label="Close dialog" data-action="close">'+icon("close",17)+'</button></div><div class="dialog-content">'+body+'<div class="form-error" role="alert" id="form-error" hidden></div></div>'+footer;
  if (!d.open) d.showModal();
  const focusTarget = d.querySelector('[role="tab"][aria-selected="true"], textarea, input:not([readonly]), select') || d.querySelector("[data-action=close]");
  if (focusTarget) focusTarget.focus({preventScroll:true});
  d.scrollTop=0;
}
const formDrafts=new Map();
function draftKey(type,ctx) { return JSON.stringify([type,ctx.id||"",ctx.kind||"",ctx.project||"",type==="answer"?ctx.version:""]); }
function openForm(type,title,fields,submit,ctx={},description="") {
  const body=(description?'<p class="form-intro">'+esc(description)+'</p>':"")+'<form id="editor">'+fields+'</form>';
  showDialog(type,title,body,ctx,'<div class="dialog-footer">'+button("Close","close",null,"quiet")+'<button type="submit" class="btn primary" form="editor">'+esc(submit)+'</button></div>');
  const stored=formDrafts.get(draftKey(type,ctx));
  if (stored) for (const [key,val] of Object.entries(stored)) {
    if (key==="ignored_project") {for (const checkbox of $("#editor").querySelectorAll('[name="ignored_project"]')) checkbox.checked=val.includes(checkbox.value);continue;}
    if (key==="repos" && ["project","suggestion_config"].includes(type)) {$("#repository-rows").innerHTML=val.map(p=>repositoryRow(p,type==="suggestion_config")).join("");continue;}
    const field=$("#editor").elements.namedItem(key);
    if (field && !field.readOnly) field.value=val;
  }
}
function rememberForm() {
  const form=$("#editor"); if (!form||!dialogState) return;
  const draft={};
  for (const el of form.elements) if (el.name && el.type!=="password") draft[el.name]=el.value;
  if (["project","suggestion_config"].includes(dialogState.type)) {delete draft.repo;draft.repos=new FormData(form).getAll("repo");}
  if (dialogState.type==="ignorelist") draft.ignored_project=new FormData(form).getAll("ignored_project");
  formDrafts.set(draftKey(dialogState.type,dialogState),draft);
}
function showError(message) {
  const el=$("#form-error");
  if ($("#dialog").open && el) { el.textContent=message; el.hidden=false; }
  else notify(message);
}
const signInNames = {github:"GitHub",slack:"Slack",email:"Google"};
const authRequest = (op,fields) => request("/api/action",{op,...fields});
const mailPresets = {Yahoo:["imap.mail.yahoo.com","smtp.mail.yahoo.com","465"],iCloud:["imap.mail.me.com","smtp.mail.me.com","587"]};

function manualConnection(kind,connection) {
  const schema=schemas[kind];
  const preset=kind==="email" && !connection
    ? selectField("mailPreset","Mail provider",[["","Custom settings"],...Object.keys(mailPresets).map(k=>[k,k])],"") : "";
  const config=schema.fields.map(f=>input("cfg."+f.key,f.label,{required:!!f.required,readonly:!!connection,value:connection?.config?.[f.key]??f.default,placeholder:f.placeholder})).join("");
  const fields=input("label","Account label",{value:connection?.label,required:true,placeholder:"A name you will recognize"})+
    preset+config+input("secret",schema.secret_label||"Access token",{type:"password",required:!connection,help:connection?"Leave empty to keep the saved credential.":"Stored in your macOS Keychain."});
  openForm("connect",(connection?"Configure ":"Connect ")+schema.label,fields,connection?"Save connection":"Connect account",{id:connection?.id,kind},schema.help);
}

const authOpen = ctx => $("#dialog").open && dialogState===ctx;
function cancelSignIn(ctx) {
  if (ctx?.type!=="signin") return;
  clearTimeout(ctx.timer);
  if (ctx.flow) {
    void authRequest("auth_cancel",{flow:ctx.flow}).catch(()=>{});
    ctx.flow=null;
  }
}
function authButton(label,disabled=false) {
  const submit=$("#auth-submit");
  submit.textContent=label;
  submit.disabled=disabled;
}
function signInFailed(ctx,message) {
  ctx.stage="retry";
  $("#auth-step").replaceChildren();
  authButton("Try again");
  showError(message);
}
function credentialFields(ctx) {
  ctx.stage="slack_token";
  $("#auth-step").innerHTML=input("secret","Slack user access token",{type:"password",placeholder:"xoxp-… or xoxe.xoxp-…"})+
    input("refresh_token","Slack refresh token",{type:"password",placeholder:"xoxe-…",help:"For automatic renewal. You can also connect with this token alone."})+
    '<div id="slack-issuer" hidden>'+input("client_id","Issuing Slack app’s client ID",{placeholder:"123456789.987654321",help:ctx.registration.configured?"Leave blank to use the configured Slack app.":"In the issuing Slack app, open Basic Information → App Credentials."})+
    input("client_secret","Client secret",{type:"password",help:"Required for server-issued tokens. Leave blank for desktop PKCE tokens."})+'</div>'+
    (ctx.registration.configured?button("Use Slack browser sign-in","browser-signin",null,"quiet"):"");
  authButton("Connect workspace");
}
async function openSignIn(kind,connection) {
  const name=signInNames[kind];
  const desc={email:"Choose your Google account in your browser and grant access to the services you want to use.",slack:"Connect your Slack workspace.",github:"Connect your GitHub CLI account to discover issues and pull requests across your accessible repositories."};
  const manual=connection?"Edit connection details":"Enter a token manually";
  showDialog("signin",(connection?"Reconnect ":"Connect ")+(kind==="email"?"Google":schemas[kind].label),
    '<form id="auth-form"><p class="auth-intro">'+esc(desc[kind])+'</p><div id="auth-step" aria-live="polite"></div></form>',
    {kind,connection,stage:"loading"},'<div class="dialog-footer auth-footer">'+(connection?.auth_method==="gh_account" || kind==="slack" || kind==="email"?"":button(manual,"manual",null,"quiet"))+'<button form="auth-form" type="submit" class="btn primary" id="auth-submit" disabled>Checking setup…</button></div>');
  const ctx=dialogState;
  try {
    const info=kind==="slack"?await authRequest("auth_info",{kind}):{};
    if (!authOpen(ctx)) return;
    ctx.stage="start";
    ctx.registration=info;
    if (kind==="slack") {
      credentialFields(ctx);
      return;
    }
    if (kind==="email") {
      authButton("Continue with Google");
      await submitSignIn($("#auth-form"));
      return;
    }
    authButton("Continue with "+name);
  } catch(e) {
    if (authOpen(ctx)) signInFailed(ctx,e.message);
  }
}
async function checkSignIn(ctx) {
  if (!authOpen(ctx)) return;
  try {
    const result=await authRequest("auth_status",{flow:ctx.flow});
    if (!authOpen(ctx)) return;
    $("#form-error").hidden=true;
    if (result.phase==="error") {
      signInFailed(ctx,result.message||"Sign-in failed.");return;
    }
    if (result.phase==="ready") {
      if (ctx.kind==="slack") {
        await mutate({op:"auth_finish",flow:ctx.flow},"Slack workspace connected.");
      } else if (ctx.kind==="github") {
        await mutate({op:"auth_finish",flow:ctx.flow},"Connected GitHub as @"+result.account+".");
      } else {
        await mutate({op:"auth_finish",flow:ctx.flow,value:result.account},"Connected "+result.account+".");
      }
      return;
    }
    const instructions=ctx.kind==="slack" && result.url
      ?"In your browser, check the workspace and select Allow. If Slack requires admin approval, request it from your workspace admin, then retry. Return here after approval."
      :result.message||"Waiting for sign-in. Return here after authorizing access.";
    setIfChanged($("#auth-step"),'<p>'+esc(instructions)+'</p>'+
      (result.code?'<p>Device code <strong class="device-code">'+esc(result.code)+'</strong></p>':"")+
      sourceReference(result.url,"Open "+signInNames[ctx.kind]+" in browser"));
  } catch(e) {
    if (!authOpen(ctx)) return;
    if (e.status>=400 && e.status<500 && e.status!==429) {signInFailed(ctx,e.message);return;}
    showError(e.message+" Checking again…");
  }
  ctx.timer=setTimeout(()=>void checkSignIn(ctx),1500);
}
async function submitSignIn(form) {
  const ctx=dialogState;
  if ($("#auth-submit").disabled) return;
  const fields=Object.fromEntries(new FormData(form));
  if (ctx.stage==="retry") {
    cancelSignIn(ctx);void openSignIn(ctx.kind,ctx.connection);return;
  }
  const previous=$("#auth-submit").textContent;
  authButton("Starting sign-in…",true);
  const controls=[...form.querySelectorAll("input,textarea,select,button")];
  controls.forEach(el=>el.disabled=true);
  $("#form-error").hidden=true;
  try {
    const credentials=ctx.stage==="slack_token"?{method:ctx.stage,secret:fields.secret,
      ...(fields.refresh_token?.trim()?{refresh_token:fields.refresh_token,client_id:fields.client_id?.trim(),client_secret:fields.client_secret}:{} )}:{};
    const result=await authRequest("auth_start",{kind:ctx.kind,id:ctx.connection?.id,...credentials});
    if (!authOpen(ctx)) {void authRequest("auth_cancel",{flow:result.flow}).catch(()=>{});return;}
    ctx.flow=result.flow;ctx.stage="waiting";$("#auth-step").replaceChildren();
    authButton("Waiting for sign-in…",true);
    await checkSignIn(ctx);
  } catch(e) {
    if (authOpen(ctx)) {
      controls.forEach(el=>el.disabled=false);
      authButton(ctx.stage==="start"?"Continue with "+signInNames[ctx.kind]:previous);
      showError(e.message);
    }
  }
}
const findTask=id=>state.tasks.find(t=>String(t.id)===String(id));
function handleAction(el) {
  const {action,id,kind,project}=el.dataset;
  const t=id?findTask(id):null;
  const selectedProject=projectFilter||state.projects[0]?.name||"";
  switch(action) {
    case "menu": document.body.classList.add("menu-open"); $("#scrim").hidden=false; el.setAttribute("aria-expanded","true"); $("#sidebar nav a")?.focus(); return;
    case "close": $("#dialog").close(); return;
    case "task-tab": selectTaskTab(el); return;
    case "inbox-select": selectInboxTask(el.dataset.id); return;
    case "inbox-back": inboxNarrowOpen=false; renderInbox(); document.querySelector('.inbox-row[data-row="'+CSS.escape(String(selectedTask))+'"] .task-open')?.focus(); return;
    case "inbox-filter": inboxFilter=el.dataset.filter; selectedTask=null; inboxNarrowOpen=false; renderInbox(); return;
    case "inbox-group": collapsedGroups.has(el.dataset.key)?collapsedGroups.delete(el.dataset.key):collapsedGroups.add(el.dataset.key); renderInbox(); return;
    case "inbox-retry-all": void retryGroup(el.dataset.key); return;
    case "inbox-tab": detailTab=el.dataset.tab; renderInbox(); document.querySelector('.detail-tabs [data-tab="'+detailTab+'"]')?.focus(); return;
    case "callout-toggle": collapsedCallouts.has(el.dataset.id)?collapsedCallouts.delete(el.dataset.id):collapsedCallouts.add(el.dataset.id); renderInbox(); return;
    case "inbox-instruct": {
      if (activeView!=="inbox" || String(selectedTask)!==String(id)) { if (activeView!=="inbox") location.hash="inbox"; selectInboxTask(id); }
      requestAnimationFrame(()=>$("#instruct-text")?.focus()); return;
    }
    case "add-repository": $("#repository-rows").insertAdjacentHTML("beforeend",repositoryRow("",dialogState.type==="suggestion_config"));$("#repository-rows").lastElementChild.querySelector("button").focus();rememberForm();return;
    case "remove-repository": el.closest(".repository-picker").remove();rememberForm();return;
    case "chat-settings": {
      const kind=el.dataset.kind, name=kind==="claude"?"Claude":"ChatGPT", saved=state.suggestions.config.chats?.[kind];
      const folder='<label class="field"><span>Extracted chat export folder (optional)</span><span class="repository-picker">'+
        '<input name="folder" readonly value="'+esc(saved?.folder||"")+'" placeholder="No export folder selected">'+button("Choose folder…","choose-repository",null,"secondary")+
        button("Clear","clear-chat-folder",null,"quiet")+'</span></label>';
      openForm("chat_source_config",name+" chat activity",folder,saved?"Save":"Connect "+name,{kind},
        "Every five minutes, your selected AI CLI receives recent requests and replies from "+(kind==="claude"?"Claude Desktop Code sessions":"ChatGPT desktop work sessions")+
        " to suggest unfinished tasks. For standard chats, select an extracted "+name+" export. Only the last three hours are considered.");
      return;
    }
    case "clear-chat-folder": el.closest(".repository-picker").querySelector("input").value="";return;
    case "chat-disconnect": void mutate({op:"chat_source_config",provider:el.dataset.kind,enabled:false},"Chat sync stopped.");return;
    case "suggestion-config": {
      const c=state.suggestions.config;
      openForm("suggestion_config","Local activity",repositoryFields(c.folders,true)+selectField("foreground","Frontmost app",[[false,"Do not sample"],[true,"Sample app name"]],c.foreground),"Save sources",{},
        "Every five minutes, the selected AI CLI can use filenames, modification times, your Git commit subjects and optional app names alongside connected activity.");
      return;
    }
    case "suggestion_scan": void mutate({op:action},"Activity check queued.");return;
    case "suggestion_start": void mutate({op:action,id},"Task queued.");return;
    case "suggestion_dismiss": void mutate({op:action,id},"Suggestion dismissed.");return;
    case "choose-repository": void chooseRepository(el); return;
    case "folder-access": folderAccess(t); return;
    case "allow-folder":
    case "check-folder": void checkFolder(el); return;
    case "open-folder-settings":
      void request("/api/action",{op:"open_folder_settings"}).catch(e=>showError(e.message)); return;
    case "filter": filter=el.dataset.filter; if (activeView!=="tasks") {projectFilter="";query="";location.hash="tasks";} else render(); return;
    case "reload": void getSchemas(); void refresh(); return;
    case "project-details": {
      const name=el.dataset.value, p=state.projects.find(item=>item.name===name);
      const body=facts([["Scope",p?.global?"Workspace-wide":""],["Parent project",p?.parent],["Registered checkouts",p?.repos?.join("\n")],["Tasks",state.tasks.filter(task=>task.project===name).length]]);
      showDialog(action,name,body,{},p?'<div class="dialog-footer">'+projectControls(p)+'</div>':"");
      return;
    }
    case "repository-details": {
      const repo=el.dataset.value;
      const github=/^[\w.-]+\/[\w.-]+$/.test(repo) && !repo.split("/").some(part=>part==="." || part==="..");
      const checkout=repo.startsWith("/")?repo:t?.repository;
      const body=facts([["Project",t?.project],["Local checkout",checkout]])+
        (github?sourceReference("https://github.com/"+repo,"Open on GitHub"):"");
      showDialog(action,repo,body,{},'<div class="dialog-footer">'+exclusionButton("repository",repo)+'</div>');
      return;
    }
    case "set_exclusion": {
      const {scope,value}=el.dataset, ignored=el.dataset.ignored==="true";
      const label=(ignored?"Ignore ":"Unignore ")+scope;
      const impact=ignored?(scope==="project"?"Work pauses for this project, its subprojects and their registered repositories.":"Work pauses for this repository across projects and connected accounts.")+" New matching work is excluded.":"Eligible tasks can resume. Other exclusions still apply.";
      openForm(action,label,facts([[titleCase(scope),value]]),label,{scope,value,ignored},impact);
      return;
    }
    case "ignorelist": {
      const list=state.ignorelist||{projects:[],repositories:[]};
      const projects=state.projects.filter(p=>!p.global).map(p=>p.name);
      const choices=projects.map(name=>'<label><input type="checkbox" name="ignored_project" value="'+esc(name)+'" '+(list.projects.includes(name)?"checked":"")+'> '+esc(name)+'</label>').join("");
      openForm(action,"Ignore list",(choices?'<fieldset class="ignore-projects"><legend>Projects</legend>'+choices+'</fieldset>':"")+
        input("repositories","Repositories",{type:"textarea",value:list.repositories.join("\n"),placeholder:"owner/repository",help:"One GitHub owner/repo or absolute checkout path per line."}),
        "Save ignore list",{},"Work pauses for these repositories, projects and their subprojects. Removing an entry lets work resume.");
      return;
    }
    case "self_check": void mutate({op:action},"App checks queued."); return;
    case "connect_app_repo": void mutate({op:action},"App repository connected."); return;
    case "app_finding": openForm(action,"Report an app finding",input("title","Issue title",{required:true})+input("body","Observed behavior and steps to reproduce",{type:"textarea",required:true}),"Track finding",{},"This finding will enter Tasks for the MeMyselfAI repository."); return;
    case "task": openForm("task","New task",input("title","Task title",{required:true,placeholder:"What needs doing?"})+projectField(selectedProject)+input("body","Instructions",{type:"textarea",placeholder:"Add context and describe the outcome you need."}),"Create task"); return;
    case "project": {
      const p=state.projects.find(x=>x.name===project);
      openForm("project",p?"Edit project":"Add project",input("name","Project name",{value:p?.name,required:true,readonly:!!p})+parentField(p)+repositoryFields(p?.repos),"Save project",{project});
      return;
    }
    case "report": openForm("report","Report missed work",input("text","What did the agent miss?",{type:"textarea",required:true,placeholder:"Describe the request and where you saw it."}),"Start investigation",{},"The agent will review connected activity in the three hours leading up to your report."); return;
    case "style": openForm("style","Suggest a writing preference",input("text","Proposed guidance",{type:"textarea",required:true,placeholder:"Describe the style and where it should apply."}),"Submit for review"); return;
    case "rule": {
      const rule=state.rules.find(r=>r.kind===kind && r.project===project);
      openForm("rule",rule?"Edit decision rule":"Add decision rule",projectField(project||selectedProject,!!rule)+input("kind","Decision type",{value:kind,required:true,readonly:!!rule,placeholder:"e.g. reply, status or commitment"})+selectField("mode","Authority",Object.entries(modes),rule?.mode||"review"),"Save rule",{kind,project});
      return;
    }
    case "providers": void loadProviders(true); return;
    case "agents": void loadAgents(true); return;
    case "orchestrator-ask": openForm("orchestrator_prompt","Ask the orchestrator",input("text","Message",{type:"textarea",required:true,placeholder:"What's on my plate this week?"}),"Send",{},"The orchestrator reads your connected accounts through MeMyselfAI and can start tasks. Anything it prepares to send still needs your approval in dry mode."); return;
    case "report-period": reportPeriod=el.dataset.period; localStorage.setItem("report-period",reportPeriod); renderOverview(); return;
    case "chat-report": void sendChat(reportPrompt()); return;
    case "chat-quick": void sendChat(el.dataset.text); return;
    case "chat-focus": $("#chat-text")?.focus(); return;
    case "chat-open-task": openTaskFromChat(id); return;
    case "orchestrator-run": void mutate({op:"orchestrator_settings",run_now:true},"Sweep requested."); return;
    case "orchestrator-toggle": void mutate({op:"orchestrator_settings",enabled:!state.orchestrator.enabled},state.orchestrator.enabled?"Orchestrator paused.":"Orchestrator resumed."); return;
    case "agent-sign-in": void signInAgent(el.dataset.agent); return;
    case "hermes-install": void hermesAction("hermes_install"); return;
    case "hermes-dashboard": void hermesAction("hermes_dashboard"); return;
    case "setup-jump": document.getElementById("step-"+el.dataset.step)?.scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"start"}); return;
    case "setup-refresh": setupChecked=0; void loadSetup(true); return;
    case "setup-github": void setupMutate({op:"setup_connect_github"},r=>"Connected GitHub as @"+r.account+"."); return;
    case "setup-skip": void setupMutate({op:"setup_skip",step:el.dataset.step}); return;
    case "setup-dismiss": void setupMutate({op:"setup_dismiss"},"Setup hidden. It's still under Setup in the sidebar."); return;
    case "vps-cancel": void setupMutate({op:"vps_cancel"},"Server setup cancelled."); return;
    case "vps-sign-in": void vpsSignIn(); return;
    case "instance-code": void pairingCode(); return;
    case "instance-checkout": openForm("instance_checkout","Add a checkout on this instance",input("path","Path of a Git checkout",{required:true,placeholder:"/home/me/src/project"}),"Add",{},"Coding tasks for this repository's origin can then move to this instance with their worktree."); return;
    case "run-on": void mutate({op:"move",items:["task:"+el.dataset.id],to:el.dataset.peer},"Move requested. It happens once the task is idle and the instances sync."); return;
    case "instance-join": openForm("instance_join","Join another instance",input("address","Address of the other instance",{required:true,placeholder:"vps.example.com:8802"})+input("code","Pairing code",{required:true,placeholder:"XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"}),"Pair",{},"Pair only machines you control. Paired instances share your logins."); return;
    case "instance-rename": openForm("instance_rename","Rename this instance",input("name","Name",{required:true,value:state.instances.name}),"Save"); return;
    case "instance-remove": openForm("instance_remove","Remove instance",'<p>'+esc(state.instances.peers.find(p=>p.id===el.dataset.peer)?.name||"This instance")+' stops syncing and the workspace key is rotated. It keeps the logins it already has. Reconnect each account afterwards to revoke them.</p>',"Remove",{peer:el.dataset.peer}); return;
    case "settings": void loadProviders(); openForm("settings","Agent settings",selectField("dry","Delivery mode",[["true","Dry mode — review every external write"],["false","Live mode — follow project rules"]],String(!!state.settings.dry))+providerField()+selectField("orchestrator_subscription","Orchestrator subscription",Object.entries(subscriptionNames),state.settings.orchestrator_subscription||(state.settings.provider==="codex"?"codex":"claude"))+(state.instances?.peers.length?selectField("preference","Tasks on this instance",Object.entries(preferenceNames),state.settings.preference||"first")+input("slots","Agent slots on this instance",{type:"number",value:String(state.settings.slots||""),placeholder:"Default",help:"How many tasks run here at once. Leave empty for the default."}):""),"Save settings",{},"In live mode the agent may deliver work without asking when all applicable rules allow it."); return;
    case "connect": {
      const c=state.connections.find(k=>String(k.id)===id), provider=c?.kind||kind, schema=schemas[provider];
      if (!schema) { showError("Connector setup is unavailable. Reload connection details and try again."); return; }
      if ((c?.kind==="slack" && !c.auth_method) || (c?.kind==="github" && !["gh_account","github_cli"].includes(c.auth_method)) || !signInNames[provider] || (c?.kind==="email" && c.config?.imap_host?.toLowerCase()!=="imap.gmail.com")) manualConnection(provider,c);
      else void openSignIn(provider,c);
      return;
    }
    case "github-work": {const c=state.connections.find(c=>c.id===id);if(c) void browseGitHub(c);return;}
    case "github-page": void browseGitHub(dialogState.connection,dialogState.view,Number(el.dataset.page));return;
    case "github-review": void mutate({op:"github_review_item",id:dialogState.connection.id,repo:el.dataset.repo,number:Number(el.dataset.number)},"Submitted for discovery.");return;
    case "manual": {
      const ctx=dialogState;
      cancelSignIn(ctx);manualConnection(ctx.kind,ctx.connection);
      return;
    }
    case "browser-signin": {
      const ctx=dialogState;
      ctx.stage="start";$("#auth-step").replaceChildren();
      authButton("Continue with "+signInNames[ctx.kind]);
      void submitSignIn($("#auth-form"));return;
    }
    case "restart-signin": {
      const ctx=dialogState;
      cancelSignIn(ctx);void openSignIn(ctx.kind,ctx.connection);return;
    }
    case "review_proposal":
      if (!t) return;
      if (t.status==="review") {
        showDialog(action,t.title,staleWarning(t,action)+proposalPreview(t),{task:t,signature:JSON.stringify(t)},
          '<div class="dialog-footer">'+button("Cancel","close",null,"quiet")+approvalButton(t)+'</div>');
        return;
      }
      // A refreshed proposal may already have moved to another state.
    case "detail": if (t) showDialog("detail",t.title,detailTask(t),{task:t,signature:JSON.stringify(t)}); return;
    case "pick-checkout":
      void answerWithFolder(id,Number(el.dataset.version)); return;
    case "answer":
      if (t?.repository_question) {
        const paths=t.repository_options || (t.answer_choices||[]).map(c=>c.value).filter(Boolean);
        if (paths.length) {
          openForm("answer","Choose repository",selectField("text","Local repository",[["","Choose a repository"],...paths.map(p=>[p,p])],""),"Submit answer",{id,version:t.version},t.question);
          return;
        }
        void answerWithFolder(id,t.version); return;
      }
    case "feedback": if (t) openForm(action,action==="answer"?"Answer agent question":"Give task feedback",input("text",action==="answer"?"Your answer":"Feedback",{type:"textarea",required:true}),action==="answer"?"Submit answer":"Submit feedback",{id,version:t.version},action==="answer"?(t.question||t.title):t.title); return;
    case "pick-answer":
      void mutate({op:"answer",id,choice:Number(el.dataset.choice),version:Number(el.dataset.version)},"Answer recorded."); return;
    case "reconcile": if (t) openForm(action,"Record delivery outcome",facts([["Operation",t.operation||"reply"],["Account",t.account],["Destination",t.destination],["Subject",t.subject],["Version",t.version]])+draftPreview(t)+selectField("outcome","Verified provider outcome",[["","Choose the verified outcome"],["yes","I verified it was delivered"],["no","I verified it was not delivered"]],"")+(["create issue","create pull request","submit PR review"].includes(t.operation)?input("number",t.operation==="submit PR review"?"GitHub review ID":t.operation==="create issue"?"Verified issue number":"Verified pull request number",{type:"number",help:"Required when recording a verified delivery."}):""),"Record outcome",{id,version:t.version},"Check the destination before recording the outcome of this version."); return;
    case "skill": {
      const s=state.skills.find(k=>String(k.id)===id); if (!s) return;
      const previous=recent(state.skills.filter(x=>x.kind===s.kind&&Number(x.at)<Number(s.at)),"at")[0];
      const body=facts([["Revision",s.id],["Saved",fullDate(s.at)],["Status",skillState(s)],["Reason",s.reason]])+group("Revision text",quote(s.body))+(previous?'<details><summary>Changes from previous revision</summary>'+revisionDiff(previous.body,s.body)+'</details>':"");
      const control=s.active&&(s.kind!=="workflow"||previous)?button("Revert revision","revert_skill",null,"secondary",'data-id="'+esc(s.id)+'"'):!s.active && s.kind==="style" && !["Declined","Reverted"].includes(skillState(s))?button("Decline","reject_skill",null,"secondary",'data-id="'+esc(s.id)+'"')+button("Approve style","accept_skill","check","primary",'data-id="'+esc(s.id)+'"'):"";
      showDialog("skill",titleCase(s.kind)+" revision",body,{id},control?'<div class="dialog-footer">'+control+'</div>':"");
      return;
    }
    case "close_task":
    case "disconnect":
    case "demo":
    case "revert_skill": {
      const copy={close_task:["Close task","Stop work on this task and keep its history. Linked GitHub issues and pull requests stay open.","Close task"],disconnect:["Disconnect account","The agent will stop reading from and delivering to this connection.","Disconnect"],demo:["Load sample workspace","Add sample tasks and activity to this local workspace.","Load samples"],revert_skill:["Revert this revision","The next agent will use the resulting active instructions.","Revert revision"]}[action];
      openForm(action,copy[0],"",copy[2],{id},action==="close_task" && t?t.title+" — "+copy[1]:copy[1]); return;
    }
    case "approve": void mutate({op:action,id,version:Number(el.dataset.version)},"Approval recorded."); return;
    case "reject_skill": void mutate({op:action,id},"Style suggestion declined."); return;
    case "accept_skill": void mutate({op:action,id},"Style guidance approved."); return;
    case "retry": void mutate({op:action,id},"Preparation queued."); return;
    case "schedule_cancel": void mutate({op:action,id},"Schedule cancelled."); return;
    case "context_retire": void mutate({op:action,id},"Context retired. Agents will no longer receive it."); return;
    case "context_restore": void mutate({op:action,id},"Context restored."); return;
    case "handoff": void mutate({op:"rule",project,kind,mode:"auto"},"Autonomous handling allowed. Dry mode still applies while it is on."); return;
    case "sync": void mutate({op:action},"Source sync requested."); return;
  }
}
const messages={instance_checkout:"Checkout added.",instance_join:"Paired. The instances now share work.",instance_rename:"Instance renamed.",instance_remove:"Instance removed and workspace key rotated.",orchestrator_prompt:"Message sent to the orchestrator.",close_task:"Task closed.",ignorelist:"Ignore list saved.",app_finding:"Finding tracked.",chat_source_config:"Chat source saved.",suggestion_config:"Activity sources saved.",task:"Task created.",project:"Project saved.",report:"Investigation requested.",style:"Style preference submitted for review.",rule:"Decision rule saved.",settings:"Agent settings saved.",connect:"Connection saved.",feedback:"Feedback recorded.",answer:"Answer submitted.",reconcile:"Delivery outcome recorded.",dismiss:"Task dismissed.",disconnect:"Account disconnected.",demo:"Sample workspace loaded.",revert_skill:"Revision reverted."};
async function signInAgent(agent) {
  try {
    const result=await request("/api/action",{op:"agent_sign_in",agent});
    notify(result.opened?"Sign-in opened in Terminal. Finish it there, then choose Check again.":"Run this in a terminal on this machine: "+result.command);
  } catch(e) { showError(e.message); }
}
async function mutate(payload,message) {
  if (running) return;
  running=true;
  const d=$("#dialog"), ctx=dialogState, opener=document.activeElement;
  if (d.open) {
    d.setAttribute("aria-busy","true");
    d.querySelectorAll("button:not([disabled])").forEach(b=>{b.disabled=true;b.dataset.busy="1";});
  } else if (opener?.tagName==="BUTTON") opener.disabled=true;
  try {
    await request("/api/action",payload);
    if (payload.op==="auth_finish") ctx.flow=null;
    if (d.open) { if (ctx) formDrafts.delete(draftKey(ctx.type,ctx)); d.close(); }
    await refresh(); notify(message);
  } catch(e) {
    showError(e.message);
    if (payload.op==="auth_finish" && authOpen(ctx)) $("#form-error").insertAdjacentHTML("beforeend"," "+button("Start sign-in again","restart-signin",null,"secondary"));
    await refresh();
  }
  finally {
    running=false; d.removeAttribute("aria-busy");
    d.querySelectorAll("[data-busy]").forEach(b=>{b.disabled=false;delete b.dataset.busy;});
    if ($("#stale") && !$("#stale").hidden) $("#dialog [data-action=approve]")?.setAttribute("disabled","");
    if (opener?.isConnected && !d.contains(opener)) opener.disabled=false;
  }
}
document.addEventListener("click",event=>{
  const el=event.target.closest("[data-action]");
  for (const menu of document.querySelectorAll("details.more-menu[open]")) if (!menu.contains(event.target) || el && menu.querySelector(".menu").contains(el)) menu.open=false;
  if (el&&!el.disabled) {
    if (event.detail && el.closest(".row-shortcuts,.scope-link")) el.focus({preventScroll:true});
    handleAction(el);
  }
  if (event.target.closest("#sidebar a")) closeMenu();
});
document.addEventListener("input",event=>{
  if (event.target.name==="refresh_token" && dialogState?.stage==="slack_token") {
    const rotating=Boolean(event.target.value.trim());
    $("#slack-issuer").hidden=!rotating;
    event.target.form.elements.client_id.required=rotating && !dialogState.registration.configured;
  }
  const el=event.target;
  if (el.id==="vps-host") hostDraft=el.value;
  if (el.id==="chat-text") chatDraft=el.value;
  if (el.id==="instruct-text") inboxDrafts.set(el.form.dataset.id,el.value);
  if (el.id==="task-search") {query=el.value;setIfChanged($("#task-list"),taskList());}
  if (el.id==="activity-search") {activityQuery=el.value;setIfChanged($("#activity-list"),activityList());}
  if (el.closest("#editor")) rememberForm();
});
document.addEventListener("change",event=>{
  if (event.target.name==="github-view" && dialogState?.type==="github-work") void browseGitHub(dialogState.connection,event.target.value);
  if (event.target.name==="mailPreset") {
    const [imap,smtp,port]=mailPresets[event.target.value]||["","","465"];
    const fields=event.target.form.elements;
    for (const [key,value] of Object.entries({imap_host:imap,imap_port:"993",smtp_host:smtp,smtp_port:port})) fields.namedItem("cfg."+key).value=value;
  }
  if (event.target.id==="auto-group") {autoGroup=event.target.checked;localStorage.setItem("inbox-autogroup",autoGroup?"on":"off");renderInbox();}
  if (event.target.id==="inbox-sort") {inboxSort=event.target.value;localStorage.setItem("inbox-sort",inboxSort);renderInbox();}
  if (event.target.id==="task-project") {projectFilter=event.target.value;setIfChanged($("#task-list"),taskList());}
  if (event.target.id==="activity-connection") location.hash = event.target.value?"activity?connection="+encodeURIComponent(event.target.value):"activity";
  if (event.target.id==="activity-project") {activityProject=event.target.value;setIfChanged($("#activity-list"),activityList());}
  if (event.target.closest("#editor")) rememberForm();
});
document.addEventListener("submit",event=>{
  if (event.target.id==="instruct-form") {event.preventDefault();void sendInstruction(event.target);return;}
  if (event.target.id==="chat-form") {event.preventDefault();void sendChat(event.target.elements.text.value);return;}
  if (event.target.id==="auth-form") {event.preventDefault();void submitSignIn(event.target);return;}
  if (event.target.id==="vps-form") {event.preventDefault();void vpsStart($("#vps-host").value.trim());return;}
  if (event.target.id==="vps-password-form") {
    event.preventDefault();
    const field=$("#vps-password"), password=field.value; field.value="";
    void setupMutate({op:"vps_password",password});
    return;
  }
  if (event.target.id!=="editor") return;
  event.preventDefault();
  const form=event.target, ctx=dialogState, fields=Object.fromEntries(new FormData(form)), op=ctx.type;
  let payload={op,...fields};
  if (ctx.id!=null) payload.id=ctx.id;
  if (op==="answer") payload.version=ctx.version;
  if (op==="set_exclusion") payload={op,scope:ctx.scope,value:ctx.value,ignored:ctx.ignored};
  if (op==="ignorelist") payload={op,projects:new FormData(form).getAll("ignored_project"),repositories:fields.repositories.split(/\r?\n/).map(v=>v.trim()).filter(Boolean)};
  if (op==="project") {delete payload.repo;payload.repos=new FormData(form).getAll("repo").map(p=>p.trim()).filter(Boolean);}
  if (op==="chat_source_config") payload={op,provider:dialogState.kind,enabled:true,folder:fields.folder||""};
  if (op==="suggestion_config") payload={op,foreground:fields.foreground==="true",folders:new FormData(form).getAll("repo").map(p=>p.trim()).filter(Boolean)};
  if (op==="settings") { payload.dry=fields.dry==="true"; if ("slots" in fields) payload.slots=Number(fields.slots||0); }
  if (ctx.peer) payload.peer=ctx.peer;
  if (ctx.items) payload.items=ctx.items;
  if (op==="connect") {
    payload={op,kind:ctx.kind,label:fields.label,secret:fields.secret,config:{}};
    if (ctx.id) payload.id=ctx.id;
    for (const [key,val] of Object.entries(fields)) if (key.startsWith("cfg.")) payload.config[key.slice(4)]=val;
  }
  if (op==="reconcile") {
    if (!fields.outcome) {showError("Choose the outcome verified at the provider.");return;}
    payload={op,id:ctx.id,version:ctx.version,delivered:fields.outcome==="yes"};
    if (payload.delivered && fields.number!==undefined) {
      if (!Number.isSafeInteger(Number(fields.number)) || Number(fields.number)<1) {showError("Enter the verified GitHub number.");return;}
      payload.number=Number(fields.number);
    }
  }
  void mutate(payload,op==="set_exclusion"?ctx.value+(ctx.ignored?" added to the ignore list.":" removed from the ignore list."):messages[op]);
});
$("#dialog").addEventListener("cancel",e=>{if(running)e.preventDefault();});
$("#dialog").addEventListener("close",()=>{
  if ($("#dialog").open) return;
  cancelSignIn(dialogState);dialogState=null;$("#dialog-body").replaceChildren();
  if (document.activeElement===document.body) {
    const sameTask=invoker?.dataset.id ? [...document.querySelectorAll(".task-entry button")].filter(b=>b.dataset.id===invoker.dataset.id) : [];
    const target=invoker?.isConnected?invoker:sameTask.find(b=>b.dataset.action===invoker.dataset.action) || sameTask[0] || $("#main");
    target.focus({preventScroll:true});
  }
  invoker=null;
});
$("#scrim").addEventListener("click",closeMenu);
const typing=el=>el.matches?.("input,textarea,select,[contenteditable]");
document.addEventListener("keydown",e=>{
  if ((e.target.id==="instruct-text" || e.target.id==="chat-text") && e.key==="Enter" && (e.metaKey||e.ctrlKey)) {e.preventDefault();e.target.form.requestSubmit();return;}
  if (e.target.matches?.("a.task-ref[role=button]") && (e.key==="Enter" || e.key===" ")) {e.preventDefault();e.target.click();return;}
  const plainKey=!e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.repeat && !e.isComposing;
  if (activeView==="inbox" && plainKey && !typing(e.target) && !$("#dialog").open && !running) {
    const key=e.key.toLowerCase();
    if (key==="j" || key==="k" || e.key==="ArrowDown" && !e.target.closest(".detail-tabs") || e.key==="ArrowUp" && !e.target.closest(".detail-tabs")) {
      const ids=[...document.querySelectorAll(".inbox-row")].map(r=>r.dataset.row);
      if (ids.length) {
        const at=ids.indexOf(String(selectedTask)), down=key==="j"||e.key==="ArrowDown";
        const next=at<0?0:Math.min(ids.length-1,Math.max(0,at+(down?1:-1)));
        e.preventDefault(); selectInboxTask(ids[next],{focusRow:true,open:false}); return;
      }
    }
    if (/^[1-9]$/.test(e.key) && selectedTask!=null) {
      const pick=document.querySelector('#inbox-detail [data-action="pick-answer"][data-choice="'+(Number(e.key)-1)+'"]');
      if (pick) {e.preventDefault(); pick.click(); return;}
    }
    if (e.key==="Enter" && e.target.closest(".inbox-row") && !e.target.matches("button,a")) {selectInboxTask(e.target.closest(".inbox-row").dataset.row);return;}
  }
  const row=e.target.closest(".task-entry") || (activeView==="inbox" && !typing(e.target) && !e.target.closest("#inbox-detail button,#inbox-detail a") ?
    document.querySelector('.inbox-row[data-row="'+CSS.escape(String(selectedTask))+'"]') : null);
  if (row && !$("#dialog").open && !running && !e.repeat && !e.isComposing &&
      !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && /^[a-z]$/i.test(e.key)) {
    const shortcut=row.querySelector('[aria-keyshortcuts="'+e.key.toUpperCase()+'"]');
    if (shortcut) {e.preventDefault(); shortcut.click(); return;}
  }
  if (e.target.matches('.detail-tabs [role="tab"]') && ["ArrowLeft","ArrowRight"].includes(e.key)) {
    const tabs=[...e.target.parentElement.children], index=tabs.indexOf(e.target);
    e.preventDefault(); tabs[(index+(e.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length].click(); return;
  }
  if (e.target.matches('.task-tabs [role="tab"]') && ["ArrowLeft","ArrowRight","Home","End"].includes(e.key)) {
    const tabs=[...e.target.parentElement.children], index=tabs.indexOf(e.target);
    const next=e.key==="Home"?0:e.key==="End"?tabs.length-1:(index+(e.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;
    e.preventDefault(); selectTaskTab(tabs[next]); return;
  }
  if (e.key==="Escape" && document.body.classList.contains("menu-open")) {closeMenu();$(".menu-toggle")?.focus();}
  if ((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"&&!$("#dialog").open) {
    e.preventDefault();if(activeView!=="tasks")location.hash="tasks";
    requestAnimationFrame(()=>$("#task-search")?.focus());
  }
});
window.addEventListener("hashchange",()=>{if($("#dialog").open)$("#dialog").close();render();$("#main").focus({preventScroll:true});});
document.addEventListener("visibilitychange",()=>{if(!document.hidden)void refresh();});
render(); void getSchemas(); void refresh();
setInterval(()=>{if(!document.hidden)void refresh();},5000);
