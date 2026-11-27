"use strict";

const main = document.querySelector("#main");
const dialog = document.querySelector("#dialog");
const notice = document.querySelector("#notice");
const unavailable = "NOT_AVAILABLE";
let tokenPromise;
let routeVersion = 0;
let refreshTimer;
let noticeTimer;
let draftLaunch;
const sessionJobs = new Map();
const demoJobs = new Map();
const demoResults = new Map();
const draft = { workspace: "", title: "", task: "", test_command: "", timeout: 180 };
const labels = {overview:"总览", workspace:"运行工作台", runs:"运行记录", research:"实验分析", system:"系统状态"};
const scenarios = [
  ["stale-write", "过期 Token 拒绝", "⟡", "旧 Attempt 在资格更新后尝试发布。", "旧 Token 被拒绝，新 Token 发布。"],
  ["idempotency-conflict", "幂等重放与冲突", "↻", "用同一个幂等键提交不同内容。", "首次内容保留，冲突被记录。"],
  ["crash-window", "崩溃恢复", "◴", "接收器落账后，Worker 在本地回执写入前退出。", "从目标账本补齐回执，随后重放已有结果。"],
  ["cancellation", "任务取消", "⊘", "启动本地子进程后撤销执行资格并发出停止信号。", "观测进程退出后释放执行槽位。"],
  ["corrected-effect", "后续效果修正", "✎", "第二个效果修正首次已发布内容。", "保留两次发布回执。"],
  ["walkthrough", "容器综合演示", "▣", "在容器中连续执行发布、资格切换、对账与取消。", "需要可用的 Docker 引擎。"],
  ["unknown-outcome", "UNKNOWN 证据边界", "?", "登记一个无法确认的外部效果。", "保留 UNKNOWN，回执为空。"],
];
const mechanisms = [
  ["Proposal", "执行提案", "查看账本记录的副作用意图、工具和入参摘要。"],
  ["Admission", "执行资格", "RunLedger 用当前资格、Fencing Token 和状态版本裁决受控操作。"],
  ["Effect", "效果提交", "受控接收器或文件发布边界判断幂等键和提交条件，效果状态独立记录。"],
  ["Receipt", "效果回执", "容器与文件发布可记录读回校验。SQLite 接收器可按幂等键读取持久目标记录。"],
  ["Recovery", "恢复判断", "按目标记录补齐回执、返回已有结果、判定未生效，或保留 UNKNOWN。"],
];

