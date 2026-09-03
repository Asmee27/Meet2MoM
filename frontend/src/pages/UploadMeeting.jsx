import { useState } from "react";

import {
  Play,
  CheckCircle2,
} from "lucide-react";

import UploadCard from "../components/UploadCard";
import ProcessingStep from "../components/ProcessingStep";
import { uploadMeeting } from "../services/api";

function UploadMeeting() {
  const [selectedFile, setSelectedFile] =
    useState(null);

  const [processing, setProcessing] =
    useState(false);

  const [finished, setFinished] =
    useState(false);

  const handleProcess = async () => {
    if (!selectedFile) return;

    setProcessing(true);
    setFinished(false);

    await uploadMeeting(selectedFile);

    setTimeout(() => {
      setProcessing(false);
      setFinished(true);
    }, 1800);
  };

  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            NEW MEETING
          </span>

          <h1>Upload meeting recording</h1>

          <p>
            Upload your meeting and let the
            intelligence pipeline handle the
            rest.
          </p>
        </div>
      </div>

      <div className="upload-layout">
        <section className="panel">
          <h2>Meeting Recording</h2>

          <p className="panel-description">
            Audio and video recordings are
            supported.
          </p>

          <UploadCard
            selectedFile={selectedFile}
            setSelectedFile={setSelectedFile}
          />

          <button
            className="primary-button process-button"
            disabled={
              !selectedFile || processing
            }
            onClick={handleProcess}
          >
            <Play size={18} />

            {processing
              ? "Processing..."
              : "Process Meeting"}
          </button>

          {finished && (
            <div className="success-message">
              <CheckCircle2 size={19} />
              Meeting processing initiated
              successfully.
            </div>
          )}
        </section>

        <section className="panel">
          <span className="eyebrow">
            PIPELINE
          </span>

          <h2>AI Processing</h2>

          <div className="processing-list">
            <ProcessingStep
              title="Audio Processing"
              description="Normalize and analyse recording"
              status={
                processing || finished
                  ? "completed"
                  : "pending"
              }
            />

            <ProcessingStep
              title="Speech Recognition"
              description="Multilingual transcription"
              status={
                processing
                  ? "processing"
                  : finished
                    ? "completed"
                    : "pending"
              }
            />

            <ProcessingStep
              title="Speaker Diarization"
              description="Detect who spoke when"
              status={
                finished
                  ? "completed"
                  : "pending"
              }
            />

            <ProcessingStep
              title="Meeting Intelligence"
              description="Topics, decisions and tasks"
              status={
                finished
                  ? "completed"
                  : "pending"
              }
            />

            <ProcessingStep
              title="Minutes Generation"
              description="Generate structured MoM"
              status={
                finished
                  ? "completed"
                  : "pending"
              }
            />
          </div>
        </section>
      </div>
    </div>
  );
}

export default UploadMeeting;