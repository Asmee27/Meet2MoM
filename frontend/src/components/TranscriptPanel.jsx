// Phase 8: indexed transcript with evidence focus and reviewer correction.
// A correction is saved on the server with the original ASR text, reviewer,
// reason and time. Nothing is ever replaced automatically.
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import { getTranscript } from "../services/api";
import { formatClock, formatDateTime } from "../format";
import { Alert, Badge, Button, EmptyState, Icon, SkeletonRows, useToast } from "./ui";

// Same threshold the verifier uses for its low-ASR-confidence warning (fusion-v1).
const LOW_ASR_CONF = 0.5;
const SPEAKER_TONES = 6;

function speakerTone(name, palette) {
  if (!palette.has(name)) palette.set(name, palette.size % SPEAKER_TONES);
  return palette.get(name);
}

function CorrectionForm({ utterance, onSave, onCancel }) {
  const [text, setText] = useState(utterance.text || "");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const textId = `correct-text-${utterance.utt_id}`;
  const reasonId = `correct-reason-${utterance.utt_id}`;
  const helpId = `correct-help-${utterance.utt_id}`;
  const unchanged = text.trim() === (utterance.text || "").trim();

  const submit = async (event) => {
    event.preventDefault();
    if (!text.trim() || unchanged) return;
    setSaving(true);
    setError("");
    try {
      await onSave(text.trim(), reason.trim());
    } catch (err) {
      setError(err?.message || "The correction could not be saved.");
      setSaving(false);
    }
  };

  return (
    <form className="correction-form" onSubmit={submit} data-testid={`correction-form-${utterance.utt_id}`}>
      <p className="ui-help" id={helpId}>
        Listen to the recording and type what was actually said. The original speech-to-text output is kept, and
        claims that cite this meeting must be verified again before approval.
      </p>
      <div className="ui-field">
        <label className="ui-label" htmlFor={textId}>Corrected text</label>
        <textarea
          id={textId}
          className="ui-textarea"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-describedby={helpId}
          maxLength={4000}
          required
          disabled={saving}
          autoFocus
        />
      </div>
      <div className="ui-field">
        <label className="ui-label" htmlFor={reasonId}>Reason <span className="ui-help">(recorded in the audit history)</span></label>
        <input
          id={reasonId}
          className="ui-input"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Checked against the audio at 0:21"
          disabled={saving}
        />
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="correction-form__actions">
        <Button type="submit" variant="primary" size="sm" loading={saving} disabled={saving || !text.trim() || unchanged}>
          Save correction
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </form>
  );
}

