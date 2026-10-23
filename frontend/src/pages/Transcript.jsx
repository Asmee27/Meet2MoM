import { useMeetingFlow } from "../context/MeetingFlowContext";
import TranscriptPanel from "../components/TranscriptPanel";
import { Alert, Button } from "../components/ui";

export default function Transcript() {
  const {
    currentStep,
    backendFileId,
    transcriptionError,
    isTranscribing,
    transcriptionComplete,
    reviewData,
    goToExtract,
  } = useMeetingFlow();

  if (currentStep !== 2) return null;

  const approved = reviewData?.draft_status === "approved";

  return (
    <main className="page page--workspace" aria-label="Transcript">
      {isTranscribing && (
        <div className="ui-card ui-card--pad loading-card" role="status">
          <span className="ui-spinner" aria-hidden="true" />
          <div>
            <strong>Loading the transcript…</strong>
            <p className="ui-help">Speech-to-text and speaker attribution results are being read from the server.</p>
          </div>
        </div>
      )}

      {transcriptionError && (
        <Alert tone="danger" title="The transcript could not be loaded">
          <p>{transcriptionError}</p>
        </Alert>
      )}

      {transcriptionComplete && backendFileId && (
        <>
          <Alert tone="info" title="Check the transcript before extracting">
            <p>
              Speech-to-text can mishear names, dates and deadlines. If a word is wrong, use <strong>Correct</strong> on
              that line after listening to the recording. Corrections are saved with the original text and an audit entry.
            </p>
          </Alert>
          <TranscriptPanel
            meetingId={backendFileId}
            title="Indexed transcript"
            editable={!approved}
            lockedReason={approved ? "These minutes are approved. Reopen the review to correct the transcript." : ""}
          />
          <div className="page-footer-actions">
            <Button variant="primary" size="lg" iconRight="arrowRight" onClick={goToExtract}>
              Continue to Extract
            </Button>
          </div>
        </>
      )}
    </main>
  );
}
