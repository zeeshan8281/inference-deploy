"use strict";

const $ = (id) => document.getElementById(id);
const state = { profiles: [], suites: [], history: [], status: {}, busy: false, refreshing: false, selectedSuites: new Map() };
const suiteTemplate = {
  name: "Application checks",
  cases: [{
    name: "Readiness response",
    messages: [{ role: "user", content: "Reply with exactly READY and nothing else." }],
    expect: { contains: "READY" },
    max_tokens: 32,
    max_latency_ms: 30000
  }]
};
let confirmation = null;

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
}

function idOf(value) {
  if (value && typeof value === "object") return value.id || value.profile_id || null;
  return value || null;
}

function statusOf(item) {
  const raw = item?.status ?? item?.decision ?? item?.result ?? item?.outcome ?? "unknown";
  return typeof raw === "object" ? String(raw.status || raw.decision || "unknown") : String(raw);
}

function statusClass(value) {
  const text = String(value).toLowerCase();
  if (/^(pass|passed|ready|approved|success|succeeded|applied|healthy|complete|completed)$/.test(text)) return "good";
  if (/(reject|fail|error|abort)/.test(text)) return "bad";
  if (/(inconclusive|interrupt|running|pending|maint|drain|recover|start|rolled_back)/.test(text)) return "warn";
  return "neutral";
}

const pretty = (value) => JSON.stringify(value, null, 2);
const profileById = (id) => state.profiles.find((profile) => profile.id === idOf(id));
function profileName(value) {
  return profileById(value)?.name || (value && typeof value === "object" ? value.name : null) || idOf(value) || "—";
}

function errorText(error) {
  if (typeof error === "string") return error;
  if (Array.isArray(error)) return error.map((item) => item.msg || pretty(item)).join("\n");
  return error?.message || error?.detail || pretty(error);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
  });
  const text = await response.text();
  let body;
  try { body = text ? JSON.parse(text) : null; }
  catch { throw new Error(response.ok ? "The server returned invalid JSON." : "Request failed (" + response.status + ")."); }
  if (!response.ok) throw new Error(errorText(body?.detail || body?.error || body || "Request failed."));
  return body;
}

function collection(data, key) {
  return Array.isArray(data) ? data : (data?.[key] || data?.items || []);
}

function notify(message, kind = "") {
  $("notice").textContent = message;
  $("notice").className = "alert " + kind;
  $("notice").hidden = false;
}

const runProfile = (run) => idOf(run.profile_id || run.profile || run.target_profile_id || run.target || run.candidate);
const runSuite = (run) => idOf(run.suite_id || run.suite);
const runKind = (run) => run.kind || run.action || run.type || "operation";
const activeId = () => idOf(state.status.active_profile_id || state.status.active_profile || state.status.active);
const previousId = () => idOf(state.status.previous_profile_id || state.status.previous_profile || state.status.previous);
function runTime(run) {
  return run.finished_at || run.finished_utc || run.completed_at || run.started_at || run.started_utc || run.created_at || "";
}
function chronological(runs) {
  return [...runs].sort((a, b) => String(runTime(a)).localeCompare(String(runTime(b))));
}
function latestQualification(profileId, suiteId) {
  return chronological(state.history).reverse().find((run) =>
    /qualif/.test(String(runKind(run))) && runProfile(run) === profileId && runSuite(run) === suiteId);
}
function working() {
  return Boolean(state.busy || state.status.operation);
}

