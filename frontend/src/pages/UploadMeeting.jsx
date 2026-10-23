import { useMeetingFlow } from "../context/MeetingFlowContext";
import { PageHeader } from "../components/AppShell";
import ProcessingStages from "../components/ProcessingStages";
import { Alert, Badge, Button, Icon } from "../components/ui";

const ACCEPTED = ".mp4, .wav or .mp3";

export default function UploadMeeting() {
  const {
    selectedFile,
    error,
    isDragging,
    isUploading,
    fileInputRef,
    backendFileId,
    jobStage,
    jobError,
    jobStatus,
    handleFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    removeFile,
    goToTranscribe,
    loadExistingMeeting,
    currentStep,
    setMeetingMode,
  } = useMeetingFlow();

  if (currentStep !== 1) return null;

  const hasJob = Boolean(backendFileId) && !isUploading;
  const isDone = jobStage === "done" || jobStatus?.state === "done";
  const isPipelineRunning = hasJob && !isDone && !jobError;
  // Opened from the meeting list while processing: there is no local file.
  const watchingExisting = hasJob && !selectedFile;

  const openTranscript = () => {
    if (selectedFile) goToTranscribe();
    else loadExistingMeeting(backendFileId);
  };

  return (
    <main className="page ui-enter" aria-labelledby="upload-title">
      <PageHeader
        title={<span id="upload-title">{watchingExisting ? "Processing meeting" : "Upload a meeting recording"}</span>}
        description={
          watchingExisting
            ? "This meeting is still being processed on the server. The stages below update as the job advances."
            : "Meet2MoM transcribes the recording, attributes speakers, extracts decisions and action items, and checks each one against the transcript."
        }
        actions={
          !hasJob && !isUploading && (
            <Button icon="mic" onClick={() => setMeetingMode("live")}>Start Live Meeting instead</Button>
          )
        }
      />

      {!selectedFile && !watchingExisting && (
        <div
          className={`dropzone${isDragging ? " is-dragging" : ""}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          role="button"
          tabIndex={0}
          aria-describedby="dropzone-help"
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              fileInputRef.current?.click();
            }
          }}
        >
          <span className="dropzone__icon" aria-hidden="true"><Icon name="upload" /></span>
          <strong className="dropzone__title">Drop a recording here, or browse</strong>
          <span className="dropzone__help" id="dropzone-help">
            {ACCEPTED} · exported from Zoom, Teams, Google Meet or any recorder
          </span>
          <span className="ui-btn ui-btn--primary ui-btn--sm dropzone__button" aria-hidden="true">Choose file</span>
          <input
            ref={fileInputRef}
            type="file"
            id="file-upload"
            accept=".mp4,.wav,.mp3"
            onChange={handleFileChange}
            hidden
          />
        </div>
      )}

      {error && (
        <Alert tone="danger" title="Upload problem">
          <p>{error}</p>
        </Alert>
      )}

      {selectedFile && (
        <div className="ui-card file-card">
          <span className="file-card__icon" aria-hidden="true"><Icon name="file" /></span>
          <div className="file-card__info">
            <strong title={selectedFile.name}>{selectedFile.name}</strong>
            <span>{(selectedFile.size / (1024 * 1024)).toFixed(2)} MB</span>
          </div>
          {isUploading ? (
            <Badge tone="info" icon={null}><span className="ui-spinner file-card__spinner" aria-hidden="true" /> Uploading…</Badge>
          ) : backendFileId ? (
            <Badge tone="success">Uploaded</Badge>
          ) : null}
          {!isUploading && !isPipelineRunning && (
            <Button size="sm" variant="ghost" onClick={removeFile} aria-label="Remove selected file">Remove</Button>
          )}
        </div>
      )}

      {hasJob && (
        <ProcessingStages
          job={{ ...(jobStatus || {}), stage: jobStatus?.stage || jobStage, state: jobError ? "failed" : jobStatus?.state, error: jobError || jobStatus?.error }}
          title={selectedFile ? "Processing recording" : "Processing"}
          onRetry={jobError && selectedFile ? removeFile : undefined}
          retryLabel="Upload a different file"
        />
      )}

      {hasJob && (
        <div className="page-footer-actions">
          {/* Wait for the background job: continuing earlier would start a
              second, concurrent pipeline run for the same recording. */}
          <Button
            variant="primary"
            size="lg"
            onClick={openTranscript}
            disabled={isPipelineRunning || Boolean(jobError)}
            title={isPipelineRunning ? "Processing is still running" : undefined}
            iconRight={isPipelineRunning ? undefined : "arrowRight"}
            loading={isPipelineRunning}
          >
            {isPipelineRunning ? "Processing…" : "Open transcript"}
          </Button>
        </div>
      )}
    </main>
  );
}
