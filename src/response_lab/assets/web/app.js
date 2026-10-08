"use strict";

const $ = (selector) => document.querySelector(selector);
let current = null;
let record = null;
let investigationVersion = 0;
let busy = false;

function safe(value) {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (character) => entities[character]);
}

async function api(path, body) {
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function notice(error) {
  $("#message").textContent = error?.message || String(error);
}

async function loadCases() {
  const data = await api("disruptions");
  const active = data.disruptions.filter((item) => !item.resolved).length;
  $("#active-count").textContent = String(active).padStart(2, "0");
  $("#queue-count").textContent = String(active).padStart(2, "0");
  $("#cards").innerHTML = data.disruptions.map((item) => {
    const shipment = data.shipments.find((entry) => entry.id === item.shipment_id);
    return `<article class="card"><div class="card-top"><strong>${safe(item.id)}</strong>
      <span class="severity ${safe(item.severity)}">${item.resolved ? "resolved" : safe(item.severity)}</span></div>
      <h3>${safe(shipment.sku)} · ${safe(shipment.destination_id.replace("FAC-", ""))}</h3>
      <p>${safe(item.summary)}</p><div class="card-meta"><span>SHIPMENT ${safe(shipment.id)}</span>
      <span>${safe(shipment.units)} UNITS</span></div><button class="button" data-id="${safe(item.id)}"
      ${item.resolved || busy ? "disabled" : ""}>${item.resolved ? "Resolved in simulation" : "Investigate →"}</button></article>`;
  }).join("");
  document.querySelectorAll("[data-id]").forEach((button) => {
    button.onclick = () => investigate(button.dataset.id);
  });
}

async function investigate(id) {
  if (busy) return;
  const version = ++investigationVersion;
  $("#message").textContent = "";
  current = null;
  record = null;
  $("#workspace").innerHTML = '<div class="empty-state">Reading linked objects and checking alternatives…</div>';
  location.hash = "investigation";
  try {
    const result = await api("investigate", { disruption_id: id });
    if (version !== investigationVersion) return;
    current = result;
    render(id);
  } catch (error) {
    if (version !== investigationVersion) return;
    $("#workspace").innerHTML = `<div class="empty-state"><span>◇</span><h3>Investigation unavailable</h3>
      <p>${safe(error.message)}</p></div>`;
  }
}

function render(id) {
  const proposal = current.proposal;
  const gate = current.gate;
  const evidence = proposal.evidence.map((entry) => `<tr><td>${safe(entry.object_type)}</td>
    <td>${safe(entry.object_id)}</td><td>${safe(entry.field)}</td>
    <td title="${safe(entry.source)}">${safe(entry.value)}</td></tr>`).join("");
  const checks = Object.entries(gate.checks).map(([key, value]) => `<div class="check">
    <span class="${value ? "pass" : "fail"}">${value ? "✓" : "✕"}</span> ${safe(key.replaceAll("_", " "))}</div>`).join("");
  $("#workspace").innerHTML = `<div class="workspace-grid"><div class="panel">
    <div class="eyebrow">CASE ${safe(id)} / RECOMMENDATION</div><h3 class="plan-heading">Alternate inventory response</h3>
    <div class="result ${gate.allowed_for_review ? "" : "blocked"}"><div>
    <strong>${gate.allowed_for_review ? "Ready for human review" : "Blocked by policy"}</strong>
    <small>${safe(proposal.rationale)}</small></div></div><div class="stat-row">
    <div class="stat"><small>TIME RECOVERED</small><strong>${safe(proposal.days_saved)} days</strong></div>
    <div class="stat"><small>INCREMENTAL COST</small><strong>$${safe(proposal.incremental_cost_usd.toLocaleString("en-US"))}</strong></div>
    <div class="stat"><small>ARRIVAL DAY</small><strong>${safe(proposal.estimated_arrival_day)}</strong></div></div>
    <p>Reserve <b>${safe(proposal.quantity)} units</b> from <b>${safe(proposal.inventory_id)}</b>
    and dispatch via <b>${safe(proposal.route_id)}</b>.</p><h3>Policy checks</h3><div class="checks">${checks}</div>
    <div class="actions" id="actions"></div></div><div class="panel"><h3>Read tool trace</h3>
    <div class="flow">${current.trace.map((entry) => `<span>${safe(entry.tool)}</span>`).join("")}</div>
    <h3 class="evidence-heading">Grounding evidence</h3><div class="table-scroll"><table class="table">
    <thead><tr><th>OBJECT</th><th>ID</th><th>FIELD</th><th>VALUE</th></tr></thead><tbody>${evidence}</tbody>
    </table></div></div></div>`;
  renderActions();
}

async function mutate(operation) {
  if (busy) return;
  busy = true;
  $("#message").textContent = "";
  document.querySelectorAll("button, .actions input").forEach((element) => { element.disabled = true; });
  try {
    record = await operation();
    await audit();
  } catch (error) {
    notice(error);
  } finally {
    busy = false;
    $("#refresh-audit").disabled = false;
    renderActions();
    try { await loadCases(); } catch (error) { notice(error); }
  }
}

function renderActions() {
  const actions = $("#actions");
  if (!actions) return;
  if (!current.gate.allowed_for_review) {
    actions.innerHTML = '<span class="hint">This recommendation cannot be submitted under the current policy.</span>';
  } else if (!record) {
    actions.innerHTML = '<button class="button" id="submit">Submit for approval →</button><span class="hint">No inventory changes before approval.</span>';
    $("#submit").onclick = () => mutate(() => api("proposals", current));
  } else if (record.status === "pending") {
    actions.innerHTML = '<input id="approver" maxlength="80" placeholder="Approver name" aria-label="Approver name"><button class="button" id="approve">Approve</button><button class="button danger" id="reject">Reject</button>';
    for (const [id, approve] of [["approve", true], ["reject", false]]) {
      $("#" + id).onclick = () => {
        const approver = $("#approver").value;
        mutate(() => api(`proposals/${record.id}/decision`, { approver, approve }));
      };
    }
  } else if (record.status === "approved") {
    actions.innerHTML = `<span class="hint">Approved by ${safe(record.approver)}</span><button class="button" id="execute">Execute local simulation →</button>`;
    $("#execute").onclick = () => mutate(() => api(`proposals/${record.id}/execute`, {}));
  } else {
    actions.innerHTML = `<span class="hint">${safe(record.status.toUpperCase())} · ${safe(record.approver)}</span>`;
  }
}

async function audit() {
  const data = await api("audit");
  $("#integrity").textContent = data.chain_valid ? "VALID" : "FAILED";
  $("#audit-list").innerHTML = data.events.length ? data.events.slice().reverse().map((event) => {
    const payload = JSON.parse(event.payload);
    return `<div class="audit-item"><b>#${event.seq}</b><span>${safe(event.event.replaceAll("_", " "))}
      <small class="audit-detail">Proposal ${safe(payload.proposal_id)}${payload.approver ? " · " + safe(payload.approver) : ""}</small></span>
      <time>${safe(new Date(event.at * 1000).toLocaleString())}</time></div>`;
  }).join("") : '<span class="hint">No decisions recorded yet.</span>';
}

$("#refresh-audit").onclick = () => audit().catch(notice);
Promise.all([loadCases(), audit()]).catch(notice);