const esc = (value) => String(value ?? unavailable).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
const enc = encodeURIComponent;
const runLink = (id) => `#/runs/${enc(id)}`;
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const localTime = new Intl.DateTimeFormat("zh-CN", {dateStyle:"short", timeStyle:"medium"});
const time = (value) => value && Number.isFinite(Date.parse(value)) ? localTime.format(new Date(value)) : unavailable;
function raw(value) {
  if (typeof value === "string") return `<pre>${esc(value)}</pre>`;
  const text = JSON.stringify(value,null,2) ?? "";
  const tokens = /"(?:[^"\\]|\\.)*"(?=\s*:)|"(?:[^"\\]|\\.)*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  let output = "";
  let cursor = 0;
  for (const token of text.matchAll(tokens)) {
    const value = token[0];
    const end = token.index + value.length;
    const kind = value.startsWith('"') ? text.slice(end).trimStart().startsWith(":") ? "key" : "string" : "literal";
    output += esc(text.slice(cursor,token.index)) + `<span class="code-${kind}">${esc(value)}</span>`;
    cursor = end;
  }
  return `<pre>${output}${esc(text.slice(cursor))}</pre>`;
}
const details = (title, value) => `<details><summary>${esc(title)}</summary>${raw(value)}</details>`;
const pill = (value) => {
  const label = String(value ?? unavailable).toUpperCase();
  const tone = /^(VERIFIED|COMPLETED|COMMITTED|CONNECTED|AVAILABLE|ACCEPTED|SUCCEEDED|MATCH)$/.test(label) ? "good"
    : /UNKNOWN|PENDING|PARTIAL|NOT_OBSERVED|CONFIGURED|QUEUED|RUNNING/.test(label) ? "warn"
    : /FAILED|REJECTED|MISMATCH|ERROR|WITHHELD|VIOLATION/.test(label) ? "bad" : "";
  const meaning = {COMMITTED:"已提交",VERIFIED:"已核实",REJECTED:"已拒绝",UNKNOWN:"未知",OUTCOME_UNKNOWN:"效果未知",
    RECOVERED:"已恢复",FAILED:"失败",NOT_OBSERVED:"未观测",NOT_AVAILABLE:"不可用"};
  return `<span class="badge ${tone}" title="${meaning[label] ?? ""}">${esc(label)}</span>`;
};
const empty = (message = "暂无记录。") => `<div class="empty" role="status">${esc(message)}</div>`;
const errorBox = (error) => `<div class="empty error" role="alert">${esc(error.message ?? error)}</div>`;
const button = (label, attrs = "", tone = "") => `<button type="button" class="button ${tone}" ${attrs}>${label}</button>`;
const link = (label, href, tone = "") => `<a class="button ${tone}" href="${href}">${label}</a>`;
const metric = (label, value, hint = "") => `<article class="card metric c3"><span>${esc(label)}</span><strong class="${/[A-Z]{3}/.test(String(value)) ? "metric-status" : ""}">${esc(value)}</strong><small>${esc(hint)}</small></article>`;
const heading = (title, description, actions = "") => `<header class="heading"><div><h1>${title}</h1><p>${description}</p></div><div class="actions">${actions}</div></header>`;
const section = (title, content, action = "") => `<section class="stack-small"><header class="heading"><h2>${title}</h2>${action}</header>${content}</section>`;
const table = (headers, rows) => rows.length ? `<div class="table-wrap"><table><thead><tr>${headers.map(item => `<th scope="col">${item}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(item => `<td>${item}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty();
const fields = (items) => `<dl>${items.map(([name, value]) => `<dt>${esc(name)}</dt><dd>${value ?? esc(unavailable)}</dd>`).join("")}</dl>`;
const get = (parent, selector) => parent.querySelector(selector);
const scenarioName = (key) => scenarios.find(item => item[0] === key)?.[1] ?? ({workspace:"真实任务", runtime:"Runtime"})[key] ?? key;
const titleFor = (row) => row.task_title || row.title || row.task || scenarioName(row.scenario) || row.run_id;
const columnPreferences = new Map();

function tableColumns(container, key) {
  const grid = get(container,"table");
  if (!grid) return;
  const excluded = columnPreferences.get(key) ?? new Set();
  columnPreferences.set(key,excluded);
  const headings = [...grid.querySelectorAll("thead th")];
  const control = document.createElement("details");
  control.innerHTML = `<summary>列显示</summary><div class="filters">${headings.map((heading,index) =>
    `<label class="inline"><input type="checkbox" value="${index}" ${excluded.has(index)?"":"checked"} ${index===0?"disabled":""}>${esc(heading.textContent)}</label>`
  ).join("")}</div>`;
  container.prepend(control);
  const paint = () => {
    for (const row of grid.rows) {
      [...row.cells].forEach((cell,index) => { cell.hidden=excluded.has(index); });
    }
  };
  control.onchange = event => {
    const index = Number(event.target.value);
    if (event.target.checked) excluded.delete(index);
    else excluded.add(index);
    paint();
  };
  headings.forEach(heading => {
    const title = document.createElement("div");
    title.className = "column-title";
    title.textContent = heading.textContent;
    heading.replaceChildren(title);
  });
  paint();
}

async function api(path, body, {blob = false, launchKey} = {}) {
  tokenPromise ??= fetch("/session-token", {cache:"no-store"}).then(async response => {
    if (!response.ok) throw new Error("无法取得本地控制面凭证。");
    return (await response.json()).token;
  }).catch(error => {
    tokenPromise = undefined;
    throw error;
  });
  const token = await tokenPromise;
  const response = await fetch(path, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : {"Content-Type":"application/json"}),
      ...(launchKey ? {"Idempotency-Key":launchKey} : {}) },
    ...(body === undefined ? {} : {method:"POST", body: JSON.stringify(body)}),
  });
  if (!response.ok) {
    if (response.status === 401) tokenPromise = undefined;
    const data = await response.json().catch(() => ({}));
    const requestId = response.headers.get("X-Request-Id");
    if (response.status === 404 && path.startsWith("/observatory/") && !data.code) {
      const health = await fetch("/health",{cache:"no-store"}).then(reply => reply.ok ? reply.json() : null).catch(() => null);
      if (health?.authority === "run_ledger" && !health.mode) {
        throw new Error("当前端口仍在运行旧版 EpochGate 后端。请停止旧服务，使用原数据目录重启并刷新页面。");
      }
    }
    throw new Error(`${data.message || (Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("，") : `HTTP ${response.status}`)}${requestId ? ` · ${requestId}` : ""}`);
  }
  return blob ? response.blob() : response.json();
}

function toast(message, failure = false) {
  notice.textContent = message;
  notice.classList.toggle("error", failure);
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}

function modal(title, html) {
  get(dialog, "#dialog-title").textContent = title;
  get(dialog, "#dialog-content").innerHTML = html;
  if (!dialog.open) dialog.showModal();
  return get(dialog, "#dialog-content");
}

function confirmAction(title, message) {
  const content = modal(title, `<p>${esc(message)}</p><form method="dialog" class="actions"><button class="button soft" value="cancel">返回</button><button class="button danger" value="confirm">确认</button></form>`);
  dialog.returnValue = "";
  get(content, '[value="cancel"]').focus();
  return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once:true}));
}

function download(name, content, type = "application/json") {
  const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], {type}));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function runTable(runs, recent = false) {
  return table(["Run ID", "任务 / 场景", "账本阶段", "执行结果", "回执核验", ...(recent ? [] : ["创建时间"])], runs.map(row => [
    `<a class="link mono" href="${runLink(row.run_id)}">${esc(row.run_id)}</a>`,
    `${esc(titleFor(row))}<br><small class="muted">${esc(scenarioName(row.scenario))}</small>`,
    pill(row.phase), pill(row.execution_status ?? "NOT_OBSERVED"), pill(row.receipt_status), ...(recent ? [] : [esc(time(row.created_at))]),
  ]));
}

function diagram() {
  return `<div class="hero-art" aria-hidden="true"><div class="diagram"><svg viewBox="0 0 330 230" fill="none">
    <circle cx="165" cy="112" r="83" stroke="#93a792" stroke-dasharray="3 5"/>
    <circle cx="165" cy="112" r="64" stroke="#a6b7a1"/>
    <path d="M165 29v166M82 112h166" stroke="#a6b7a1"/>
    <path d="M165 58l54 54-54 54-54-54z" fill="#26362e"/>
    <path d="M165 77l35 35-35 35-35-35z" stroke="#d5e3cc"/>
    <path d="M153 111l9 9 17-18" stroke="#e4d4a9" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="165" cy="29" r="5" fill="#748c73"/><circle cx="248" cy="112" r="5" fill="#748c73"/><circle cx="82" cy="112" r="5" fill="#748c73"/>
    <path d="M263 171c17 0 31-13 34-32-17 2-30 13-34 32zM263 171c-13-10-16-23-12-36 11 8 16 20 12 36zM257 191l9-27" stroke="#748c73" stroke-width="1.5"/>
    <text x="108" y="216">EFFECT BOUNDARY</text>
  </svg></div></div>`;
}

function sceneCards() {
  return `<div class="grid">${scenarios.slice(0,4).map(([id,title,icon,description,expected]) => `<article class="card scenario c3" id="scene-${id}">
    <span class="scene-icon" aria-hidden="true">${icon}</span><h3>${title}</h3>
    <div><p>${description}</p><p><strong>预期</strong> ${expected}</p></div>
    <div class="actions">${button("运行场景", `data-demo="${id}" ${demoJobs.has(id) ? "disabled" : ""}`, "soft")}${demoResults.has(id) ? `<a class="link" href="${runLink(demoResults.get(id))}">查看结果</a>` : ""}</div>
  </article>`).join("")}</div>`;
}

async function startDemo(id, control) {
  if (demoJobs.has(id)) return;
  control.disabled = true;
  demoJobs.set(id, true);
  toast("正在启动演示…");
  const origin = location.hash;
  const version = routeVersion;
  try {
    const started = await api(`/actions/${enc(id)}`, {});
    const result = await waitJob(started.job_id);
    if (result.run_id) demoResults.set(id, result.run_id);
    if (result.status === "failed") throw new Error(result.error || "演示失败。");
    toast(`演示运行结束 · ${result.run_id}`);
    if (result.run_id && location.hash === origin && version === routeVersion) location.hash = runLink(result.run_id);
  } catch (error) {
    toast(error.message, true);
  } finally {
    demoJobs.delete(id);
    control.disabled = false;
    if (control.isConnected && demoResults.has(id)) {
      const actions = control.parentElement;
      if (!actions.querySelector("a")) actions.insertAdjacentHTML("beforeend", `<a class="link" href="${runLink(demoResults.get(id))}">查看结果</a>`);
    }
  }
}

async function waitJob(id) {
  for (let count = 0; count < 600; count += 1) {
    const job = await api(`/jobs/${enc(id)}`);
    if (job.status === "failed" || job.status === "succeeded") return job;
    await sleep(1000);
  }
  throw new Error("追踪任务超时，可在运行记录中继续查看。");
}

async function overview(version) {
  main.innerHTML = `<div class="stack">
    <section class="hero"><div class="hero-main"><p class="eyebrow">AGENT EXECUTION OBSERVATORY</p>
      <h1>让每一次工具执行<br>都有资格依据与效果回执</h1>
      <p>EpochGate 为工具型 Agent 提供执行资格检查、受控效果提交、幂等与崩溃恢复，并通过目标端证据核验实际效果。</p>
      <div class="actions">${button("运行一个 Demo", 'id="quick-demo"')}${link("创建真实任务", "#/workspace", "outline")}</div>
    </div>${diagram()}</section>
    <div id="metrics" class="grid">${[["运行总数","RunLedger"],["已确认效果","目标端记录或发布读回"],["拒绝提交","提交拒绝事件"],["待核实结果","明确记录的 UNKNOWN 效果"]].map(([name,hint]) => metric(name,"—",hint)).join("")}</div>
    ${section("从执行提案到恢复决定", `<div class="flow">${mechanisms.map(([name,zh],index) => `<button type="button" data-mechanism="${index}"><strong>${name}</strong><small>${zh}${index < 4 ? " →" : ""}</small></button>`).join("")}</div>`)}
    ${section("快速体验", `<div id="scenario-cards">${sceneCards()}</div>`)}
    ${section("最近运行", `<div class="card" id="recent">${empty("正在读取账本…")}</div>`, `<a class="link" href="#/runs">全部运行 →</a>`)}
    ${section("本地运行环境", `<div class="card" id="overview-health">${empty("正在执行健康检查…")}</div>`, `<a class="link" href="#/system">组件详情 →</a>`)}
  </div>`;
  get(main, "#quick-demo").onclick = () => startDemo("stale-write", get(main, "#quick-demo"));
  main.querySelectorAll("[data-mechanism]").forEach(control => control.onclick = () => {
    const [name, zh, description] = mechanisms[Number(control.dataset.mechanism)];
    modal(`${name} · ${zh}`, `<p>${description}</p><div class="actions">${link("查看运行证据", "#/runs", "soft")}</div>`);
  });
  await Promise.all([
    api("/runs").then(({runs}) => {
      if (version !== routeVersion) return;
      const sum = key => runs.reduce((total, row) => total + (row[key] ?? 0), 0);
      for (const [id] of scenarios) {
        const latest = runs.find(row => row.scenario === id && row.execution_status === "succeeded");
        if (latest && !demoResults.has(id)) demoResults.set(id, latest.run_id);
      }
      get(main, "#scenario-cards").innerHTML = sceneCards();
      get(main, "#metrics").innerHTML = [
        ["运行总数",runs.length,"RunLedger"],["已确认效果",sum("confirmed"),"目标端记录或发布读回"],
        ["拒绝提交",sum("rejected_submissions"),"提交拒绝事件"],["待核实结果",sum("unknown_effects"),"明确记录的 UNKNOWN 效果"],
      ].map(item => metric(...item)).join("");
      get(main, "#recent").innerHTML = runTable(runs.slice(0,5), true);
    }).catch(error => { if (version === routeVersion) get(main,"#recent").innerHTML = errorBox(error); }),
    api("/observatory/system").then(system => {
      if (version !== routeVersion) return;
      get(main, "#overview-health").innerHTML = fields([
        ["Runtime / Python", esc(`${system.runtime} / ${system.python}`)], ["SQLite",pill(system.sqlite.status)],
        ["Docker",pill(system.docker.status)],["Worker",esc(`${system.worker.jobs.running} 个运行中`)],["最近检查",esc(time(system.checked_at))],
      ]);
    }).catch(error => { if (version === routeVersion) get(main,"#overview-health").innerHTML = errorBox(error); }),
  ]);
}

async function runsPage(version) {
  const {runs} = await api("/runs");
  if (version !== routeVersion) return;
  const options = (values, mapper = value => value) => values.map(value => `<option value="${esc(value)}">${esc(mapper(value))}</option>`).join("");
  main.innerHTML = `<div class="stack">${heading("运行记录", "按场景、时间或运行状态定位账本证据。", link("创建任务", "#/workspace"))}
    <section class="card stack-small"><form class="filters" id="filters">
    <label>搜索 <input type="search" name="search" placeholder="Run ID 或任务名称"></label>
    <label>状态 <select name="phase"><option value="">全部</option>${options([...new Set(runs.map(row => row.phase))], value => value.toUpperCase())}</select></label>
    <label>场景 <select name="scenario"><option value="">全部</option>${options([...new Set(runs.map(row => row.scenario))], scenarioName)}</select></label>
    <label>执行结果 <select name="result"><option value="">全部</option>${options(["succeeded","failed","rejected","unknown","recovered"])}</select></label>
    <label>仓库 <input name="repository"></label><label>Git SHA <input name="commit"></label>
    <label>排序 <select name="order"><option value="desc">最新</option><option value="asc">最早</option></select></label>
    <label>起始日期 <input type="date" name="from"></label><label>结束日期 <input type="date" name="to"></label>
    </form><div id="run-table"></div><div id="run-pagination" class="actions"></div></section>
    <div class="actions">${button("对比所选运行", 'id="compare-runs" disabled')}${button("导出筛选 JSON", 'id="export-runs"', "soft")}${button("导出筛选 CSV", 'id="export-runs-csv"', "soft")}${button("清空本地运行", 'id="clear-runs"', "danger")}</div>
  </div>`;
  const filter = get(main,"#filters");
  let shown = runs;
  let page = 0;
  const selected = new Set();
  const outcome = row => row.unknown_effects || row.phase === "unknown" ? "unknown" :
    row.execution_status === "failed" ? "failed" : row.rejected_submissions && !row.confirmed ? "rejected" :
    row.recovered ? "recovered" : row.execution_status;
  get(main,"#filters").closest("section").insertAdjacentHTML("beforebegin",`<div class="grid">${[
    ["总运行",runs.length,"账本"],["运行中",runs.filter(row=>row.phase==="running" && !row.execution_status && !row.unknown_effects).length,"尚无终态执行结果"],
    ["执行完成",runs.filter(row=>row.execution_status==="succeeded" || row.phase==="completed").length,"任务结果"],
    ["执行失败",runs.filter(row=>outcome(row)==="failed").length,"实际执行结果"],["效果未知",runs.filter(row=>outcome(row)==="unknown").length,"保留 UNKNOWN"],
    ["待恢复","NOT_AVAILABLE","需要显式的恢复待处理状态"]
  ].map(item=>metric(...item)).join("")}</div>`);
  const paint = () => {
    const {search,phase,scenario,from,to,result,repository,commit,order} = Object.fromEntries(new FormData(filter));
    const after = from ? new Date(`${from}T00:00:00`).getTime() : null;
    const before = to ? new Date(`${to}T23:59:59.999`).getTime() : null;
    shown = runs.filter(row => {
      const date = row.created_at ? Date.parse(row.created_at) : null;
      return `${row.run_id} ${titleFor(row)} ${row.task || ""}`.toLowerCase().includes(search.trim().toLowerCase())
        && (!phase || phase === row.phase) && (!scenario || scenario === row.scenario)
        && (!result || result === outcome(row)) && (!repository || row.repository?.toLowerCase().includes(repository.toLowerCase()))
        && (!commit || row.git_commit?.startsWith(commit.toLowerCase()))
        && (after === null || (date !== null && date >= after)) && (before === null || (date !== null && date <= before));
    });
    shown.sort((a,b) => (order === "asc" ? 1 : -1) * ((Date.parse(a.created_at)||0) - (Date.parse(b.created_at)||0) || a.last_seq - b.last_seq));
    const size = 15;
    const pages = Math.max(1,Math.ceil(shown.length/size));
    page = Math.min(page,pages-1);
    get(main, "#run-table").innerHTML = `<p>${shown.length} / ${runs.length} 次运行</p>${table(["选择","Run ID","任务","Attempt","Effect","结果","时间","操作"],shown.slice(page*size,(page+1)*size).map(row=>[
      `<input type="checkbox" aria-label="选择 ${esc(row.run_id)}" data-select-run="${esc(row.run_id)}" ${selected.has(row.run_id)?"checked":""}>`,
      `<a class="link mono" href="${runLink(row.run_id)}">${esc(row.run_id)}</a>`,esc(titleFor(row)),esc(row.attempt_count),esc(row.effect_count),
      pill(outcome(row) ?? row.phase),esc(time(row.created_at)),
      button("复制 ID",`data-copy-run="${esc(row.run_id)}"`,"soft")+button("JSON",`data-product-export="/runs/${enc(row.run_id)}?figures=false" data-filename="${esc(row.run_id)}.json"`,"soft")
    ]))}`;
    tableColumns(get(main,"#run-table"),"runs");
    get(main,"#run-pagination").innerHTML = `${button("上一页",`id="previous-page" ${page===0?"disabled":""}`,"soft")} ${page+1} / ${pages} ${button("下一页",`id="next-page" ${page+1===pages?"disabled":""}`,"soft")}`;
    get(main,"#previous-page").onclick = () => {page--; paint();};
    get(main,"#next-page").onclick = () => {page++; paint();};
    get(main,"#compare-runs").disabled = selected.size !== 2;
  };
  filter.onsubmit = event => event.preventDefault();
  filter.oninput = () => {page=0; paint();};
  get(main,"#run-table").onchange = event => {
    const id = event.target.dataset.selectRun;
    if (!id) return;
    if (event.target.checked && selected.size === 2) { event.target.checked=false; toast("一次选择两次运行。"); return; }
    if (event.target.checked) selected.add(id);
    else selected.delete(id);
    get(main,"#compare-runs").disabled = selected.size !== 2;
  };
  get(main,"#run-table").onclick = async event => {
    const id = event.target.closest("[data-copy-run]")?.dataset.copyRun;
    if (!id) return;
    try { await navigator.clipboard.writeText(id); toast("已复制 Run ID。"); } catch (error) { toast(error.message,true); }
  };
  get(main,"#compare-runs").onclick = async () => {
    const box = modal("Compare Runs",empty("正在读取两个 Run 的证据…"));
    try {
      const pair = [];
      for (const id of selected) pair.push(await api(`/runs/${enc(id)}?figures=false`));
      const counts = run => run.events.reduce((map,event) => ((map[event.event_type] ??= 0),map[event.event_type]++,map),Object.create(null));
      const [left,right] = pair.map(counts);
      const changed = [...new Set([...Object.keys(left),...Object.keys(right)])].filter(key => left[key] !== right[key]);
      if (!box.isConnected || version !== routeVersion || !dialog.open) return;
      box.innerHTML = `<div class="grid">${pair.map(run => `<div class="c6 stack-small"><h3>${esc(run.run_id)}</h3>${fields([
        ["最终阶段",pill(run.phase)],["执行结果",pill(run.summary.execution_status)],["耗时",esc(run.summary.execution_seconds)],
        ["Fencing Token",esc(run.fencing_token)],["回执核验",pill(run.summary.receipt_status)]
      ])}${details("Attempts",run.attempts)}${details("Effects",run.effects)}${details("Receipts",run.receipts)}
      ${details("Proposal / Admission / Recovery",run.events.filter(event=>/intent|attempt|reject|reconcile|eligib/i.test(event.event_type)))}
      ${details("原始事件顺序",run.events)}</div>`).join("")}</div><h3>不同事件计数</h3>${table(["事件",esc(pair[0].run_id),esc(pair[1].run_id)],changed.map(key=>[esc(key),left[key]??0,right[key]??0]))}`;
    } catch (error) { box.innerHTML=errorBox(error); }
  };
  get(main,"#export-runs-csv").onclick = async () => {
    if (!shown.length) { toast("没有可导出的运行。"); return; }
    try { download("epochgate-runs.csv",await api(`/observatory/runs/export?ids=${enc(shown.map(row=>row.run_id).join(","))}`,undefined,{blob:true})); toast("CSV 已生成。"); }
    catch (error) { toast(error.message,true); }
  };
  get(main, "#export-runs").onclick = () => download("epochgate-runs.json", JSON.stringify(shown,null,2));
  get(main, "#clear-runs").onclick = async () => {
    if (!await confirmAction("清空本地运行", "此操作会删除运行账本、会话和本地运行产物，无法撤销。")) return;
    try {
      const result = await api("/runs/clear", {});
      toast(`已清空 ${result.cleared} 次运行`);
      if (version === routeVersion) renderRoute();
    } catch (error) { toast(error.message, true); }
  };
  paint();
  const listing = document.createElement("section");
  listing.className = "card stack-small";
  listing.innerHTML = `<h2>Agent Intelligence</h2>${empty("正在读取持久任务…")}`;
  get(main,".stack")?.append(listing);
  try {
    const {runs: agentRuns} = await api("/agent/tasks");
    if (listing.isConnected) listing.innerHTML = `<h2>Agent Intelligence</h2>${table(["Run","状态","Epoch"],agentRuns.map(run=>[
      `<a class="link mono" href="${runLink(run.id)}">${esc(run.id)}</a>`,pill(run.status),esc(run.epoch)
    ]))}`;
  } catch (error) { if (listing.isConnected) listing.innerHTML = errorBox(error); }
}


async function workspacePage(version) {
  main.innerHTML = `<div class="stack">${heading("运行工作台", "选择代码来源、定义任务，并在启动前核对执行策略。")}
    <div id="last-launch" hidden></div><ol class="steps" aria-label="创建任务进度"><li>1 · 代码来源</li><li>2 · 任务定义</li><li>3 · 执行策略</li></ol>
    <div class="grid"><section class="card c8" id="workspace-step"></section><aside class="card c4 stack-small">
      <h2>本地执行环境</h2><div id="requirements">${empty("正在检查依赖…")}</div>
      <div class="actions">${button("启动 Docker / 复查", 'id="start-ready"', "soft")}</div>
      <h3>其他机制演示</h3><div class="actions">${scenarios.slice(4).map(([id,title]) => button(title, `data-demo="${id}"`, "soft")).join("")}</div>
    </aside></div></div>`;
  let step = 0;
  let sample;
  let preview;
  let preflight;
  const target = get(main,"#workspace-step");
  const planner = document.createElement("section");
  planner.className = "card stack-small";
  planner.innerHTML = `<h2>提交 Agent 计划</h2><p>任务由独立 Worker 领取，目标端为受控资源存储。可提交 plan，或以 prompt 和 resources 请求 DeepSeek 生成计划。</p>
    <form class="stack-small"><label>计划 JSON <textarea name="plan" required placeholder='{"plan":[{"step_id":"publish","effect":{"demo-artifact":"content"}}]}'></textarea></label>
    <button class="button">登记持久任务</button></form><div></div>`;
  get(main,".stack")?.append(planner);
  const plannerForm = get(planner,"form");
  plannerForm.onsubmit = async event => {
    event.preventDefault();
    const submit = get(plannerForm,"button");
    submit.disabled = true;
    try {
      const task = JSON.parse(plannerForm.elements.plan.value);
      const result = await api(task?.prompt ? "/agent/model-tasks" : "/agent/tasks", task);
      if (version === routeVersion) location.hash = runLink(result.run_id);
    } catch (error) { get(planner,"div").innerHTML = errorBox(error); }
    finally { submit.disabled = false; }
  };
  const previousKey = sessionStorage.getItem("epochgate:last-launch");
  if (previousKey) {
    const box = get(main,"#last-launch");
    api(`/observatory/launch/${enc(previousKey)}`).then(previous => {
      if (!box.isConnected || previous.status === "NOT_OBSERVED") return;
      box.hidden = false;
      box.innerHTML = previous.run_id ? link("打开上次提交的运行",runLink(previous.run_id),"soft") :
        empty("上次提交仍待确认，请在运行记录中检查是否已创建。");
    }).catch(error => { if (box.isConnected) { box.hidden=false; box.innerHTML=errorBox(error); } });
  }
  const capture = form => Object.assign(draft, Object.fromEntries(new FormData(form)));
  const next = (label) => `<button class="button" type="submit">${label}</button>`;
  const back = button("上一步", 'data-back', "soft");

  const requirements = async (start = false) => {
    const box = get(main,"#requirements");
    try {
      const status = start ? await api("/requirements/start", {}) : await api("/examples");
      if (!box.isConnected) return;
      sample = status.tasks[0];
      box.innerHTML = `${pill(status.ready ? "AVAILABLE" : "PREFLIGHT_BLOCKED")} <p>${esc(status.message)}</p>${status.missing.length ? `<p class="mono">${esc(status.missing.join(" · "))}</p>` : ""}`;
      const sampleButton = get(target, "#sample");
      if (sampleButton) sampleButton.disabled = !sample;
    } catch (error) { if (box.isConnected) box.innerHTML = errorBox(error); }
  };

  function paint() {
    if (version !== routeVersion) return;
    main.querySelectorAll(".steps li").forEach((item,index) => index === step ? item.setAttribute("aria-current","step") : item.removeAttribute("aria-current"));
    if (step === 0) {
      target.innerHTML = `<form class="stack-small"><h2>选择代码仓库</h2>
        <label>本地仓库路径 <input name="workspace" value="${esc(draft.workspace)}" placeholder="C:\\Project\\example" required></label>
        <div class="actions">${button("使用问候样本", 'id="sample"' + (sample ? "" : " disabled"), "soft")}${button("检查仓库", 'id="inspect"', "outline")}</div>
        <div id="workspace-preview">${preview ? tree(preview) : empty("检查目录后可预览文件树。")}</div>
        <div class="actions">${next("继续定义任务 →")}</div></form>`;
      const form = get(target,"form");
      form.elements.workspace.oninput = () => {
        capture(form);
        preview = undefined;
        get(target,"#workspace-preview").innerHTML = empty("检查目录后可预览文件树。");
      };
      get(form,"#sample").onclick = () => {
        Object.assign(draft,{workspace:sample.workspace, task:sample.task, title:sample.title, test_command:sample.test_command});
        form.elements.workspace.value = draft.workspace;
        preview = {workspace:sample.workspace, files:sample.files.map(file => file.name)};
        get(target,"#workspace-preview").innerHTML = tree(preview) + sample.files.map(file => details(file.name, file.text)).join("");
      };
      const inspect = async (advance) => {
        if (!form.reportValidity()) return;
        capture(form);
        const requested = draft.workspace;
        const box = get(target,"#workspace-preview");
        const controls = form.querySelectorAll("button");
        controls.forEach(control => { control.disabled = true; });
        box.innerHTML = empty("正在检查目录…");
        try {
          const result = await api("/observatory/workspace", {workspace: requested});
          if (!form.isConnected || form.elements.workspace.value !== requested) return;
          preview = result;
          draft.workspace = result.workspace;
          form.elements.workspace.value = result.workspace;
          box.innerHTML = tree(result);
          if (advance) { step = 1; paint(); }
        } catch (error) { if (form.isConnected) box.innerHTML = errorBox(error); }
        finally { controls.forEach(control => { control.disabled = control.id === "sample" && !sample; }); }
      };
      get(form,"#inspect").onclick = () => inspect(false);
      form.onsubmit = event => { event.preventDefault(); inspect(true); };
    } else if (step === 1) {
      target.innerHTML = `<form class="stack-small"><h2>定义执行任务</h2>
        <label>任务名称 <input name="title" maxlength="120" value="${esc(draft.title)}"></label>
        <label>任务描述 <textarea name="task" required>${esc(draft.task)}</textarea></label>
        <details><summary>高级配置</summary><div class="stack-small">
        <label>执行入口 <input value="DSH · DeepSeek Flash" readonly></label>
        <label>容器工作目录 <input value="/workspace" readonly></label>
        <label>测试命令（可选） <input name="test_command" value="${esc(draft.test_command)}" placeholder="python -m unittest"></label>
        <label>Agent 单轮超时（秒） <input type="number" name="timeout" min="1" max="3600" required value="${esc(draft.timeout)}"></label>
        </div></details><div class="actions">${back}${next("继续预检 →")}</div></form>`;
      const form = get(target,"form");
      form.oninput = () => capture(form);
      form.onsubmit = event => { event.preventDefault(); capture(form); step = 2; preflight = undefined; paint(); check(); };
    } else {
      target.innerHTML = `<div class="stack-small"><h2>预检与执行策略</h2><p class="mono">${esc(draft.workspace)}</p>
        <div id="policy">${preflight ? policy(preflight) : empty("正在检查执行条件…")}</div>
        <div class="actions">${back}${button("重新预检", 'id="check"', "soft")}${button("开始运行", 'id="launch"' + (preflight?.ready ? "" : " disabled"))}</div></div>`;
      get(target,"#check").onclick = check;
      get(target,"#launch").onclick = async event => {
        const control = event.currentTarget;
        control.disabled = true;
        toast("正在准备容器和 Agent 会话…");
        try {
          const fingerprint = JSON.stringify(draft);
          if (draftLaunch?.fingerprint !== fingerprint) draftLaunch = {fingerprint,key:crypto.randomUUID()};
          sessionStorage.setItem("epochgate:last-launch",draftLaunch.key);
          const result = await api("/sessions", draft, {launchKey:draftLaunch.key});
          draftLaunch = undefined;
          if (result.job_id) trackSession(result.run_id, result.job_id);
          if (version === routeVersion) location.hash = runLink(result.run_id);
        } catch (error) { toast(error.message,true); control.disabled = false; }
      };
    }
    const previous = get(target,"[data-back]");
    if (previous) previous.onclick = () => { step -= 1; paint(); };
  }

  async function check() {
    const box = get(target,"#policy");
    const checkButton = get(target,"#check");
    const launch = get(target,"#launch");
    checkButton.disabled = true;
    launch.disabled = true;
    box.innerHTML = empty("正在检查执行条件…");
    try {
      const result = await api("/observatory/preflight", draft);
      if (!box.isConnected) return;
      preflight = result;
      box.innerHTML = policy(result);
      launch.disabled = !result.ready;
    } catch (error) { if (box.isConnected) box.innerHTML = errorBox(error); }
    finally { checkButton.disabled = false; }
  }

  function policy(value) {
    return `${fields([...Object.entries(value.policy).map(([key, status]) => [key,pill(status)]),
      ["执行入口",esc(value.runner)],["工作目录",esc(value.working_directory)],["超时",esc(`${value.timeout} 秒`)],
      ["启动预检",pill(value.ready ? "AVAILABLE" : "PREFLIGHT_BLOCKED")]])}<p>${esc(value.message)}</p>`;
  }

  function tree(value) {
    const root = {};
    for (const path of value.files) {
      let node = root;
      for (const part of path.split("/")) {
        if (!Object.hasOwn(node,part)) Object.defineProperty(node,part,{value:{},enumerable:true});
        node = node[part];
      }
    }
    const lines = (node, indent = "") => Object.entries(node).flatMap(([name, contents]) => {
      const folder = Object.keys(contents).length > 0;
      return [indent + name + (folder ? "/" : ""), ...lines(contents, `${indent}  `)];
    });
    return `<details open><summary>文件树 · ${value.files.length}${value.truncated ? "+" : ""}</summary>${value.files.length ? raw(lines(root).join("\n")) : empty("未发现可预览路径。")}</details>`;
  }

  get(main,"#start-ready").onclick = async event => {
    const control = event.currentTarget;
    control.disabled = true;
    try { await requirements(true); if (step === 2 && target.isConnected) await check(); }
    finally { control.disabled = false; }
  };
  paint();
  await requirements();
}

function trackSession(id, jobId) {
  sessionJobs.set(id, {status:"queued", jobId});
  waitJob(jobId).then(job => {
    if (sessionJobs.get(id)?.jobId !== jobId) return;
    sessionJobs.set(id, job);
    toast(job.status === "failed" ? (job.error || "会话任务失败。") : "会话任务已返回。", job.status === "failed");
  }).catch(error => {
    if (sessionJobs.get(id)?.jobId === jobId) sessionJobs.set(id,{status:"NOT_OBSERVED",error:error.message});
    toast(error.message, true);
  });
}

const eventGroups = {
  admission: new Set(["attempt_opened","admission_rejected","eligibility_superseded","eligibility_revoked","commit_rejected","result_withheld","publish_claimed"]),
  execution: new Set(["run_created","attempt_started","command_started","command_exited","agent_turn_finished","demo_finished","cancel_requested","stop_signal_sent","process_exited","process_still_running","slot_release_observed"]),
  effect: new Set(["intent_recorded","effect_committed","file_published","publish_outcome","external_outcome_unknown"]),
  receipt: new Set(["file_published","reconciled_from_sink","effect_committed"]),
  recovery: new Set(["reconciled_from_sink","reconcile_decision","not_applied","outcome_unknown","worker_reconciled","worker_crashed"]),
  semantic: new Set(["semantic_checked","semantic_verified"]),
};
const eventLabel = event => event.label === event.event_type ? ({"slot_release_observed":"观测到执行槽位释放","worker_crashed":"Worker 退出","process_still_running":"进程仍在运行","demo_finished":"演示执行结束","agent_turn_finished":"Agent 执行结束"})[event.event_type] || event.label : event.label;
const eventStatus = event => event.payload.reason ?? event.payload.decision ?? event.payload.status ?? event.payload.effect_state;
const logEvent = event => /command|agent|process|worker|error|failed|cancel/i.test(event.event_type);
const logLevel = event => {
  if (/failed|error/.test(event.event_type) || (typeof event.payload.exit_code === "number" && event.payload.exit_code !== 0)) return "ERROR";
  if (/unknown|still_running|crashed|cancel/.test(event.event_type)) return "WARN";
  return "INFO";
};
const tabNames = {summary:"概览", timeline:"时间线", evidence:"证据", logs:"日志", artifacts:"产物", raw:"原始数据"};

function duration(start, end) {
  if (!start || !end) return unavailable;
  const elapsed = (Date.parse(end) - Date.parse(start)) / 1000;
  return Number.isFinite(elapsed) && elapsed >= 0 ? `${elapsed.toFixed(2)} 秒` : unavailable;
}

async function runDetail(version, id, initialTab, focusKind, focusId) {
  if (id.startsWith("agent-")) {
    const paint = async () => {
      try {
        const view = await api(`/agent/tasks/${enc(id)}`);
        if (version !== routeVersion) return;
        const repairs = view.decisions.filter(item=>item.kind === "repair");
        const latestRepair = repairs.at(-1);
        const decisions = view.decisions.filter(item=>item.kind === "decision");
        const latestDecision = decisions.at(-1);
        const latestBudget = view.decisions.at(-1)?.budget ?? latestDecision?.budget;
        const changed = view.decisions.flatMap(item=>item.changed_resources ?? []);
        main.innerHTML = `<div class="stack">${heading("Agent Intelligence",esc(id),link("运行记录","#/runs","outline"))}
          <div class="grid">${metric("Worker epoch",view.epoch)}${metric("任务状态",view.status)}${metric("目标回执",view.receipts.length)}</div>
          <section class="card stack-small"><h2>Plan Graph</h2>${table(["步骤","依赖","读取","写入"],(latestRepair?.after ?? view.task.plan).map(node=>[
            esc(node.step_id),esc((node.dependencies ?? []).join(", ")),esc((node.read_set ?? []).join(", ")),esc(Object.keys(node.effect ?? {}).join(", "))
          ]))}${fields([
            ["状态变化",esc([...new Set(changed)].join(", ") || "NOT_OBSERVED")],
            ["失效节点",esc(latestRepair?.invalidated_steps?.join(", ") || "NOT_OBSERVED")],
            ["保留节点",esc(latestRepair?.preserved_steps?.join(", ") || "NOT_OBSERVED")],
            ["最近决策",esc(latestDecision?.action)],["决策依据",esc(latestDecision?.reason_code)]
          ])}${latestRepair ? details("修复前后计划",{before:latestRepair.before,after:latestRepair.after}) : ""}
          ${latestBudget ? details("最近记录的预算",latestBudget) : ""}</section>
          <section class="card stack-small"><h2>Agent Decision Trace</h2>${table(["序号","步骤","决策或结果","依据"],view.decisions.map(item=>[
            esc(item.sequence),esc(item.step),esc(item.action ?? item.result ?? item.kind),details(item.reason_code ?? "证据",item)
          ]))}${details("Effect Evidence",view.receipts)}</section></div>`;
      } catch (error) { if (version === routeVersion) main.innerHTML = errorBox(error); }
    };
    await paint();
    if (version === routeVersion) refreshTimer = setInterval(() => { if (!main.querySelector("details[open]")) paint(); },3000);
    return;
  }
  const path = `/runs/${enc(id)}`;
  let view = await api(`${path}?figures=false`);
  if (version !== routeVersion) return;
  let active = Object.hasOwn(tabNames, initialTab) ? initialTab : "summary";
  let logText = "";
  let logError = "";
  let logOutputId = "";
  let session;
  let artifactError;
  let artifactPending = false;
  let search = "";
  let level = "";
  let autoscroll = false;
  let eventFilter = "";
  let signature = "";
  const isSession = view.summary.scenario === "workspace";
  main.innerHTML = `<div class="stack">
    <div class="actions"><a href="#/runs" class="link">← 运行记录</a><span class="mono muted">${esc(id)}</span></div>
    <div id="run-heading"></div><div id="run-metrics" class="grid"></div>
    <nav class="tabs" role="tablist" aria-label="运行详情">${Object.entries(tabNames).map(([key,label]) => `<button type="button" id="tab-${key}" role="tab" aria-controls="run-panel" data-tab="${key}">${label}</button>`).join("")}</nav>
    <div id="run-panel" role="tabpanel" tabindex="0" class="stack"></div>
  </div>`;
  const panel = get(main,"#run-panel");

  function header() {
    const {summary, phase} = view;
    const job = sessionJobs.get(id);
    const elapsed = typeof summary.execution_seconds === "number" ? `执行耗时 ${summary.execution_seconds.toFixed(2)} 秒` : `账本观测跨度 ${duration(summary.created_at,summary.last_event_at)}`;
    get(main,"#run-heading").innerHTML = heading(esc(summary.task_title || `Run · ${id}`),
      `${esc(scenarioName(summary.scenario))} · ${esc(time(summary.created_at))} · ${esc(elapsed)}`,
      `<span class="muted">账本 ${pill(phase)}</span><span class="muted">执行 ${pill(job?.status ?? summary.execution_status ?? "NOT_OBSERVED")}</span>${button("刷新", 'id="refresh-run"', "soft")}`);
    get(main,"#refresh-run").onclick = () => refresh(true);
    get(main,"#run-metrics").innerHTML = [
      ["Attempt",view.attempts.length,"账本中的 Attempt"],["Effect",summary.effect_count,"独立幂等键记录"],
      ["回执核验",summary.receipt_status,`${summary.confirmed} 条回执获证`],
      ["观测到的重复发布",summary.duplicate_publications ?? "NOT_OBSERVED","按已验证的文件发布事件去重"],
    ].map(item => metric(...item)).join("");
  }

  const rowsFor = (group) => view.events.filter(event => eventGroups[group].has(event.event_type));
  const eventButton = (event) => `<button class="button soft" type="button" data-event="${event.seq}">#${event.seq} ${esc(eventLabel(event))}</button>`;
  const eventLinks = (events) => events.length ? `<div class="actions">${events.map(eventButton).join("")}</div>` : pill("NOT_OBSERVED");

  function summary() {
    const receipts = view.receipts;
    const admissions = rowsFor("admission");
    const recovery = rowsFor("recovery");
    const cancel = view.events.filter(event => ["cancel_requested","stop_signal_sent","process_exited","process_still_running","slot_release_observed"].includes(event.event_type));
    const changed = view.events.filter(event => event.payload.before !== undefined && event.payload.after !== undefined && event.payload.receipt_verified === true);
    return `<div class="grid">
      <section class="card c6 stack-small"><h2>Admission · 执行资格</h2>
        ${fields([["当前资格",pill(view.eligible ? "ELIGIBLE" : "REVOKED")],["Fencing Token",esc(view.fencing_token)],["状态版本",esc(view.state_version)],["拒绝提交",esc(view.summary.rejected_submissions)]])}
        ${admissions.length ? eventLinks(admissions.slice(-5)) : pill("NOT_OBSERVED")}
      </section>
      <section class="card c6 stack-small"><h2>Effect Receipt · 目标核验</h2>${receipts.length ? table(["回执","来源","状态"],receipts.map(item => [
        `<button type="button" class="button soft mono" data-receipt="${esc(item.idempotency_key)}">${esc(item.receipt_id)}</button>`,
        esc(item.source),pill(item.status)])) : empty("NOT_OBSERVED · 当前未采集到回执。")}</section>
      <section class="card c6 stack-small"><h2>Recovery · 恢复决定</h2>
        ${fields([["UNKNOWN 效果",esc(view.summary.unknown_effects)],["故障检查点",esc(recovery.find(event => event.payload.checkpoint)?.payload.checkpoint)]])}
        ${eventLinks(recovery)}</section>
      <section class="card c6 stack-small"><h2>执行槽位与取消</h2>
        ${fields([["槽位已释放",esc(view.slot_released)],["进程退出后释放",esc(view.observations.slot.released_after_exit)],["GPU / KV",pill("NOT_IMPLEMENTED")]])}
        ${cancel.length ? table(["观测","时间"],cancel.map(event => [eventButton(event),esc(time(event.recorded_at))])) : pill("NOT_OBSERVED")}
      </section></div>
      <section class="card stack-small"><h2>目标状态前后对比</h2>${changed.length ? changed.map(event => `<div class="grid"><div class="c6"><h3>Before</h3>${raw(event.payload.before)}</div><div class="c6"><h3>After</h3>${raw(event.payload.after)}</div></div>`).join("") : empty("NOT_OBSERVED · 本次运行没有带提交时前后状态的目标端记录。")}</section>
      ${isSession ? controls() : ""}`;
  }

  function controls() {
    return `<section class="card stack-small"><h2>会话操作</h2>${sessionJobs.get(id)?.error ? errorBox(sessionJobs.get(id).error) : ""}
      <div class="actions">${button("撤销并停止", 'data-session="cancel"', "danger")}${button("对账", 'data-session="reconcile"', "soft")}${button("恢复执行", 'data-session="resume"', "outline")}</div>
      <div class="grid">
        <form class="c6 stack-small" data-session-form="patch"><h3>发布文件</h3><label>相对路径 <input name="path" required></label><label>文件内容 <textarea name="content"></textarea></label><button class="button soft">发布受控变更</button></form>
        <form class="c6 stack-small" data-session-form="commands"><h3>执行容器命令</h3><label>命令 <input name="command" required></label><button class="button soft">启动命令</button></form>
      </div></section>`;
  }

  function timeline() {
    const filters = [["","全部"],["admission","资格"],["execution","执行"],["effect","效果"],["receipt","回执"],["recovery","恢复"]];
    const events = eventFilter ? rowsFor(eventFilter) : view.events;
    return `<section class="card stack-small"><div class="heading"><h2>Execution Timeline</h2><label>事件类别 <select id="event-filter">${filters.map(([key,label]) => `<option value="${key}" ${key === eventFilter ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
      ${events.length ? `<ol class="timeline">${events.map(event => `<li><div class="heading"><div>${eventButton(event)} ${eventStatus(event) ? pill(eventStatus(event)) : ""}</div>
       <time datetime="${esc(event.recorded_at)}">${esc(time(event.recorded_at))}</time></div>
       <small class="muted mono">${esc(event.event_type)}${event.attempt_id ? ` · ${esc(event.attempt_id)}` : ""}</small></li>`).join("")}</ol>` : empty("当前分类没有账本事件。")}</section>`;
  }

  function evidenceTable() {
    const categories = [["admission","Admission · 执行资格"],["execution","Execution · 执行观测"],["effect","Effect · 效果"],["receipt","Receipt · 回执"],["recovery","Recovery · 恢复"],["semantic","Semantic · 业务约束"]];
    return `<div class="grid"><section class="card c6 stack-small"><h2>Attempts</h2>
      ${table(["Attempt ID","Fencing Token","阶段"],view.attempts.map(item => [
        `<button class="button soft mono" type="button" data-attempt="${esc(item.attempt_id)}">${esc(item.attempt_id)}</button>`,esc(item.fencing_token),pill(item.phase)]))}
      </section><section class="card c6 stack-small"><h2>Effects</h2>
      ${table(["Effect / 幂等键","效果状态","回执"],view.effects.map(item => [
        `<button class="button soft mono" type="button" data-effect="${esc(item.idempotency_key)}">${esc(item.effect_id || item.idempotency_key)}</button>`,
        pill(item.effect_state),item.receipt ? `<button class="button soft" type="button" data-receipt="${esc(item.idempotency_key)}">查看</button>` : pill("NOT_OBSERVED")]))}
      </section></div>
      <section class="card stack-small"><h2>证据审计</h2>${table(["类别","来源","可查事件"],categories.map(([key,title]) => [title,"RunLedger",eventLinks(rowsFor(key))]))}</section>`;
  }

  function logs() {
    return `<section class="card stack-small"><div class="heading"><h2>日志与执行输出</h2>${button("复制当前输出", 'id="copy-log"', "soft")}</div>
      <div class="filters">
      <label>搜索 <input type="search" id="log-search" value="${esc(search)}"></label>
      <label>账本事件级别 <select id="log-level" ${logOutputId ? "disabled" : ""}>${["","INFO","WARN","ERROR"].map(item => `<option ${item === level ? "selected" : ""} value="${item}">${item || "全部"}</option>`).join("")}</select></label>
      <label>读取效果输出 <select id="log-output"></select></label>
      <label class="inline"><input type="checkbox" id="autoscroll" ${autoscroll ? "checked" : ""}>自动滚动</label></div>
      <div id="log-lines"></div></section>`;
  }

  function paintLogs() {
    const outputs = get(panel,"#log-output");
    const files = [...new Set(view.effects.map(effect => effect.log_id).filter(Boolean))];
    const values = ["",...files];
    if (values.length !== outputs.options.length || values.some((value,index) => value !== outputs.options[index]?.value)) {
      outputs.replaceChildren(new Option("账本事件", ""), ...files.map(id => new Option(id,id)));
      outputs.value = logOutputId;
    }
    const lines = logOutputId ? logText.split("\n") : view.events.filter(logEvent).map(event =>
      `${time(event.recorded_at)} [${logLevel(event)}] ${event.event_type} ${JSON.stringify(event.payload)}`);
    const filtered = lines.filter(line => line.toLowerCase().includes(search.toLowerCase()) && (logOutputId || !level || line.includes(`[${level}]`)));
    const box = get(panel,"#log-lines");
    box.innerHTML = logError ? errorBox(logError) : filtered.join("").length ? raw(filtered.join("\n")) : empty("暂无匹配输出。");
    const output = get(box,"pre");
    if (autoscroll && output) output.scrollTop = output.scrollHeight;
  }

  function artifacts() {
    return `<section class="card stack-small"><h2>运行产物</h2>
      <div class="actions">${button("导出效果图 SVG", 'id="export-figure"', "soft")}${button("导出证据表 HTML", 'id="export-table"', "soft")}</div>
      ${isSession ? `<h3>容器文件变更</h3>${artifactError ? errorBox(artifactError) : session ? raw(session.diff) + details("未导入路径",session.skipped) : empty("正在读取可用的容器文件差异…")}` : `<p>运行生成的效果图与事件表来自 RunLedger。</p>`}
      ${view.effects.some(effect => effect.log_id) ? `<p>测试与命令输出可在「日志」按 Effect ID 读取。</p>` : ""}</section>`;
  }

  async function loadArtifacts() {
    if (!isSession || session || artifactPending || artifactError) return;
    artifactPending = true;
    try { session = await api(`/sessions/${enc(id)}`); }
    catch (error) { artifactError = error; }
    finally { artifactPending = false; }
    if (panel.isConnected && active === "artifacts") paint();
  }

  function paint() {
    panel.setAttribute("aria-labelledby",`tab-${active}`);
    main.querySelectorAll("[data-tab]").forEach(control => {
      const selected = control.dataset.tab === active;
      control.setAttribute("aria-selected", String(selected));
      control.tabIndex = selected ? 0 : -1;
    });
    const rawView = () => `<section class="card stack-small"><div class="heading"><h2>运行快照 JSON</h2>${button("下载 JSON", 'id="export-json"', "soft")}</div>${raw(view)}</section>`;
    panel.innerHTML = ({summary,timeline,evidence:evidenceTable,logs,artifacts,raw:rawView})[active]();
    if (active === "artifacts") loadArtifacts();
    if (active === "logs") paintLogs();
  }

  function selection(kind, key) {
    if (kind === "event") {
      const event = view.events.find(item => String(item.seq) === key);
      if (!event) return;
      const effect = view.effects.find(item => item.idempotency_key === event.payload.idempotency_key);
      return modal(`#${event.seq} · ${eventLabel(event)}`, `${fields([
        ["来源","RunLedger"],["时间",esc(time(event.recorded_at))],["Attempt",event.attempt_id ? button(esc(event.attempt_id),`data-attempt="${esc(event.attempt_id)}"`,"soft") : esc(unavailable)],
        ["原因 / 决策",esc(eventStatus(event))],["关联事件",event.causation_id ? button(`#${event.causation_id}`,`data-event="${event.causation_id}"`,"soft") : esc(unavailable)],
        ["效果",effect ? button(esc(effect.effect_id || effect.idempotency_key),`data-effect="${esc(effect.idempotency_key)}"`,"soft") : esc(unavailable)],
      ])}${details("原始事件 JSON",event)}`);
    }
    if (kind === "attempt") {
      const attempt = view.attempts.find(item => item.attempt_id === key);
      const events = view.events.filter(event => event.attempt_id === key || (event.payload.fencing_token === attempt?.fencing_token && !event.attempt_id));
      const effects = view.effects.filter(effect => effect.attempt_id === key);
      return modal(`Attempt · ${key}`, `${attempt ? fields([["Fencing Token",esc(attempt.fencing_token)],["阶段",pill(attempt.phase)],["会话",esc(attempt.session_id)]]) : pill(unavailable)}
        <p>关联效果</p><div class="actions">${effects.length ? effects.map(effect => button(esc(effect.effect_id || effect.idempotency_key),`data-effect="${esc(effect.idempotency_key)}"`,"soft")).join("") : pill("NOT_OBSERVED")}</div>
        <p>关联事件</p>${eventLinks(events)}${attempt ? details("原始 Attempt JSON",attempt) : ""}`);
    }
    if (kind === "effect") {
      const effect = view.effects.find(item => item.idempotency_key === key);
      if (!effect) return;
      return modal(`Effect · ${effect.effect_id || key}`, `${fields([
        ["幂等键",esc(key)],["Payload Hash",esc(effect.payload_hash)],["Arguments Hash",esc(effect.arguments_hash)],["Fencing Token",esc(effect.fencing_token)],
        ["提交结果",pill(effect.effect_state)],["Attempt",effect.attempt_id ? button(esc(effect.attempt_id),`data-attempt="${esc(effect.attempt_id)}"`,"soft") : esc(unavailable)], ["Attempt 关联来源",esc(effect.attempt_source)],
        ["目标记录",effect.receipt ? button("核验回执",`data-receipt="${esc(key)}"`,"soft") : esc(unavailable)],
      ])}<p>关联事件</p>${eventLinks(view.events.filter(event => event.payload.idempotency_key === key))}${details("原始 Effect JSON",effect)}`);
    }
    const receipt = view.receipts.find(item => item.idempotency_key === key);
    if (!receipt) return;
    modal(`Receipt · ${receipt.receipt_id}`, `${fields([
      ["核对结果",pill(receipt.status)],["证据来源",esc(receipt.source)],["核对时间",esc(time(receipt.verified_at))],["不匹配原因",esc(receipt.reason)],
      ["关联效果",button(esc(key),`data-effect="${esc(key)}"`,"soft")],
    ])}${eventLinks(view.events.filter(event => receipt.event_seqs.includes(event.seq)))}${details("目标记录",receipt.target_record ?? unavailable)}${details("原始回执 JSON",receipt)}`);
  }

  function activate(tab) {
    active = tab;
    history.replaceState(null,"",`${runLink(id)}/${tab}`);
    paint();
  }

  function snap() {
    return JSON.stringify([view.phase,view.eligible,view.state_version,view.slot_released,view.events.at(-1)?.seq,view.effects,view.receipts,sessionJobs.get(id)?.status]);
  }

  let refreshing = false;
  async function refresh(manual = false) {
    if (refreshing || version !== routeVersion) return;
    refreshing = true;
    try {
      const next = await api(`${path}?figures=false`);
      if (version !== routeVersion) return;
      view = next;
      const latest = snap();
      if (manual || latest !== signature) {
        signature = latest;
        header();
        if (active === "logs") paintLogs();
        else if (manual || active === "timeline" || active === "evidence" || active === "raw" || (active === "summary" && !panel.contains(document.activeElement))) paint();
      }
    } catch (error) { if (manual && panel.isConnected) toast(error.message,true); }
    finally { refreshing = false; }
  }

  function wire(event) {
    const control = event.target.closest("button");
    if (!control || !panel.isConnected) return;
    for (const kind of ["event","attempt","effect","receipt"]) {
      if (Object.hasOwn(control.dataset,kind)) { selection(kind, control.dataset[kind]); return; }
    }
  }
  panel.addEventListener("click",wire);
  dialog.addEventListener("click",wire);
  panel.addEventListener("click", async event => {
    const control = event.target.closest("button");
    if (!control) return;
    if (control.id === "export-json") download(`${id}.json`,JSON.stringify(view,null,2));
    if (control.id === "export-figure" || control.id === "export-table") {
      control.disabled = true;
      try {
        const report = await api(path);
        if (control.id === "export-figure") download(`${id}-effect.svg`,report.effect_svg,"image/svg+xml");
        else download(`${id}-table.html`,report.table_html,"text/html");
      } catch (error) { toast(error.message,true); }
      finally { control.disabled = false; }
    }
    if (control.id === "copy-log") {
      try { await navigator.clipboard.writeText(get(panel,"#log-lines pre")?.textContent || ""); toast("已复制输出"); }
      catch (error) { toast(error.message,true); }
    }
    const action = control.dataset.session;
    if (action) {
      if (action === "cancel" && !await confirmAction("撤销并停止", "撤销本会话的执行资格并请求停止进程。已经发生的效果仍保留。")) return;
      await sessionAction(action,{},control);
    }
  });
  panel.addEventListener("input", async event => {
    const control = event.target;
    if (control.id === "event-filter") { eventFilter = control.value; paint(); get(panel,"#event-filter").focus(); }
    if (control.id === "log-search") { search = control.value; paintLogs(); }
    if (control.id === "log-level") { level = control.value; paintLogs(); }
    if (control.id === "autoscroll") { autoscroll = control.checked; paintLogs(); }
    if (control.id === "log-output") {
      const selected = control.value;
      logOutputId = selected;
      get(panel,"#log-level").disabled = Boolean(selected);
      logText = "";
      logError = "";
      paintLogs();
      if (selected) {
        try { const result = await api(`/logs/${enc(selected)}`); if (selected === logOutputId) logText = result.text; }
        catch (error) { if (selected === logOutputId) logError = error.message; }
      }
      if (active === "logs" && panel.isConnected) paintLogs();
    }
  });
  panel.addEventListener("submit", event => {
    const form = event.target;
    if (!form.dataset.sessionForm) return;
    event.preventDefault();
    sessionAction(form.dataset.sessionForm,Object.fromEntries(new FormData(form)),get(form,"button"));
  });

  async function sessionAction(action, body, control) {
    control.disabled = true;
    try {
      const result = await api(`/sessions/${enc(id)}/${action}`,body);
      if (result.job_id) trackSession(id,result.job_id);
      if (result.resumed === false) throw new Error("当前会话无法恢复，请核对资格与效果状态。");
      toast(`控制面已返回 · ${result.status || action}`);
      session = undefined;
      artifactError = undefined;
      await refresh(true);
    } catch (error) { toast(error.message,true); }
    finally { control.disabled = false; }
  }

  const tabs = get(main,"[role=tablist]");
  tabs.addEventListener("click", event => { if (event.target.dataset.tab) activate(event.target.dataset.tab); });
  tabs.addEventListener("keydown", event => {
    const keys = Object.keys(tabNames);
    const index = keys.indexOf(active);
    const position = {"ArrowLeft":(index+keys.length-1)%keys.length,"ArrowRight":(index+1)%keys.length,"Home":0,"End":keys.length-1}[event.key];
    if (position === undefined) return;
    event.preventDefault();
    activate(keys[position]);
    get(tabs,`#tab-${active}`).focus();
  });
  header();
  paint();
  if (focusKind === "attempt" && focusId) selection("attempt",focusId);
  if (focusKind === "effect" && focusId) {
    const item = view.effects.find(effect=>effect.effect_id === focusId || effect.idempotency_key === focusId);
    if (item) selection("effect",item.idempotency_key);
  }
  if (focusKind === "receipt" && focusId) {
    const item = view.receipts.find(receipt=>receipt.receipt_id === focusId);
    if (item) selection("receipt",item.idempotency_key);
  }
  signature = snap();
  refreshTimer = setInterval(refresh,2500);
  routeCleanup = () => dialog.removeEventListener("click",wire);
}


