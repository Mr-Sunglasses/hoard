// Downloads tab: progress, speed, stop and resume. Also drives the tab badge.

import { $, html, icon, render } from "../lib/dom.js";
import { api } from "../lib/api.js";
import * as format from "../lib/format.js";
import { state, emit } from "../state.js";
import { avatar, repoName, emptyState, goToButton } from "../ui/components.js";
import { toast, toastError } from "../ui/overlay.js";

export const POLL_INTERVAL_MS = 1500;

let lastActiveCount = 0;

/** Fetch the job list, update speeds and the badge, and redraw if visible. */
export async function pollDownloads() {
  try {
    state.jobs = await api.get("/api/downloads");
  } catch {
    return;   // server briefly unreachable; try again next tick
  }
  state.jobs.filter((job) => job.status === "downloading").forEach(trackSpeed);

  const active = state.jobs.filter((job) => job.status === "downloading").length;
  const badge = $("#downloads-count");
  badge.hidden = !active;
  badge.textContent = active;
  if (active < lastActiveCount) emit("library:changed");   // something finished
  lastActiveCount = active;

  if (state.tab === "downloads") renderDownloads();
}

/** Bytes land on disk in bursts, so the rate is smoothed between polls. */
function trackSpeed(job) {
  const now = Date.now();
  const previous = state.speeds[job.id];
  if (!previous) {
    state.speeds[job.id] = { bytes: job.done_bytes, time: now, rate: 0 };
  } else if (job.done_bytes > previous.bytes) {
    const instant = (job.done_bytes - previous.bytes) / ((now - previous.time) / 1000);
    const rate = previous.rate ? previous.rate * 0.6 + instant * 0.4 : instant;
    state.speeds[job.id] = { bytes: job.done_bytes, time: now, rate };
  }
}

export function renderDownloads() {
  const list = $("#downloads-list");
  if (!state.jobs.length) {
    render(list, emptyState({
      doodle: "download",
      title: "Nothing downloading",
      text: "Models you save from Browse show up here.",
      action: goToButton("browse", "Browse models"),
    }));
    return;
  }
  render(list, html`<div class="list sk">${state.jobs.map(jobRow)}</div>`);
}

function jobRow(job) {
  const percent = job.total_bytes ? Math.min(100, (job.done_bytes / job.total_bytes) * 100) : 0;
  const running = job.status === "downloading";
  const canResume = job.status === "cancelled" || job.status === "error";
  return html`
    <div class="item">
      <div class="row">
        ${avatar(job.repo_id)}
        <div class="row-main">
          <div class="name">${repoName(job.repo_id)}</div>
          <div class="sub">
            ${statusLine(job, percent)}
            <span>${format.bytes(Math.min(job.done_bytes, job.total_bytes))} of ${format.bytes(job.total_bytes)}</span>
            <span class="wide-only">${job.file_count} files</span>
          </div>
          ${running && html`
            <div class="progress sk ${percent < 0.5 && "waiting"}"><div style="width:${percent}%"></div></div>`}
        </div>
        <div class="row-side">
          ${running && html`<button class="btn sm danger" data-stop="${job.id}">Stop</button>`}
          ${canResume && html`<button class="btn sm" data-resume="${job.id}">${icon("retry")}Resume</button>`}
        </div>
      </div>
    </div>`;
}

function statusLine(job, percent) {
  if (job.status === "downloading") {
    const rate = state.speeds[job.id]?.rate || 0;
    const left = rate ? format.duration((job.total_bytes - job.done_bytes) / rate) : "";
    return html`
      <span class="tag hl">${percent.toFixed(0)}%</span>
      <span>${rate ? `${format.bytes(rate)}/s` : "warming up…"}</span>
      ${left && html`<span>~${left} left</span>`}`;
  }
  if (job.status === "done") {
    return html`<span class="tag ok">${icon("check")}saved</span><span>${format.ago(job.finished_at)}</span>`;
  }
  if (job.status === "cancelled") return html`<span class="tag warn">stopped</span>`;
  return html`
    <span class="tag danger">failed</span>
    <span title="${job.error}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px">${job.error}</span>`;
}

async function stopDownload(button) {
  button.disabled = true;
  await api.delete(`/api/downloads/${button.dataset.stop}`);
  toast("Download stopped");
}

async function resumeDownload(button) {
  button.disabled = true;
  const job = state.jobs.find((j) => j.id === button.dataset.resume);
  await api.post("/api/downloads", { repo_id: job.repo_id, revision: job.revision, include: job.include ?? null });
  toast(`Resuming ${job.repo_id}`);
}

export function initDownloads() {
  $("#downloads-list").addEventListener("click", async (event) => {
    const stop = event.target.closest("[data-stop]");
    const resume = event.target.closest("[data-resume]");
    if (!stop && !resume) return;
    try {
      if (stop) await stopDownload(stop);
      if (resume) await resumeDownload(resume);
    } catch (error) {
      toastError(error);
    }
    pollDownloads();
  });

  $("#clear-downloads").addEventListener("click", async () => {
    await api.post("/api/downloads/clear");
    pollDownloads();
  });
}