function UtteranceRow({ utterance, tone, focused, editable, editing, onEdit, onCancel, onSave, rowRef }) {
  const [showOriginal, setShowOriginal] = useState(false);
  const lowConfidence = typeof utterance.asr_conf === "number" && utterance.asr_conf < LOW_ASR_CONF;

  return (
    <li
      ref={rowRef}
      id={`utt-${utterance.utt_id}`}
      className={`utt${focused ? " is-focused" : ""}${utterance.corrected ? " is-corrected" : ""}`}
      tabIndex={-1}
      data-testid={`utterance-${utterance.utt_id}`}
    >
      <div className="utt__head">
        <span className={`speaker-chip speaker-chip--${tone}`} title={utterance.speaker_id}>
          {utterance.speaker_name || utterance.speaker_id || "Unknown speaker"}
        </span>
        <span className="utt__time">{formatClock(utterance.start)}–{formatClock(utterance.end)}</span>
        <span className="ui-mono utt__id">{utterance.utt_id}</span>
        {utterance.corrected && <Badge tone="accent" icon="pencil">Corrected by reviewer</Badge>}
        {lowConfidence && (
          <Badge tone="warning" title={`ASR confidence ${utterance.asr_conf.toFixed(2)}`}>
            Low ASR confidence
          </Badge>
        )}
        {editable && !editing && (
          <Button
            size="sm"
            variant="ghost"
            icon="pencil"
            className="utt__edit"
            onClick={onEdit}
            aria-label={`Correct text of ${utterance.utt_id}`}
          >
            Correct
          </Button>
        )}
      </div>

      {editing ? (
        <CorrectionForm utterance={utterance} onSave={onSave} onCancel={onCancel} />
      ) : (
        <p className="utt__text">{utterance.text}</p>
      )}

      {utterance.corrected && !editing && (
        <div className="utt__correction">
          <button
            type="button"
            className="utt__toggle"
            aria-expanded={showOriginal}
            onClick={() => setShowOriginal((v) => !v)}
          >
            <Icon name={showOriginal ? "chevronDown" : "chevronRight"} size={14} />
            Original speech-to-text
          </button>
          {showOriginal && (
            <div className="utt__original">
              <p><span className="ui-visually-hidden">Original: </span><s>{utterance.asr_text}</s></p>
              <p className="ui-help">
                Corrected {utterance.correction?.reviewer ? `by ${utterance.correction.reviewer} ` : ""}
                {utterance.correction?.created_at ? `on ${formatDateTime(utterance.correction.created_at)}` : ""}
                {utterance.correction?.reason ? ` · “${utterance.correction.reason}”` : ""}
              </p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

const TranscriptPanel = forwardRef(function TranscriptPanel(
  { meetingId, editable = true, lockedReason = "", title = "Transcript", onCorrected, className = "" },
  ref,
) {
  const { correctUtterance } = useMeetingFlow();
  const { notify } = useToast();
  const [state, setState] = useState({ status: "loading", items: [], error: "" });
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [focusId, setFocusId] = useState(null);
  const rows = useRef({});
  const palette = useRef(new Map());

  const load = useCallback(() => {
    if (!meetingId) return;
    setState((s) => ({ ...s, status: "loading", error: "" }));
    getTranscript(meetingId)
      .then((data) => setState({ status: "ready", items: Array.isArray(data) ? data : [], error: "" }))
      .catch((err) => setState({ status: "error", items: [], error: err?.message || "The transcript could not be loaded." }));
  }, [meetingId]);

  useEffect(() => {
    load();
  }, [load]);

  // Evidence links call focusUtterance(uttId): scroll to it and highlight it.
  useImperativeHandle(ref, () => ({
    focusUtterance(uttId) {
      setQuery("");
      setFocusId(uttId);
      window.requestAnimationFrame(() => {
        const node = rows.current[uttId];
        if (!node) return;
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        node.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "center" });
        node.focus({ preventScroll: true });
      });
    },
  }), []);

  const save = async (uttId, text, reason) => {
    const result = await correctUtterance(uttId, text, reason);
    if (result?.utterance) {
      setState((s) => ({ ...s, items: s.items.map((u) => (u.utt_id === uttId ? result.utterance : u)) }));
    }
    setEditingId(null);
    notify(`Correction saved for ${uttId}. Run verification again before approving.`, "success");
    onCorrected?.(result);
  };

  const needle = query.trim().toLowerCase();
  const visible = state.items.filter((u) =>
    !needle ||
    (u.text || "").toLowerCase().includes(needle) ||
    (u.speaker_name || u.speaker_id || "").toLowerCase().includes(needle) ||
    (u.utt_id || "").toLowerCase().includes(needle)
  );
  const correctedCount = state.items.filter((u) => u.corrected).length;

  return (
    <section className={`ui-card transcript-panel ${className}`} aria-labelledby="transcript-panel-title">
      <div className="transcript-panel__header">
        <div>
          <h2 className="ui-card__title" id="transcript-panel-title">{title}</h2>
          {state.status === "ready" && (
            <div className="ui-meta">
              <span>{state.items.length} utterances</span>
              {correctedCount > 0 && <span>{correctedCount} corrected by a reviewer</span>}
            </div>
          )}
        </div>
        {state.status === "ready" && state.items.length > 0 && (
          <div className="transcript-panel__search">
            <label className="ui-visually-hidden" htmlFor={`transcript-search-${meetingId}`}>Search transcript</label>
            <input
              id={`transcript-search-${meetingId}`}
              className="ui-input ui-input--search"
              type="search"
              placeholder="Search words, speakers or IDs"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        )}
      </div>

      {!editable && lockedReason && state.status === "ready" && (
        <div className="transcript-panel__notice">
          <Icon name="shield" size={14} /> {lockedReason}
        </div>
      )}

      <div className="transcript-panel__body">
        {state.status === "loading" && <SkeletonRows rows={4} label="Loading transcript" />}
        {state.status === "error" && (
          <Alert tone="danger" title="Transcript unavailable" actions={<Button size="sm" icon="refresh" onClick={load}>Try again</Button>}>
            <p>{state.error}</p>
          </Alert>
        )}
        {state.status === "ready" && state.items.length === 0 && (
          <EmptyState icon="file" title="No utterances">The transcript index has no utterances for this meeting.</EmptyState>
        )}
        {state.status === "ready" && state.items.length > 0 && visible.length === 0 && (
          <p className="ui-help">No utterance matches “{query}”.</p>
        )}
        {visible.length > 0 && (
          <ol className="utt-list">
            {visible.map((u) => (
              <UtteranceRow
                key={u.utt_id}
                utterance={u}
                tone={speakerTone(u.speaker_name || u.speaker_id || "?", palette.current)}
                focused={focusId === u.utt_id}
                editable={editable}
                editing={editingId === u.utt_id}
                onEdit={() => setEditingId(u.utt_id)}
                onCancel={() => setEditingId(null)}
                onSave={(text, reason) => save(u.utt_id, text, reason)}
                rowRef={(node) => { rows.current[u.utt_id] = node; }}
              />
            ))}
          </ol>
        )}
      </div>
    </section>
  );
});

export default TranscriptPanel;