function renderEndpoint() {
  const status = state.status;
  const active = activeId();
  const previous = previousId();
  const runtime = status.runtime || status.active_runtime || {};
  const phase = status.operation?.phase || status.phase || "idle";
  const busy = working();
  const ready = Boolean(active && runtime.ready && !status.maintenance);
  $("endpoint-state").textContent = status.recovery_error ? "Recovery requires attention" : busy ? "Operation in progress" : status.maintenance && active ? "Maintenance" : ready ? "Serving" : "No active runtime";
  $("endpoint-state").className = "pill " + (status.recovery_error ? "bad" : busy || (status.maintenance && active) ? "warn" : ready ? "good" : "neutral");
  $("active-name").textContent = profileName(active);
  $("active-id").textContent = active || "No runtime loaded";
  $("previous-name").textContent = profileName(previous);
  $("previous-id").textContent = previous || "Available after an upgrade";
  $("runtime-pid").textContent = runtime.pid || status.runtime_pid || "—";
  $("runtime-url").textContent = runtime.url || status.runtime_url || "Awaiting backend";
  $("inflight").textContent = status.inflight ?? status.in_flight ?? 0;
  $("phase").textContent = busy ? "Operation · " + String(phase).replaceAll("_", " ") :
    status.recovery_error ? status.recovery_error : ready ? "Ready for requests" : "Create a profile and qualify it to begin";
  $("operation-indicator").classList.toggle("busy", busy);
  $("rollback-button").disabled = busy || !previous;
  $("raw-state").textContent = pretty(status);
}

function renderProfiles() {
  const list = $("profiles-list");
  if (list.contains(document.activeElement) && document.activeElement.tagName === "SELECT") return;
  list.replaceChildren();
  $("profile-count").textContent = state.profiles.length;
  $("profiles-empty").hidden = state.profiles.length > 0;
  for (const profile of state.profiles) {
    const card = node("article", "profile");
    card.dataset.testid = "profile-card";
    card.dataset.profileId = profile.id;
    const top = node("div", "profile-top");
    const title = node("div");
    title.append(node("h3", "", profile.name), node("div", "profile-id", profile.id));
    top.append(title);
    if (profile.id === activeId()) top.append(node("span", "pill good", "Active"));
    else if (profile.id === previousId()) top.append(node("span", "pill neutral", "Previous"));
    card.append(top, node("div", "profile-path", typeof profile.model === "object" ? profile.model.path : profile.model),
      node("div", "args-line", Array.isArray(profile.args) ? profile.args.join(" ") : ""));
    const controls = node("div", "profile-controls");
    const select = node("select");
    select.dataset.testid = "profile-suite-select";
    select.setAttribute("aria-label", "Acceptance suite for " + profile.name);
    const blank = node("option", "", state.suites.length ? "Select acceptance suite…" : "Create an acceptance suite first");
    blank.value = "";
    select.append(blank);
    for (const suite of state.suites) {
      const option = node("option", "", suite.name);
      option.value = suite.id;
      select.append(option);
    }
    const selected = state.selectedSuites.get(profile.id);
    if (selected && state.suites.some((suite) => suite.id === selected)) select.value = selected;
    else if (state.suites.length === 1) select.value = state.suites[0].id;
    select.disabled = working() || !state.suites.length;
    const qualify = node("button", "button secondary", "Qualify");
    const apply = node("button", "button primary", "Apply");
    qualify.type = apply.type = "button";
    qualify.dataset.testid = "qualify-button";
    apply.dataset.testid = "apply-button";
    const label = node("div", "qualification-label");
    function updateControls() {
      const suiteId = select.value;
      state.selectedSuites.set(profile.id, suiteId);
      const latest = latestQualification(profile.id, suiteId);
      const decision = latest ? statusOf(latest) : null;
      const approved = decision === "passed" || decision === "pass" || decision === "approved";
      qualify.disabled = working() || !suiteId;
      apply.disabled = working() || !suiteId || !approved;
      apply.title = approved ? "Apply this qualified profile" : "Pass qualification for this profile and suite first";
      label.textContent = latest ? "Last qualification · " + decision.replaceAll("_", " ") : "Qualification required before apply";
      label.style.color = latest && statusClass(decision) === "good" ? "var(--accent)" :
        latest && statusClass(decision) === "bad" ? "var(--danger)" : "var(--muted)";
    }
    select.addEventListener("change", updateControls);
    select.addEventListener("blur", () => { renderedSnapshot = ""; });
    qualify.addEventListener("click", () => confirmAction("qualify", profile, select.value));
    apply.addEventListener("click", () => confirmAction("apply", profile, select.value));
    controls.append(select, qualify, apply);
    card.append(controls, label);
    list.append(card);
    updateControls();
  }
}

