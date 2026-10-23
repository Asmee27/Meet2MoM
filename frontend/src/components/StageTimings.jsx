// Per-stage processing timings (Phase 5). Reads the `stage_timings` field the
// status endpoints add to the processing job. Skipped, cached, fallback and
// failed stages are shown as such, never as a misleading "0 s".

const STAGE_LABELS = {
  speech_pipeline: "Speech pipeline (total)",
  pipeline_startup: "Start-up (loading libraries)",
  audio_preparation: "Audio preparation",
  transcription: "Speech-to-text",
  speaker_attribution: "Speaker attribution",
  transcript_merge: "Transcript merge",
  transcript_index: "Utterance index",
  topic_segmentation: "Topic segmentation",
  extraction: "Decision & action extraction",
  verification: "Verification (total)",
  evidence_retrieval: "Evidence retrieval",
  nli_model_load: "NLI model load",
  nli_verification: "NLI verification",
  minutes_generation: "Minutes generation",
  pdf_export: "PDF export",
};

const STATUS_COLORS = {
  completed: "#16a34a",
  running: "#6366f1",
  failed: "#dc2626",
  fallback: "#d97706",
  cached: "#0891b2",
  skipped: "#94a3b8",
};

function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

function outcome(stage) {
  const time = formatSeconds(stage.duration_seconds);
  switch (stage.status) {
    case "completed":
      return time;
    case "running":
      return "running…";
    case "failed":
      return time ? `failed after ${time}` : "failed";
    case "fallback":
      return time ? `fallback · ${time}` : "fallback";
    case "cached":
      return time ? `cached · ${time}` : "cached";
    default:
      return "skipped";
  }
}

function note(stage) {
  const d = stage.detail || {};
  if (stage.stage === "extraction" && d.mode_used === "llm") {
    const attempts = (d.llm_attempts || [])
      .map((a) => `${a.outcome} ${formatSeconds(a.seconds)}`)
      .join(", ");
    return `LLM: ${d.llm_provider || "?"} / ${d.llm_model || "?"} · provider wait ${formatSeconds(d.llm_provider_seconds || 0)} · ${d.llm_requests ?? 0} request(s)${attempts ? ` (${attempts})` : ""}`;
  }
  return d.fallback_reason || d.title_fallback_reason || d.reason || d.error || null;
}

export default function StageTimings({ timings }) {
  if (!timings || !timings.stages || timings.stages.length === 0) return null;
  return (
    <div className="stage-timings" data-testid="stage-timings" style={{ marginTop: "0.75rem", fontSize: "0.82rem" }}>
      <div style={{ fontWeight: 600, marginBottom: "0.35rem" }}>
        Stage timings
        {timings.processing_seconds != null && (
          <span style={{ fontWeight: 400, opacity: 0.75 }}>
            {" "}· processing {formatSeconds(timings.processing_seconds)}
          </span>
        )}
      </div>
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {timings.stages.map((stage) => {
          const extra = note(stage);
          return (
            <li
              key={stage.stage}
              data-testid={`stage-${stage.stage}`}
              style={{ padding: "0.15rem 0", paddingLeft: stage.parent_stage ? "1.1rem" : 0 }}
            >
              <span>{STAGE_LABELS[stage.stage] || stage.stage}</span>
              {": "}
              <strong style={{ color: STATUS_COLORS[stage.status] || undefined }}>{outcome(stage)}</strong>
              {extra && (
                <div style={{ opacity: 0.75, paddingLeft: "0.5rem", wordBreak: "break-word" }}>{extra}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