const armLabels = {sqlite:"SQLite",sqlite_fenced:"SQLite 版本围栏",file:"文件发布"};
const schedulerLabels = {before_check:"检查前更新",check_commit_gap:"检查与提交间更新",after_commit:"提交后更新"};

async function researchPage(version, cellId) {
  const data = await api("/observatory/research");
  if (version !== routeVersion) return;
  const {report, integrity, sha256} = data;
  const detected = report.rows.filter(row => row.historical_flags);
  const archiveMatch = !integrity.missing.length && !integrity.mismatched.length;
  const strategies = ["ordinary","paired","strong_existing"];
  const rows = Object.entries(report.equal_budget_detection);
  const peak = Math.max(1,...rows.flatMap(([,values]) => strategies.map(key => values[key])));
  const digest = Object.entries(sha256).filter(([name]) => name === "report.json")[0]?.[1];
  const artifact = (name,label) => button(esc(label),`data-research-file="${esc(name)}"`,"soft");
  main.innerHTML = `<div class="stack">
    ${heading("Research Lab", "Effect History Evaluation · 协议、对照与原始证据。", `${link("Evidence Audit","#/research/audit","outline")}${artifact("protocol","下载冻结协议")}`)}
    <section class="card stack-small"><h2>研究问题与适用范围</h2>
      ${fields([["批次","pilot-01"],["Research Question","终态通过时，提交时业务违规能否被掩盖？"],
      ["Hypothesis","提交时业务核对能揭示漏检。调度增量需在等预算下超过已有强对照。"],
      ["Dataset",`${report.cells} 格 · SQLite、文件发布 · 三个执行臂`],
      ["Metrics","终态匹配、授权/去重/回执检出、提交时业务违规、各臂等预算检出"],
      ["Limitations","固定夹具和确定性顺序。总体失败率、掉电耐久、网络故障和模型泛化尚未观测。"]])}
    </section>
    <div class="grid">${[
      ["实验格",report.cells,"归档报告"],["最终产物正确",`${report.cells-report.final_flags}/${report.cells}`,"最终产物匹配"],
      ["提交时业务违规",report.hidden_counterexamples,Object.keys(armLabels).map(arm => [arm,detected.filter(row=>row.arm===arm).length]).filter(([,count])=>count).map(([arm,count])=>`${armLabels[arm]} ${count}`).join(" · ")],
      ["基线检出",`${detected.filter(row=>row.baseline_flags).length}/${detected.length}`,"授权 · 去重 · 回执检查"],
    ].map(item => metric(...item)).join("")}</div>
    <div class="grid">
      <section class="card c6 stack-small"><h2>各边界的调度检出</h2>
        <div class="chart-row"><span>执行边界</span><span>普通</span><span>配对</span><span>强对照</span></div>
        ${rows.map(([arm,values]) => `<div class="chart-row"><strong>${esc(armLabels[arm] || arm)}</strong>${strategies.map(key => `<div class="bar"><i style="width:${100*values[key]/peak}%"></i><span>${esc(values[key])}</span></div>`).join("")}</div>`).join("")}
        <p>数值表示每个边界内检出的业务输入数。强对照在预检后、提交前更新业务状态。本批次配对调度与强对照打平。</p>
        <p>实验范围为归档中的确定性夹具与调度顺序。</p>
      </section>
      <section class="card c6 stack-small" id="research-integrity"><h2>Evidence Integrity</h2>
        ${fields([["原始记录",esc(`${integrity.files-integrity.missing.length}/${integrity.files}`)],["归档 SHA-256",pill(archiveMatch ? "MATCH" : "MISMATCH")],
          ["协议摘要",pill(data.protocol_matches ? "MATCH" : "MISMATCH")],["逐格证据错误",esc(report.errors.length)],["归档 Git Commit",`<span class="mono">${esc(data.archive_commit)}</span>`],
          ["报告 SHA-256",`<span class="mono">${esc(digest)}</span>`]])}
        ${integrity.missing.length || integrity.mismatched.length ? details("缺失或不匹配的文件",integrity) : ""}
        <div class="actions">${button("从目标账本独立重算",'id="audit-research"')}${artifact("sha256.json","校验清单")}${artifact("report.json","原始报告")}${artifact("manifest.json","实验清单")}</div>
        <div id="audit-result"></div><div id="audit-history"></div>
        <div class="actions">${["json","csv","md","zip"].map(kind => button(`导出 ${kind.toUpperCase()}`,`data-product-export="/observatory/research/export/${kind}" data-filename="effect-history.${kind}"`,"soft")).join("")}</div>
      </section></div>
    <section class="card stack-small"><div class="heading"><h2>实验格与目标证据</h2><label>执行边界 <select id="arm-filter"><option value="">全部</option>${Object.entries(armLabels).map(([key,label]) => `<option value="${key}">${label}</option>`).join("")}</select></label></div>
    <div class="filters"><label>实验格 ID <input type="search" id="cell-search"></label>
    <label>调度策略 <select id="strategy-filter"><option value="">全部</option>${strategies.map((key,i)=>`<option value="${key}">${["普通","配对","强对照"][i]}</option>`).join("")}</select></label>
    <label>结果 <select id="cell-result"><option value="">全部</option><option value="violation">发现</option><option value="match">未发现</option><option value="unknown">不可判定</option></select></label></div>
    <div id="cell-table"></div></section>
    <section class="card"><h2>冻结协议</h2><p class="mono">${esc(data.protocol_sha256)}</p><div id="protocol-content">${empty("正在读取冻结协议…")}</div></section></div>`;
  const portfolios = {ordinary:["before_check","after_commit"],paired:["check_commit_gap","after_commit"],strong_existing:["before_check","check_commit_gap"]};
  const failed = new Set(report.errors.map(item => item.case));
  const invalidFiles = new Set([...integrity.missing,...integrity.mismatched]);
  let latestAudit;
  const cells = data.manifest.cases.map(item => report.rows.find(row => row.id === item.id) ?? {...item, historical_flags:null});
  function paintCells() {
    const arm = get(main,"#arm-filter").value;
    const strategy = get(main,"#strategy-filter").value;
    const result = get(main,"#cell-result").value;
    const query = get(main,"#cell-search").value.trim().toLowerCase();
    const status = row => failed.has(row.id) || row.historical_flags === null ? "unknown" : row.historical_flags ? "violation" : "match";
    const integrityFor = (row,name) => !sha256[row.id+"/"+name] ? "NOT_AVAILABLE" : invalidFiles.has(row.id+"/"+name) ? "MISMATCH" : "MATCH";
    get(main,"#cell-table").innerHTML = table(["实验格","执行边界","夹具","时序","最终产物","提交时核验","目标账本","时序哈希","回执与重算"],cells.filter(row =>
      (!arm || arm === row.arm) && (!strategy || portfolios[strategy].includes(row.schedule)) &&
      (!result || status(row) === result) && row.id.toLowerCase().includes(query)).map(row => [
      button(esc(row.id),`data-cell="${esc(row.id)}"`,"soft"),esc(armLabels[row.arm]),esc(row.fixture),esc(schedulerLabels[row.schedule]),
      pill(row.final_match == null ? "NOT_AVAILABLE" : row.final_match ? "MATCH" : "MISMATCH"),pill(status(row)),
      pill(integrityFor(row,"target.sqlite")),pill(integrityFor(row,"trace.json")),
      pill(latestAudit?.status === "VERIFIED" && archiveMatch && status(row) !== "unknown" && row.baseline_flags === false ? "VERIFIED" : "NOT_OBSERVED"),
    ]));
    tableColumns(get(main,"#cell-table"),"research");
  }
  for (const selector of ["#arm-filter","#strategy-filter","#cell-result","#cell-search"]) get(main,selector).oninput = paintCells;
  paintCells();
  const historyBox = get(main,"#audit-history");
  async function refreshAudits() {
    try {
      const {audits} = await api("/observatory/research/audits");
      if (version !== routeVersion) return;
      latestAudit = audits[0];
      historyBox.innerHTML = audits.length ? details("复核记录",audits) : empty("尚无本机复核记录。");
      if (get(main,"#cell-table")) paintCells();
    } catch (error) { historyBox.innerHTML = errorBox(error); }
  }
  refreshAudits();
  main.onclick = event => {
    const control = event.target.closest("[data-cell]");
    if (control) openCell(control.dataset.cell);
  };
  get(main,"#audit-research").onclick = async event => {
    const control = event.currentTarget;
    const box = get(main,"#audit-result");
    control.disabled = true;
    box.innerHTML = empty("正在校验归档并从目标账本重算…");
    try {
      const result = await api("/observatory/research/audit", {});
      box.innerHTML = `${pill(result.status)} ${result.result ? details("重算报告摘要",result.result) : errorBox(result.error || "重算失败")}`;
      await refreshAudits();
    } catch (error) { box.innerHTML = errorBox(error); }
    finally { control.disabled = false; }
  };
  async function openCell(id) {
    const box = modal(id,empty("正在读取目标账本与时序…"));
    const ticket = Symbol();
    box.researchTicket = ticket;
    try {
      const cell = await api(`/observatory/research/cells/${enc(id)}`);
      if (version !== routeVersion || box.researchTicket !== ticket || !dialog.open) return;
      box.innerHTML = `<div class="stack-small">${fields([["执行边界",esc(armLabels[cell.row.arm])],["协议 SHA-256",esc(data.protocol_sha256)],
        ["最终产物",pill(cell.row.final_match ? "MATCH" : "MISMATCH")],["提交时违规",esc(cell.row.invalid_effects.join(" · ") || "无")],
        ["旧动作拒绝原因",esc(cell.row.old_rejection)]])}
        ${fields([["初始业务值",esc(cell.row.worlds["1"])],["最终应写值",esc(cell.row.worlds["2"])],
          ["实际最终产物",esc(cell.final_artifact?.bytes?.text)],["产物来源",esc(cell.final_artifact?.source)]])}
        <h3>目标事件与回执</h3>${table(["顺序","事件","提交时应写值","目标端证据"],cell.timeline.map(item => [
          esc(item.sequence),esc(`${item.event} ${item.key ?? ""}`),esc(item.expected),item.target_record ?
          `<details ${item.violation ? "open" : ""}><summary>${item.violation ? "违规原始记录" : "目标记录"}</summary>${raw({table:item.target_table,row:item.target_record,receipt:item.receipt})}</details>` : pill("NOT_OBSERVED")
        ]))}
        ${button("下载逐格证据 ZIP",`data-product-export="/observatory/research/export/zip?cell_id=${enc(id)}" data-filename="${esc(id)}.zip"`,"soft")}
        ${details("冻结逐格结果",cell.row)}${details("目标端账本",cell.ledger)}${details("时序与接收器响应",cell.trace)}
        ${table(["归档文件","SHA-256"],Object.entries(cell.artifacts).map(([name,hash]) => [artifact(name,name.split("/").at(-1)),`<span class="mono">${esc(hash)}</span>`]))}</div>`;
    } catch (error) { if (box.researchTicket === ticket) box.innerHTML = errorBox(error); }
  }
  const protocolBox = get(main,"#protocol-content");
  if (cellId === "audit") {
    const auditPanel = get(main,"#research-integrity");
    const auditPage = document.createElement("div");
    auditPage.className = "stack";
    auditPage.innerHTML = heading("Evidence Audit","冻结协议、归档完整性与本地独立复核。",link("返回实验分析","#/research","outline"));
    auditPage.append(auditPanel);
    main.replaceChildren(auditPage);
    get(auditPanel,"#audit-research").focus();
  } else if (cellId) openCell(cellId);
  api("/observatory/research/artifacts/protocol",undefined,{blob:true}).then(value => value.text()).then(value => {
    protocolBox.innerHTML = details("展开协议原文",value);
  }).catch(error => { protocolBox.innerHTML = errorBox(error); });
}