function renderSuites() {
  const list = $("suites-list");
  const openIds = new Set([...list.querySelectorAll("details[open]")].map((item) => item.dataset.suiteId));
  list.replaceChildren();
  $("suite-count").textContent = state.suites.length;
  $("suites-empty").hidden = state.suites.length > 0;
  for (const suite of state.suites) {
    const item = node("article", "suite");
    item.dataset.testid = "suite-card";
    const top = node("div", "suite-top");
    const title = node("div");
    title.append(node("h3", "", suite.name), node("div", "profile-id", suite.id));
    top.append(title, node("span", "pill neutral", (suite.cases?.length || 0) + " checks"));
    const details = node("details");
    details.dataset.suiteId = suite.id;
    details.open = openIds.has(suite.id);
    details.append(node("summary", "", "Inspect definition"), node("pre", "", pretty(suite)));
    item.append(top, details);
    list.append(item);
  }
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function renderHistory() {
  const list = $("history-list");
  const openIds = new Set([...list.querySelectorAll("details[open]")].map((item) => item.dataset.runId));
  list.replaceChildren();
  $("history-count").textContent = state.history.length;
  $("history-empty").hidden = state.history.length > 0;
  for (const run of chronological(state.history).reverse()) {
    const item = node("details", "history-item");
    const id = String(run.id || run.run_id || runTime(run));
    item.dataset.runId = id;
    item.dataset.testid = "history-item";
    item.open = openIds.has(id);
    const summary = node("summary", "history-summary");
    summary.append(node("span", "history-action", String(runKind(run)).replaceAll("_", " ")));
    const context = node("div", "history-context");
    context.append(node("div", "", runProfile(run) ? profileName(runProfile(run)) : (run.message || id)),
      node("div", "history-date", formatDate(runTime(run))));
    const status = statusOf(run);
    summary.append(context, node("span", "pill " + statusClass(status), status.replaceAll("_", " ")));
    const receipt = node("div", "receipt");
    const receiptHeading = node("div", "receipt-heading");
    receiptHeading.append(node("span", "", "Receipt " + id));
    const download = node("button", "button secondary", "Save JSON");
    download.type = "button";
    download.addEventListener("click", (event) => {
      event.preventDefault();
      const url = URL.createObjectURL(new Blob([pretty(run) + "\n"], { type: "application/json" }));
      const anchor = node("a");
      anchor.href = url;
      anchor.download = "inference-deploy-" + id.replace(/[^a-zA-Z0-9_-]/g, "_") + ".json";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    receiptHeading.append(download);
    receipt.append(receiptHeading, node("pre", "", pretty(run)));
    item.append(summary, receipt);
    list.append(item);
  }
}

let renderedSnapshot = "";
function render() {
  const snapshot = JSON.stringify([state.status, state.profiles, state.suites, state.history, state.busy]);
  if (snapshot === renderedSnapshot) return;
  renderedSnapshot = snapshot;
  renderEndpoint();
  renderProfiles();
  renderSuites();
  renderHistory();
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [status, profiles, suites, history] = await Promise.all([
      api("/api/status"), api("/api/profiles"), api("/api/suites"), api("/api/history")
    ]);
    state.status = status;
    state.profiles = collection(profiles, "profiles");
    state.suites = collection(suites, "suites");
    state.history = collection(history, "history");
    $("connection-alert").hidden = true;
    render();
  } catch (error) {
    $("connection-alert").textContent = "Manager unavailable. " + error.message + " The page will retry automatically.";
    $("connection-alert").hidden = false;
    $("endpoint-state").textContent = "Disconnected";
    $("endpoint-state").className = "pill bad";
  } finally { state.refreshing = false; }
}

function confirmAction(action, profile, suiteId) {
  confirmation = { action, profile, suiteId };
  $("confirm-heading").textContent = action === "qualify" ? "Qualify this profile?" :
    action === "apply" ? "Apply this profile?" : "Restore the previous profile?";
  $("confirm-description").textContent = action === "qualify"
    ? "Run application checks against “" + profile.name + "”. The manager will temporarily stop the active runtime to test the candidate, then restore the committed endpoint."
    : action === "apply"
    ? "Apply “" + profile.name + "” after draining active requests. The manager will verify the candidate and attempt to restore the previous endpoint if the change fails."
    : "Restore “" + profileName(previousId()) + "” and verify it before making it active.";
  $("confirm-action").textContent = action === "qualify" ? "Run qualification" : action === "apply" ? "Apply profile" : "Roll back";
  $("confirm-dialog").showModal();
}

async function runOperation(command) {
  if (state.busy) return;
  state.busy = true;
  const name = command.action === "qualify" ? "Qualification" : command.action === "apply" ? "Apply" : "Rollback";
  notify(name + " started. Follow the current operation state below.", "warn");
  render();
  try {
    const body = command.action === "rollback" ? undefined : JSON.stringify({ profile_id: command.profile.id, suite_id: command.suiteId });
    const run = await api("/api/" + command.action, { method: "POST", body });
    const result = statusOf(run);
    notify(name + " finished · " + result.replaceAll("_", " ") + ". Inspect the receipt for details.",
      statusClass(result) === "bad" ? "error" : statusClass(result) === "warn" ? "warn" : "");
  } catch (error) {
    notify(error.message + " Review the current state and history before retrying.", "error");
  } finally {
    state.busy = false;
    await refresh();
    render();
  }
}

$("endpoint-url").textContent = window.location.origin + "/v1";
$("suite-json").value = pretty(suiteTemplate);
for (const id of ["new-profile", "empty-new-profile"]) $(id).addEventListener("click", () => $("profile-dialog").showModal());
for (const id of ["new-suite", "empty-new-suite"]) $(id).addEventListener("click", () => $("suite-dialog").showModal());
$("rollback-button").addEventListener("click", () => confirmAction("rollback"));
$("refresh-button").addEventListener("click", refresh);
$("copy-endpoint").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(window.location.origin + "/v1"); notify("API base URL copied."); }
  catch { notify("Copy the API URL shown in the endpoint card.", "warn"); }
});
document.querySelectorAll(".close-dialog").forEach((button) => {
  button.addEventListener("click", () => $(button.dataset.dialog).close());
});
$("confirm-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("confirm-dialog").close();
  if (confirmation) runOperation(confirmation);
});
$("profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("profile-error").hidden = true;
  $("save-profile").disabled = true;
  try {
    const args = JSON.parse($("profile-args").value);
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string")) throw new Error("Runtime arguments must be a JSON array of strings.");
    const profile = {
      name: $("profile-name").value.trim(), runtime: $("profile-runtime").value.trim(),
      model: $("profile-model").value.trim(), args,
      startup_timeout: Number($("profile-startup-timeout").value),
      drain_timeout: Number($("profile-drain-timeout").value)
    };
    if (!profile.runtime.startsWith("/") || !profile.model.startsWith("/")) throw new Error("Runtime and model paths must be absolute paths.");
    await api("/api/profiles", { method: "POST", body: JSON.stringify(profile) });
    $("profile-dialog").close();
    $("profile-name").value = "";
    notify("Profile “" + profile.name + "” saved. Select a suite and run qualification.");
    await refresh();
  } catch (error) {
    $("profile-error").textContent = error.message;
    $("profile-error").hidden = false;
  } finally { $("save-profile").disabled = false; }
});
$("suite-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("suite-error").hidden = true;
  $("save-suite").disabled = true;
  try {
    const suite = JSON.parse($("suite-json").value);
    if (!suite || typeof suite !== "object" || Array.isArray(suite)) throw new Error("Suite definition must be a JSON object.");
    await api("/api/suites", { method: "POST", body: JSON.stringify(suite) });
    $("suite-dialog").close();
    notify("Acceptance suite “" + suite.name + "” saved.");
    await refresh();
  } catch (error) {
    $("suite-error").textContent = error.message;
    $("suite-error").hidden = false;
  } finally { $("save-suite").disabled = false; }
});

refresh();
setInterval(refresh, 1500);
