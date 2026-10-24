// Phase 8: processing view. Shows the job's real stages, grouped for people,
// with the Phase 5 timings when they exist. No percentage is invented: a stage
// is complete only when the job has moved past it or its timing says so.
import { useEffect, useState } from "react";
import StageTimings, { STAGE_LABELS, formatSeconds, note } from "./StageTimings";
import { Badge, Icon } from "./ui";

// Job stages (app/core/jobs.py) in order.
const JOB_STAGES = [
  "uploaded", "audio", "quality", "asr", "diarization", "merge",
  "index", "topics", "extraction", "verification", "mom", "done",
];

const GROUPS = [
  { id: "speech", label: "Speech processing", jobStages: ["uploaded", "audio", "quality", "asr"],
    timings: ["pipeline_startup", "audio_preparation", "transcription"] },
  { id: "speakers", label: "Speaker attribution & transcript index", jobStages: ["diarization", "merge", "index"],
    timings: ["speaker_attribution", "transcript_merge", "transcript_index"] },
  { id: "topics", label: "Topic segmentation", jobStages: ["topics"], timings: ["topic_segmentation"] },
  { id: "extraction", label: "Decision & action extraction", jobStages: ["extraction"], timings: ["extraction"] },
  { id: "verification", label: "Evidence verification", jobStages: ["verification"],
    timings: ["verification", "evidence_retrieval", "nli_model_load", "nli_verification"] },
  { id: "minutes", label: "Minutes & PDF export", jobStages: ["mom"], timings: ["minutes_generation", "pdf_export"] },
];

const STATUS_UI = {
  completed: { icon: "check", text: "Completed" },
  running: { icon: null, text: "Running" },
  failed: { icon: "error", text: "Failed" },
  fallback: { icon: "fallback", text: "Fallback used" },
  cached: { icon: "cache", text: "Reused from cache" },
  skipped: { icon: "skip", text: "Skipped" },
  on_demand: { icon: "clock", text: "Runs on demand" },
  pending: { icon: null, text: "Waiting" },
};

function groupStatus(group, rows, job) {
  const jobIndex = JOB_STAGES.indexOf(job?.stage);
  const first = JOB_STAGES.indexOf(group.jobStages[0]);
  const last = JOB_STAGES.indexOf(group.jobStages[group.jobStages.length - 1]);
  const jobFailedHere = job?.state === "failed" && jobIndex >= first && jobIndex <= last;
  const jobHere = jobIndex >= first && jobIndex <= last && job?.state !== "done";

  if (rows.some((r) => r.status === "failed") || jobFailedHere) return "failed";
  if (rows.some((r) => r.status === "running") || (jobHere && job?.state === "running")) return "running";
  if (rows.length > 0) {
    if (rows.every((r) => r.status === "skipped")) {
      return rows.some((r) => /on demand/i.test(r.detail?.note || "")) ? "on_demand" : "skipped";
    }
    if (rows.some((r) => r.status === "fallback")) return "fallback";
    if (rows.every((r) => r.status === "cached" || r.status === "skipped")) return "cached";
    return "completed";
  }
  if (job?.state === "done" || jobIndex > last) {
    // The job only marks "mom"; minutes are generated on demand.
    return group.id === "minutes" ? "on_demand" : "completed";
  }
  return "pending";
}

function useNow(active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export default function ProcessingStages({ job, title = "Processing", onRetry, retryLabel = "Try again" }) {
  const stages = job?.stage_timings?.stages || [];
  const byName = Object.fromEntries(stages.map((s) => [s.stage, s]));
  const groups = GROUPS.map((group) => {
    const rows = group.timings.map((name) => byName[name]).filter(Boolean);
    const status = groupStatus(group, rows, job);
    const top = rows.filter((r) => !r.parent_stage);
    const measured = (top.length ? top : rows).filter((r) => typeof r.duration_seconds === "number");
    const seconds = measured.length ? measured.reduce((sum, r) => sum + r.duration_seconds, 0) : null;
    const running = rows.find((r) => r.status === "running");
    const detail = rows.map(note).find(Boolean) || null;
    return { ...group, rows, status, seconds, running, detail };
  });

  const anyRunning = groups.some((g) => g.status === "running");
  const now = useNow(anyRunning);
  const failed = job?.state === "failed";
  const done = job?.state === "done" || job?.stage === "done";
  const usedFallback = groups.find((g) => g.id === "extraction" && g.status === "fallback");

  return (
    <section className="ui-card ui-card--pad processing" aria-labelledby="processing-title" data-testid="processing-stages">
      <div className="processing__header">
        <h2 className="processing__title" id="processing-title">{title}</h2>
        {failed ? (
          <Badge tone="danger">Failed</Badge>
        ) : done ? (
          <Badge tone="success">Processing complete</Badge>
        ) : (
          <Badge tone="info" pulse>{job?.state === "queued" ? "Queued" : "In progress"}</Badge>
        )}
      </div>

      <ol className="stage-list" aria-live="polite">
        {groups.map((group) => {
          const ui = STATUS_UI[group.status];
          const elapsed = group.running?.started_at
            ? Math.max(0, (now - new Date(group.running.started_at).getTime()) / 1000)
            : null;
          return (
            <li
              key={group.id}
              className={`stage stage--${group.status}${["completed", "cached", "fallback", "skipped", "on_demand"].includes(group.status) ? " is-complete" : ""}`}
              data-testid={`group-${group.id}`}
            >
              <span className="stage__icon" aria-hidden="true">
                {group.status === "running" ? <span className="ui-spinner" /> : ui.icon && <Icon name={ui.icon} />}
              </span>
              <div>
                <div className="stage__label">{group.label}</div>
                <div className="stage__detail">
                  <span className="ui-visually-hidden">Status: </span>
                  {ui.text}
                  {group.id === "minutes" && group.status === "on_demand" && " · created when you open or approve the minutes"}
                </div>
                {group.detail && group.status !== "on_demand" && <div className="stage__detail">{group.detail}</div>}
                {group.rows.length > 1 && group.status !== "pending" && group.status !== "on_demand" && (
                  <ul className="stage__substages">
                    {group.rows.map((row) => (
                      <li key={row.stage}>
                        <span>{STAGE_LABELS[row.stage] || row.stage}</span>
                        <span className="stage__time">
                          {row.status === "completed" ? formatSeconds(row.duration_seconds) : row.status}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <span className="stage__time">
                {group.status === "running" && elapsed !== null
                  ? `${formatSeconds(elapsed)} so far`
                  : group.seconds !== null && group.status !== "on_demand"
                    ? formatSeconds(group.seconds)
                    : ""}
              </span>
            </li>
          );
        })}
      </ol>

      {usedFallback && (
        <p className="stage__detail" data-testid="fallback-note">
          <Icon name="fallback" size={14} /> Extraction used a fallback extractor, not the configured LLM. Items are still checked
          against the transcript.
        </p>
      )}

      {failed && (
        <div className="ui-alert ui-alert--danger" role="alert">
          <Icon name="error" />
          <div className="ui-alert__body">
            <strong className="ui-alert__title">Processing stopped at “{job?.stage}”</strong>
            <p>{job?.error || "The pipeline reported a failure without details."}</p>
            {onRetry && (
              <div className="ui-alert__actions">
                <button type="button" className="ui-btn ui-btn--sm" onClick={onRetry}>{retryLabel}</button>
              </div>
            )}
          </div>
        </div>
      )}

      {stages.length > 0 && (
        <details className="processing__details">
          <summary>Detailed stage timings</summary>
          <StageTimings timings={job.stage_timings} />
        </details>
      )}
    </section>
  );
}