async function systemPage(version) {
  const data = await api("/observatory/system");
  if (version !== routeVersion) return;
  const components = [
    ["Runtime","AVAILABLE",{版本:data.runtime,Python:data.python,启动时间:time(data.started_at),运行时长:`${Math.floor(data.uptime_seconds)} s`}],
    ["Control Plane",data.control_plane,{authority:"run_ledger",最近检查:time(data.checked_at)}],
    ["SQLite",data.sqlite.status,{引擎:data.sqlite.version,模式:data.sqlite.schema,事务日志:data.sqlite.journal,当前事务:data.sqlite.transaction == null ? unavailable : String(data.sqlite.transaction),耗时:`${data.sqlite.check_ms} ms`,最近成功:time(data.sqlite.last_success),最近故障:time(data.sqlite.last_error)}],
    ["Worker",data.worker.status,{等待中:data.worker.jobs.queued,活跃任务:data.worker.jobs.running,跟踪中的进程:data.worker.attached_runs,活跃Attempt:data.worker.active_attempts,当前租约:data.worker.held_leases,近期任务:data.worker.recent}],
    ["Docker",data.docker.status,{引擎探测:data.docker.status,耗时:`${data.docker.check_ms} ms`}],
    ["DeepSeek",data.model.status,{连接测试:data.model.connectivity}],
    ["Storage","AVAILABLE",{RunLedger:[data.storage.ledger],效果接收器:[data.storage.sink]}],
  ];
  main.innerHTML = `<div class="stack">${heading("系统状态", `最近健康检查 ${esc(time(data.checked_at))}`, button("重新检查",'id="refresh-system"',"soft"))}
    <section class="card stack-small"><h2>组件状态</h2>${table(["组件","状态","观测点"],components.map(([name,status,items],index) => [
      button(esc(name),`data-component="${index}"`,"soft"),pill(status),esc(Object.keys(items).join(" · ")),
    ]))}</section>
    <div class="grid"><section class="card c6 stack-small"><h2>能力边界</h2>${fields([
      ["外部 HTTP exactly-once",pill("NOT_IMPLEMENTED")],["跨数据库原子性",pill("NOT_IMPLEMENTED")],["GPU / KV 回收观测",pill("NOT_IMPLEMENTED")],
      ["效果核验","读取接收端证据，按具体发布边界判断"],
    ])}</section>
    <section class="card c6 stack-small"><h2>控制面访问日志</h2><div id="system-log">${empty("正在读取近期日志…")}</div></section></div></div>`;
  get(main,"#refresh-system").onclick = renderRoute;
  main.querySelectorAll("[data-component]").forEach(control => control.onclick = () => {
    const [name,status,values] = components[Number(control.dataset.component)];
    modal(name,`${pill(status)}${fields(Object.entries(values).map(([key,value]) => [key,Array.isArray(value) ? raw(value) : esc(value || (value === 0 ? 0 : null))]))}`);
  });
  const box = get(main,"#system-log");
  try {
    const {text} = await api("/observatory/system/log");
    box.innerHTML = text ? raw(text) : empty("暂无访问日志。");
  } catch (error) { box.innerHTML = errorBox(error); }
}

