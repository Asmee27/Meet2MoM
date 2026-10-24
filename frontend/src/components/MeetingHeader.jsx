// Phase 8: header for an open meeting: name, metadata, review state and the
// workflow steps. Steps opened in this session can be revisited; others unlock
// only through the workflow itself (which runs the real backend stage).
import { useEffect, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import { listMeetings } from "../services/api";
import { formatDateTime, formatDuration, meetingTitle } from "../format";
import { Badge, Icon } from "./ui";

const STEPS = [
  { step: 2, label: "Transcript" },
  { step: 3, label: "Extract" },
  { step: 4, label: "Verify & review" },
  { step: 5, label: "Minutes" },
];

export default function MeetingHeader({ meeting, visitedSteps, onBack }) {
  const { currentStep, setCurrentStep, backendFileId, reviewData, selectedFile } = useMeetingFlow();
  const [info, setInfo] = useState(meeting?.meeting_id === backendFileId ? meeting : null);

  useEffect(() => {
    if (meeting?.meeting_id === backendFileId) {
      setInfo(meeting);
      return undefined;
    }
    let active = true;
    listMeetings()
      .then((items) => {
        if (active) setInfo((items || []).find((m) => m.meeting_id === backendFileId) || null);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [meeting, backendFileId]);

  const title = info ? meetingTitle(info) : selectedFile?.name || `Meeting ${String(backendFileId || "").slice(0, 8)}`;
  const draft = reviewData?.draft_status || info?.review_status;

  return (
    <div className="meeting-header ui-enter">
      <button type="button" className="crumb-back" onClick={onBack}>
        <Icon name="arrowLeft" /> All meetings
      </button>
      <div className="meeting-header__row">
        <div className="meeting-header__text">
          <h1 className="meeting-header__title" title={title}>{title}</h1>
          <div className="ui-meta">
            {info?.created_at && <span>{formatDateTime(info.created_at)}</span>}
            {typeof info?.duration_seconds === "number" && <span>{formatDuration(info.duration_seconds)}</span>}
            {info?.speaker_count > 0 && <span>{info.speaker_count} speaker{info.speaker_count === 1 ? "" : "s"}</span>}
            <span className="ui-mono">{String(backendFileId || "").slice(0, 16)}</span>
          </div>
        </div>
        {draft === "approved" ? (
          <Badge tone="success" icon="shield">Minutes approved</Badge>
        ) : draft === "reopened" ? (
          <Badge tone="warning">Review reopened</Badge>
        ) : (
          <Badge tone="warning">Not approved yet</Badge>
        )}
      </div>

      <nav className="stepper" aria-label="Meeting workflow">
        <ol>
          {STEPS.map(({ step, label }, index) => {
            const isCurrent = currentStep === step;
            const reachable = Boolean(visitedSteps?.includes(step)) && !isCurrent;
            const done = step < currentStep;
            return (
              <li key={step} className={`stepper__item${isCurrent ? " is-current" : ""}${done ? " is-done" : ""}`}>
                <button
                  type="button"
                  className="stepper__button"
                  aria-current={isCurrent ? "step" : undefined}
                  disabled={!reachable}
                  onClick={() => setCurrentStep(step)}
                >
                  <span className="stepper__index" aria-hidden="true">
                    {done ? <Icon name="check" /> : index + 1}
                  </span>
                  <span>{label}</span>
                  {done && <span className="ui-visually-hidden"> (completed)</span>}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
    </div>
  );
}
