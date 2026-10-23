// Per-stage processing timings (Phase 5). Reads the `stage_timings` field the
// status endpoints add to the processing job. Skipped, cached, fallback and
// failed stages are shown as such, never as a misleading "0 s".

export const STAGE_LABELS = {
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

export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

export function outcome(stage) {
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

export function note(stage) {
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
    <div className="stage-timings" data-testid="stage-timings">
      <div className="stage-timings__title">
        Stage timings
        {timings.processing_seconds != null && (
          <span className="stage-timings__total">
            {" "}· processing {formatSeconds(timings.processing_seconds)}
          </span>
        )}
      </div>
      <ul className="stage-timings__list">
        {timings.stages.map((stage) => {
          const extra = note(stage);
          return (
            <li
              key={stage.stage}
              data-testid={`stage-${stage.stage}`}
              className={`stage-timings__row${stage.parent_stage ? " is-child" : ""}`}
            >
              <span>{STAGE_LABELS[stage.stage] || stage.stage}</span>
              {": "}
              <strong className={`stage-timings__outcome is-${stage.status}`}>{outcome(stage)}</strong>
              {extra && <div className="stage-timings__note">{extra}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