document.addEventListener("click", async event => {
  const control = event.target.closest("[data-demo],[data-research-file]");
  if (!control) return;
  if (control.dataset.demo) { await startDemo(control.dataset.demo,control); return; }
  const name = control.dataset.researchFile;
  control.disabled = true;
  try {
    const file = await api(`/observatory/research/artifacts/${name.split("/").map(enc).join("/")}`,undefined,{blob:true});
    download(name === "protocol" ? "PROTOCOL.md" : name.split("/").at(-1),file);
  } catch (error) { toast(error.message,true); }
  finally { control.disabled = false; }
});

let routeCleanup;
async function renderRoute() {
  if (location.hash === "#main") { main.focus(); return; }
  const version = ++routeVersion;
  clearInterval(refreshTimer);
  routeCleanup?.();
  routeCleanup = undefined;
  main.onclick = null;
  if (dialog.open) dialog.close();
  const [,routeName,id,tab,kind,focusId] = location.hash.split("/");
  const page = Object.hasOwn(labels,routeName) ? routeName : "overview";
  document.querySelectorAll("[data-nav]").forEach(item => {
    if (item.dataset.nav === page) item.setAttribute("aria-current","page");
    else item.removeAttribute("aria-current");
  });
  document.querySelector("#crumb").textContent = id && page === "runs" ? "运行详情" : labels[page];
  document.title = `${id && page === "runs" ? "运行详情" : labels[page]} · EpochGate`;
  main.innerHTML = empty("正在加载…");
  window.scrollTo(0,0);
  const connection = document.querySelector("#connection");
  try {
    if (page === "runs" && id) await runDetail(version,decodeURIComponent(id),tab,kind,focusId ? decodeURIComponent(focusId) : undefined);
    else if (page === "research") await researchPage(version,id ? decodeURIComponent(id) : undefined);
    else await ({overview,workspace:workspacePage,runs:runsPage,system:systemPage})[page](version);
    if (version === routeVersion) connection.removeAttribute("data-offline");
  } catch (error) {
    if (version !== routeVersion) return;
    connection.toggleAttribute("data-offline",!navigator.onLine || !tokenPromise);
    main.innerHTML = `<div class="stack">${heading(labels[page],"读取本地控制面时遇到问题。")}${errorBox(error)}<div>${button("重试",'id="retry"')}</div></div>`;
    get(main,"#retry").onclick = renderRoute;
  }
}

