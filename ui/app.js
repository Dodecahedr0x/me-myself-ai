"use strict";

const $ = selector => document.querySelector(selector);
const esc = (value = "") => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const plain = value => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);
const titleCase = text => String(text ?? "").replace(/[_-]/g, " ").replace(/^./, c => c.toUpperCase());
const viewNames = {overview:"Home", tasks:"Tasks", activity:"Activity", connections:"Connections", projects:"Projects", policies:"Decision rules", skills:"Skills & style", settings:"Settings"};
const paths = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  tasks:'<rect x="5" y="4" width="15" height="17" rx="2"/><path d="M9 4V2m7 2V2M9 10h7m-7 4h5"/>',
  activity:'<path d="M2 12h5l3-7 4 14 3-7h5"/>', connections:'<path d="m9 15 6-6m-4-2 2-2a4.2 4.2 0 0 1 6 6l-3 3m-3 3-2 2a4.2 4.2 0 0 1-6-6l3-3"/>',
  projects:'<path d="M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  policies:'<path d="M4 6h4m5 0h7M4 12h9m5 0h2M4 18h2m5 0h9"/><circle cx="10.5" cy="6" r="2.2"/><circle cx="15.5" cy="12" r="2.2"/><circle cx="8.5" cy="18" r="2.2"/>',
  skills:'<path d="M3 5c4-1 6 0 9 2 3-2 5-3 9-2v14c-4-1-6 0-9 2-3-2-5-3-9-2Zm9 2v14"/>',
  settings:'<path d="m9.2 4 .7-2h4.2l.7 2 2 .9 2.1-.4 2.1 3.6-1.4 1.6.2 2.3 1.2 1.7-2.1 3.6-2-.3-2 1-.8 2h-4l-.8-2-2-1-2 .3-2.1-3.6 1.3-1.7.1-2.3-1.4-1.6L5.3 4.5l2 .4Z"/><circle cx="12" cy="11.5" r="3"/>',
  shield:'<path d="m12 2 8 3.5v6.2c0 5-3.9 8-8 10.3-4.1-2.3-8-5.3-8-10.3V5.5Z"/><path d="m8.8 11.6 2.2 2.1 4.4-4.6"/>',
  add:'<path d="M12 5v14M5 12h14"/>', arrow:'<path d="M4 12h15m-6-6 6 6-6 6"/>', chevron:'<path d="m9 5 7 7-7 7"/>',
  menu:'<path d="M3 5h18M3 12h18M3 19h18"/>', close:'<path d="m5 5 14 14m0-14L5 19"/>',
  check:'<path d="m4 12 5 5L20 6"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  review:'<path d="M3 5h18v12H9l-5 4v-4H3Z"/><path d="M7 9h10m-10 4h7"/>',
  working:'<path d="m13 2-9 12h7l-1 8 10-13h-7Z"/>', done:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  github:'<path d="M9 20c-3 1-4-1-5-2m10 4v-4c0-1 .2-2-.9-3 3-.3 6-1.5 6-6 0-1.3-.5-2.4-1.3-3.2.2-.8.2-2-.3-3.1 0 0-1.1-.3-3.5 1.2a11 11 0 0 0-6 0C5.7 2.4 4.5 2.7 4.5 2.7c-.5 1.1-.5 2.3-.3 3.1C3.4 6.6 3 7.7 3 9c0 4.5 3 5.7 6 6-1 1-1 1.7-1 3v4" transform="translate(1 0)"/>',
  slack:'<path d="M9 3v10m6-2v10M3 9h10m-2 6h10M6 3v2M3 18h2m13 3v-2m3-13h-2"/>',
  email:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 7 9-7"/>',
  sync:'<path d="M20 5v5h-5m-11 9v-5h5M5.5 9a7.5 7.5 0 0 1 12.8-3l1.7 3M4 15l1.7 3A7.5 7.5 0 0 0 18.5 15"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  stack:'<path d="m3 8 9-5 9 5-9 5Zm0 5 9 5 9-5M3 17l9 5 9-5"/>',
  flag:'<path d="M5 22V4m0 0c6-4 8 4 15 0v11c-7 4-9-4-15 0"/>', dot:'<circle cx="12" cy="12" r="4"/>', monitor:'<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8m-4-3v3"/>'
};
function icon(name, size = 18) {
  return '<svg aria-hidden="true" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">'+(paths[name] || paths.dot)+'</svg>';
}
const groups = {attention:["review","question","failed","uncertain"], progress:["queued","working","sending","scheduled"], completed:["done"]};
const statusNames = {queued:"Queued",working:"In progress",review:"Awaiting approval",question:"Needs an answer",done:"Completed",failed:"Failed",skipped:"Skipped",dismissed:"Dismissed",uncertain:"Delivery uncertain",sending:"Sending",scheduled:"Scheduled"};
const filters = {all:"All tasks",attention:"Needs attention",progress:"In progress",completed:"Completed",closed:"Skipped & dismissed"};
const compactDate = stamp => stamp ? new Date(Number(stamp)*1000).toLocaleDateString(undefined,{month:"short",day:"numeric"}) : "";
const fullDate = stamp => stamp ? new Date(Number(stamp)*1000).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}) : "";
function relativeDate(stamp) {
  if (!stamp) return "";
  const mins = Math.floor((Date.now()/1000-Number(stamp))/60);
  return mins < 0 ? compactDate(stamp) : mins < 1 ? "Just now" : mins < 60 ? mins+"m ago" : mins < 1440 ? Math.floor(mins/60)+"h ago" : compactDate(stamp);
}
const state = {tasks:[],activity:[],connections:[],rules:[],skills:[],projects:[],settings:{dry:true}};
let schemas = {}, loaded = false, refreshing = false, running = false, dialogState = null, invoker = null, noticeTimer;
let filter = "all", projectFilter = "", query = "", activeView = "";
let activityProject = "", activityQuery = "", activityConnection = "";
const providerNames = {claude:"Claude Code", codex:"Codex CLI"};
let providers = null, providersPending = false, providersChecked = 0;
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
    const match = controls.find(n => n.id && n.id===active.id || n.dataset.id && n.dataset.id===active.dataset.id && n.dataset.action===active.dataset.action || n.getAttribute("href") && n.getAttribute("href")===active.getAttribute("href"));
    if (match) match.focus({preventScroll:true});
  }
}
const recent = (list,key) => [...list].sort((a,b) => Number(b[key]||0)-Number(a[key]||0));
function shell(view) {
  const nav = key => '<a href="#'+key+'" '+(view===key?'aria-current="page"':'')+'>'+icon(key,16)+'<span>'+esc(viewNames[key])+'</span></a>';
  setIfChanged($("#sidebar"), '<div class="brand">'+mark+'<span>MeMyselfAI</span></div><nav><div class="navgroup">'+["overview","tasks","activity"].map(nav).join("")+'</div><div class="navgroup">'+["connections","projects","policies","skills"].map(nav).join("")+'</div></nav><div class="sidebar-foot">'+nav("settings")+'</div>');
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
function taskContext(t) {
  const origin = t.origin || {};
  const evidence = t.evidence?.[0];
  const provider = origin.source || evidence?.source || t.source || "Local";
  const name = provider === "github" ? "GitHub" : titleCase(provider);
  const source = [name, origin.workspace, origin.channel || origin.account].filter(Boolean).join(" · ");
  const repository = origin.repository || t.repository || (evidence?.source === "Git" && evidence.folder);
  return [["Project", t.project], ["Repository", repository], ["Source", source]].filter(([, value])=>value);
}
function contextLine(t) {
  const fields = taskContext(t).map(([label,value])=>
    '<span class="context-field"><span class="context-label">'+label+'</span> '+esc(value)+'</span>'
  );
  return '<span class="task-context">'+fields.join("")+'</span>';
}
function rows(tasks) {
  return tasks.map(t=>{
    const preview = (t.status==="review"?t.draft:t.status==="question"?t.question:"") || t.result || t.reason || t.body || "";
    return '<button type="button" class="task-row'+(groups.attention.includes(t.status)?' needs-attention':'')+'" data-action="detail" data-id="'+esc(t.id)+'">'+
      '<span class="row-title"><strong>'+esc(t.title)+'</strong>'+contextLine(t)+'<small>'+esc(plain(preview).slice(0,200))+'</small><span class="row-mobile">'+esc(relativeDate(t.created))+'</span></span>'+
      status(t.status)+'<time class="task-age" title="'+esc(fullDate(t.created))+'">'+esc(relativeDate(t.created))+'</time></button>';
  }).join("");
}
function taskTable(tasks) {
  return '<div class="task-columns" aria-hidden="true"><span>Task</span><span>Status</span><span>Created</span></div>'+rows(tasks);
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
  return '<ul class="suggestion-evidence">'+records.map(r=>'<li><strong>'+esc(r.source)+'</strong> · '+
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
function overview() {
  const ordered = recent(state.tasks,"created");
  const attention = ordered.filter(t=>groups.attention.includes(t.status));
  const active = ordered.filter(t=>groups.progress.includes(t.status));
  const closed = ordered.filter(t=>!groups.attention.includes(t.status) && !groups.progress.includes(t.status));
  const summary = [["attention","Needs attention",attention.length],["progress","In progress",active.length],["completed","Completed",closed.filter(t=>t.status==="done").length]]
    .map(([key,label,count])=>'<button type="button" class="counter" data-action="filter" data-filter="'+key+'">'+label+' <strong>'+count+'</strong></button>').join("");
  const queue = ordered.length ? taskTable([...attention,...active,...closed].slice(0,12)) : empty("No tasks yet.");
  const events = recent(state.activity,"at").slice(0,8);
  const projects = state.projects.map(p=>{
    const count = [...attention,...active].filter(t=>inProject(p.name,t.project)).length;
    return entry("projects",p.name,[count+" open",p.repos?.length?p.repos.length+(p.repos.length===1?" repository":" repositories"):""].filter(Boolean).join(" · "),button("Edit","project",null,"quiet",'data-project="'+esc(p.name)+'"'));
  }).join("");
  const styleReviews = state.skills.filter(s=>s.kind==="style" && !s.active && s.status==="pending")
    .map(s=>'<button class="skill-row" data-action="skill" data-id="'+esc(s.id)+'"><span class="row-title"><strong>'+esc(s.body)+'</strong></span>'+icon("chevron",14)+'</button>').join("");
  const manage = '<a class="btn secondary" href="#connections">Manage connections</a>';
  return '<section class="home-queue"><div class="queue-heading"><h2>Task queue</h2><div class="counters">'+summary+'</div>'+link("All tasks","tasks")+'</div>'+queue+'</section>'+taskSuggestions(false)+
    '<div class="overview-grid">'+section("Recent activity",link("View log","activity"),events.length?timeline(events):empty("No activity yet."))+
    '<div>'+section("Connections",manage,"")+
    (projects?section("Projects",link("View all","projects"),projects):"")+
    (styleReviews?section("Style to review",link("View all","skills"),styleReviews):"")+'</div></div>';
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
  const statuses = filter==="closed" ? ["skipped","dismissed"] : groups[filter];
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
  return testing+'<div class="filters">'+searchInput("task-search",query,"Search tasks")+projectSelect("task-project",projectFilter)+'</div><div class="tabs">'+Object.entries(filters).map(([key,label])=>'<button type="button" data-action="filter" data-filter="'+key+'" aria-pressed="'+(filter===key)+'">'+label+'</button>').join("")+'</div><div id="task-list">'+taskList()+'</div>'+taskSuggestions();
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
  return '<div class="entry"><div class="source-icon">'+icon(glyph)+'</div><div class="entry-text"><strong>'+esc(title)+'</strong><p>'+esc(detail)+'</p></div><div class="entry-actions">'+controls+'</div></div>';
}
function logLink(id) {
  return link("View log","activity?connection="+esc(encodeURIComponent(id)));
}
function connectionsPage() {
  const hasGoogle = state.connections.some(c=>["google","workspace_connector"].includes(c.auth_method));
  const accounts = state.connections.map(c=>{
    const id='data-id="'+esc(c.id)+'"';
    const browse=c.repositories?button("Browse work","github-work",null,"secondary",id):"";
    const row=entry(c.kind,c.label||titleCase(c.kind),[c.auth_method==="gh_account"?"":c.identity,c.google_services?.join(", ")||(c.auth_method==="slack_workspace"?(c.channel_count||0)+" channels":titleCase(c.kind)),titleCase(c.status)].filter(Boolean).join(" · "),
      logLink(c.id)+browse+button("Configure","connect",null,"secondary",id)+button("Disconnect","disconnect",null,"quiet",id));
    if (!c.repositories) return row;
    return row+'<details class="github-inventory"><summary>'+c.repositories.length+' repositories</summary>'+c.repositories.map(r=>'<p>'+sourceReference("https://github.com/"+r.name,r.name)+' · '+esc(r.status)+'</p>').join("")+'</details>';
  }).join("");
  const providers = Object.entries(schemas).map(([key,s])=>entry(key,key==="email"?"Google":s.label||titleCase(key),({email:"Choose your Google account in your browser",slack:"All workspace channels",github:"Repository issues and pull requests"})[key]||"",button(key==="slack"?"Connect Slack":key==="email"?(hasGoogle?"Add Google account":"Connect Google"):"Connect","connect","add","secondary",'data-kind="'+esc(key)+'"'))).join("");
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
const workViews=[["assigned","Assigned issues"],["issues","My open issues"],["pulls","My open pull requests"],["reviews","Requested reviews"]];
async function browseGitHub(connection,view="assigned",page=1) {
  showDialog("github-work",connection.label,selectField("github-view","Open work",workViews,view)+
    '<div id="github-results" aria-live="polite">Loading…</div>',{connection,view,page});
  const ctx=dialogState;
  try {
    const data=await request("/api/action",{op:"github_browse",id:connection.id,view,page});
    if (!authOpen(ctx)) return;
    const monitored=new Set(connection.repositories.map(r=>r.name.toLowerCase()));
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
  return (state.projects.length?state.projects.map(p=>entry("projects",p.name,[p.global?"Workspace-wide":p.parent?"Under "+p.parent:null,...(p.repos||[])].filter(Boolean).join(" · ")||"No linked repositories",button("Edit","project",null,"secondary",'data-project="'+esc(p.name)+'"'))).join(""):empty("No projects yet."));
}
const modes = {auto:"Allow autonomous handling",review:"Require review",user:"Leave to me"};
function policiesPage() {
  return '<div class="policy-note">'+icon("shield")+'<span>'+esc(state.settings.dry?"Dry mode requires approval for every external write.":"Live mode follows the rules below.")+'</span></div>'+
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
function settingsPage() {
  return section("Agent configuration","", entry("shield","Delivery mode",state.settings.dry?"Dry mode · External writes require your approval.":"Live mode · Project decision rules govern delivery.",button("Configure","settings",null))+
      entry("working","Agent provider",Object.hasOwn(providerNames,state.settings.provider)?providerLabel(state.settings.provider):"Choose a provider to prepare tasks.",button("Choose provider","settings",null)+button("Check again","providers","sync","quiet")))+
    section("Sample workspace","",entry("stack","Load sample work","Add fixture tasks and activity to explore the workspace.",button("Load sample","demo",null)));
}
const pages = {overview,tasks:tasksPage,activity:activityPage,connections:connectionsPage,projects:projectsPage,policies:policiesPage,skills:skillsPage,settings:settingsPage};
function closeMenu() {
  document.body.classList.remove("menu-open"); $("#scrim").hidden=true;
  $(".menu-toggle")?.setAttribute("aria-expanded","false");
}
function render() {
  const [key,params] = location.hash.slice(1).split("?"), view = Object.hasOwn(pages,key)?key:"overview";
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
  }
  activeView=view; setIfChanged($("#main"),pages[view]());
  if (view==="settings") void loadProviders();
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
    if (dialogState?.type==="detail") {
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
async function chooseRepository(button) {
  button.disabled=true;
  try {
    const picker=window.webkit?.messageHandlers?.chooseRepository;
    const path=picker?await picker.postMessage(null):(await request("/api/action",{op:"choose_repository"})).path;
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
  return '<dl class="facts">'+items.filter(([,v])=>v!=null && v!=="").map(([k,v])=>'<div><dt>'+esc(k)+'</dt><dd>'+esc(plain(v))+'</dd></div>').join("")+'</dl>';
}
function group(label,html) { return '<section class="detail-group"><h3>'+esc(label)+'</h3>'+html+'</section>'; }
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
function detailTask(t) {
  const id='data-id="'+esc(t.id)+'"';
  const detail = '<div class="detail-status">'+status(t.status)+'</div>'+
    facts([...taskContext(t),["Local checkout",t.origin?.repository && t.repository],["Opened",fullDate(t.created)],["Due",fullDate(t.due)],["Decision type",t.kind],["Worktree",t.worktree]])+
    (t.url?'<dl class="facts"><div><dt>Source address</dt><dd>'+sourceReference(t.url)+'</dd></div></dl>':"")+
    (t.parent && findTask(t.parent)?group("Linked task",button(findTask(t.parent).title,"detail","arrow","secondary",'data-id="'+esc(t.parent)+'"')):"")+
    (present(t.reason)?group("Why this surfaced",quote(t.reason)):"")+
    (present(t.body)?group("Task context",quote(t.body)):"")+
    (present(t.result)?group("Result & evidence",quote(t.result)):"")+
    (t.findings?.length?group("Test findings",t.findings.map(id=>button(findTask(id)?.title||id,"detail","tasks","secondary",'data-id="'+esc(id)+'"')).join("")):"")+
    (t.special==="app_finding" && t.milestones?.issue?group("GitHub",sourceReference("https://github.com/"+t.milestones.repository+"/issues/"+t.milestones.issue,"Issue #"+t.milestones.issue)+" "+sourceReference(t.milestones.url,"Pull request #"+t.milestones.pr)+" "+sourceReference(t.receipts.find(r=>r.operation==="submit PR review")?.receipt,"Posted review")):"")+
    (present(t.question)?group("Agent question",quote(t.question)):"")+
    (present(t.feedback)?group("Feedback & answers",t.feedback.map(f=>quote(f.text||[f.question,f.answer].filter(Boolean).join("\n"))).join("")):"")+
    (present(t.receipts)?group("Delivery receipts",t.receipts.map(r=>facts([["Operation",r.operation],["Recorded",fullDate(r.at)],["Receipt",r.receipt]])+quote(r.text)).join("")):"")+
    (t.milestones && Object.keys(t.milestones).length?group("Verified milestones",facts(Object.entries(t.milestones))):"");
  const recovery = (Array.isArray(t.recovered)&&t.recovered.length)?group("Recovered tasks",t.recovered.map(id=>{
    const found=findTask(id); return found?button(found.title,"detail","tasks","secondary",'data-id="'+esc(found.id)+'"'):'<p>'+esc(id)+'</p>';
  }).join("")):"";
  const categories = [...new Set([t.kind,...(t.categories||[]),...(present(t.commitments)?["commitment"]:[])].filter(Boolean))];
  const applicable=categories.map(k=>[titleCase(k),modes[state.rules.find(r=>r.project===t.project&&r.kind===k)?.mode]||"Require review"]);
  const actionPreview = (t.status==="review" || present(t.draft)) ? group("Proposed action",facts([["Operation",t.operation||t.kind||"External write"],["Sending account",t.account||"Unavailable"],["Destination",t.destination||"Unavailable"],["Subject",t.subject],["Version",t.version]])+draftPreview(t)+
    (present(t.commitments)?group("Commitments",quote(t.commitments)):"")+
    (applicable.length?'<details><summary>Decision rules</summary>'+facts(applicable)+'</details>':"")):"";
  const activity=recent(state.activity.filter(a=>a.task!=null&&String(a.task)===String(t.id)),"at");
  const confirmable=present(t.draft)&&present(t.account)&&present(t.destination)&&t.version!=null;
  const actions = (t.status==="review"?button("Approve this version","approve","check","primary",id+' data-version="'+esc(t.version)+'" '+(!confirmable?"disabled":"")):"")+
    (t.status==="question"?button("Answer question","answer","review","primary",id):"")+
    (t.status==="uncertain"?button("Record delivery outcome","reconcile","review","secondary",id):"")+
    (t.status==="failed"?button("Retry preparation","retry","sync","secondary",id):"")+
    (["queued","review","question","failed","working","scheduled"].includes(t.status)?button("Give feedback","feedback",null,"secondary",id):"")+
    (["queued","review","question","failed","working","scheduled"].includes(t.status)?button("Dismiss","dismiss",null,"quiet",id):"");
  return '<div id="stale" class="policy-note" hidden>This task changed. '+button("View latest","detail","sync","secondary",id)+'</div>'+detail+(t.evidence?group("Supporting activity",evidenceList(t.evidence)):"")+recovery+actionPreview+
    (actions?'<div class="detail-actions">'+actions+'</div>':"")+
    (activity.length?group("Task activity",timeline(activity)):"");
}
function showDialog(type,title,body,context={},footer="") {
  const d=$("#dialog");
  if (!d.open) invoker=document.activeElement;
  dialogState={type,...context}; d.className=type==="detail"?"wide":"";
  $("#dialog-body").innerHTML='<div class="dialog-head"><h2 id="dialog-title">'+esc(title)+'</h2><button type="button" class="iconbtn" aria-label="Close dialog" data-action="close">'+icon("close",17)+'</button></div><div class="dialog-content">'+body+'<div class="form-error" role="alert" id="form-error" hidden></div></div>'+footer;
  if (!d.open) d.showModal();
  const focusTarget = d.querySelector('textarea, input:not([readonly]), select') || d.querySelector("[data-action=close]");
  if (focusTarget) focusTarget.focus({preventScroll:true});
  d.scrollTop=0;
}
const formDrafts=new Map();
function draftKey(type,ctx) { return JSON.stringify([type,ctx.id||"",ctx.kind||"",ctx.project||""]); }
function openForm(type,title,fields,submit,ctx={},description="") {
  const body=(description?'<p class="form-intro">'+esc(description)+'</p>':"")+'<form id="editor">'+fields+'</form>';
  showDialog(type,title,body,ctx,'<div class="dialog-footer">'+button("Close","close",null,"quiet")+'<button type="submit" class="btn primary" form="editor">'+esc(submit)+'</button></div>');
  const stored=formDrafts.get(draftKey(type,ctx));
  if (stored) for (const [key,val] of Object.entries(stored)) {
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
    case "filter": filter=el.dataset.filter; if (activeView!=="tasks") {projectFilter="";query="";location.hash="tasks";} else render(); return;
    case "reload": void getSchemas(); void refresh(); return;
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
    case "settings": void loadProviders(); openForm("settings","Agent settings",selectField("dry","Delivery mode",[["true","Dry mode — review every external write"],["false","Live mode — follow project rules"]],String(!!state.settings.dry))+providerField(),"Save settings",{},"In live mode the agent may deliver work without asking when all applicable rules allow it."); return;
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
    case "detail": if (t) showDialog("detail",t.title,detailTask(t),{task:t,signature:JSON.stringify(t)}); return;
    case "answer":
      if (t?.repository_question) {
        const scope=state.projects.find(p=>p.name===t.project);
        const paths=[...new Set(state.projects.filter(p=>includesProject(scope,p)).flatMap(p=>p.repos||[]))];
        if (paths.length) {
          openForm("answer","Choose repository",selectField("text","Local repository",[["","Choose a repository"],...paths.map(p=>[p,p])],""),"Submit answer",{id},t.question);
          return;
        }
      }
    case "feedback": if (t) openForm(action,action==="answer"?"Answer agent question":"Give task feedback",input("text",action==="answer"?"Your answer":"Feedback",{type:"textarea",required:true}),action==="answer"?"Submit answer":"Submit feedback",{id},action==="answer"?(t.question||t.title):t.title); return;
    case "reconcile": if (t) openForm(action,"Record delivery outcome",facts([["Operation",t.operation||"reply"],["Account",t.account],["Destination",t.destination],["Subject",t.subject],["Version",t.version]])+draftPreview(t)+selectField("outcome","Verified provider outcome",[["","Choose the verified outcome"],["yes","I verified it was delivered"],["no","I verified it was not delivered"]],"")+(["create issue","create pull request","submit PR review"].includes(t.operation)?input("number",t.operation==="submit PR review"?"GitHub review ID":t.operation==="create issue"?"Verified issue number":"Verified pull request number",{type:"number",help:"Required when recording a verified delivery."}):""),"Record outcome",{id,version:t.version},"Check the destination before recording the outcome of this version."); return;
    case "skill": {
      const s=state.skills.find(k=>String(k.id)===id); if (!s) return;
      const previous=recent(state.skills.filter(x=>x.kind===s.kind&&Number(x.at)<Number(s.at)),"at")[0];
      const body=facts([["Revision",s.id],["Saved",fullDate(s.at)],["Status",skillState(s)],["Reason",s.reason]])+group("Revision text",quote(s.body))+(previous?'<details><summary>Changes from previous revision</summary>'+revisionDiff(previous.body,s.body)+'</details>':"");
      const control=s.active&&(s.kind!=="workflow"||previous)?button("Revert revision","revert_skill",null,"secondary",'data-id="'+esc(s.id)+'"'):!s.active && s.kind==="style" && !["Declined","Reverted"].includes(skillState(s))?button("Decline","reject_skill",null,"secondary",'data-id="'+esc(s.id)+'"')+button("Approve style","accept_skill","check","primary",'data-id="'+esc(s.id)+'"'):"";
      showDialog("skill",titleCase(s.kind)+" revision",body,{id},control?'<div class="dialog-footer">'+control+'</div>':"");
      return;
    }
    case "dismiss":
    case "disconnect":
    case "demo":
    case "revert_skill": {
      const copy={dismiss:["Dismiss task","This task will leave your active queue.","Dismiss task"],disconnect:["Disconnect account","The agent will stop reading from and delivering to this connection.","Disconnect"],demo:["Load sample workspace","Add sample tasks and activity to this local workspace.","Load samples"],revert_skill:["Revert this revision","The next agent will use the resulting active instructions.","Revert revision"]}[action];
      openForm(action,copy[0],"",copy[2],{id},copy[1]); return;
    }
    case "approve": void mutate({op:action,id,version:Number(el.dataset.version)},"Approval recorded."); return;
    case "reject_skill": void mutate({op:action,id},"Style suggestion declined."); return;
    case "accept_skill": void mutate({op:action,id},"Style guidance approved."); return;
    case "retry": void mutate({op:action,id},"Preparation queued."); return;
    case "sync": void mutate({op:action},"Source sync requested."); return;
  }
}
const messages={app_finding:"Finding tracked.",chat_source_config:"Chat source saved.",suggestion_config:"Activity sources saved.",task:"Task created.",project:"Project saved.",report:"Investigation requested.",style:"Style preference submitted for review.",rule:"Decision rule saved.",settings:"Agent settings saved.",connect:"Connection saved.",feedback:"Feedback recorded.",answer:"Answer submitted.",reconcile:"Delivery outcome recorded.",dismiss:"Task dismissed.",disconnect:"Account disconnected.",demo:"Sample workspace loaded.",revert_skill:"Revision reverted."};
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
  if (el&&!el.disabled) handleAction(el);
  if (event.target.closest("#sidebar a")) closeMenu();
});
document.addEventListener("input",event=>{
  if (event.target.name==="refresh_token" && dialogState?.stage==="slack_token") {
    const rotating=Boolean(event.target.value.trim());
    $("#slack-issuer").hidden=!rotating;
    event.target.form.elements.client_id.required=rotating && !dialogState.registration.configured;
  }
  const el=event.target;
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
  if (event.target.id==="task-project") {projectFilter=event.target.value;setIfChanged($("#task-list"),taskList());}
  if (event.target.id==="activity-connection") location.hash = event.target.value?"activity?connection="+encodeURIComponent(event.target.value):"activity";
  if (event.target.id==="activity-project") {activityProject=event.target.value;setIfChanged($("#activity-list"),activityList());}
  if (event.target.closest("#editor")) rememberForm();
});
document.addEventListener("submit",event=>{
  if (event.target.id==="auth-form") {event.preventDefault();void submitSignIn(event.target);return;}
  if (event.target.id!=="editor") return;
  event.preventDefault();
  const form=event.target, ctx=dialogState, fields=Object.fromEntries(new FormData(form)), op=ctx.type;
  let payload={op,...fields};
  if (ctx.id!=null) payload.id=ctx.id;
  if (op==="project") {delete payload.repo;payload.repos=new FormData(form).getAll("repo").map(p=>p.trim()).filter(Boolean);}
  if (op==="chat_source_config") payload={op,provider:dialogState.kind,enabled:true,folder:fields.folder||""};
  if (op==="suggestion_config") payload={op,foreground:fields.foreground==="true",folders:new FormData(form).getAll("repo").map(p=>p.trim()).filter(Boolean)};
  if (op==="settings") payload.dry=fields.dry==="true";
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
  void mutate(payload,messages[op]);
});
$("#dialog").addEventListener("cancel",e=>{if(running)e.preventDefault();});
$("#dialog").addEventListener("close",()=>{cancelSignIn(dialogState);dialogState=null;$("#dialog-body").replaceChildren();if(invoker?.isConnected)invoker.focus({preventScroll:true});else $("#main").focus({preventScroll:true});invoker=null;});
$("#scrim").addEventListener("click",closeMenu);
document.addEventListener("keydown",e=>{
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