window.addEventListener("hashchange",renderRoute);
window.addEventListener("offline",() => {
  document.querySelector("#connection").setAttribute("data-offline","");
  toast("连接中断。恢复后将读取服务端状态。",true);
});
window.addEventListener("online",() => {
  tokenPromise = undefined;
  if (!location.hash.startsWith("#/workspace")) renderRoute();
  else toast("已重新连接。可重试预检或查询上次提交。");
});
function openSearch() {
  const box = modal("全局搜索",`<form class="filters"><label>运行或证据 ID <input type="search" name="query" required></label>
    <label>开始日期 <input type="date" name="start"></label><label>结束日期 <input type="date" name="end"></label>
    <button class="button">搜索</button></form><div id="search-results"></div>`);
  const form = get(box,"form");
  form.elements.query.focus();
  form.onsubmit = async event => {
    event.preventDefault();
    const values = new FormData(form);
    const result = get(box,"#search-results");
    result.innerHTML = empty("正在搜索…");
    try {
      const args = new URLSearchParams({q:values.get("query"),start:values.get("start"),end:values.get("end")});
      const {matches} = await api(`/observatory/search?${args}`);
      result.innerHTML = table(["类型","标识"], matches.map(item=>[
        esc(item.type),`<a class="link mono" href="${item.type==="experiment" ? `#/research/${enc(item.id)}` : `${runLink(item.run_id)}/evidence/${enc(item.type)}/${enc(item.id)}`}">${esc(item.id)}</a>`
      ]));
    } catch (error) { result.innerHTML=errorBox(error); }
  };
}
document.querySelector("#global-search").onclick = openSearch;
document.addEventListener("keydown",event=>{
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==="k") {event.preventDefault(); openSearch();}
});
fetch("/health",{cache:"no-store"}).then(response=>response.json()).then(health=>{
  if (health.mode==="demo") document.querySelector(".sidebar-foot").textContent="隔离演示环境";
}).catch(()=>{});
document.addEventListener("click", async event => {
  const control = event.target.closest("[data-product-export]");
  if (!control) return;
  control.disabled = true;
  try {
    const body = await api(control.dataset.productExport,undefined,{blob:true});
    download(control.dataset.filename,body);
    toast("已生成下载文件。");
  } catch (error) { toast(error.message,true); }
  finally { control.disabled = false; }
});
renderRoute();
