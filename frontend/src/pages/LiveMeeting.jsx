import { useRef, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function LiveMeeting() {
  // -------------------------------------------------------
  // SHARED MEETING CONTEXT
  // -------------------------------------------------------

  const {
    setBackendFileId,
    setMeetingId,
    setJobStatus,
    setJobStage,
  } = useMeetingFlow();

  // -------------------------------------------------------
  // REFS
  // -------------------------------------------------------

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);

  // -------------------------------------------------------
  // STATE
  // -------------------------------------------------------

  const [isRecording, setIsRecording] = useState(false);

  const [status, setStatus] = useState(
    "Ready to start live meeting"
  );

  const [elapsedSeconds, setElapsedSeconds] =
    useState(0);

  const [error, setError] = useState("");

  const [liveMeetingId, setLiveMeetingId] =
    useState(null);

  // -------------------------------------------------------
  // FORMAT TIMER
  // -------------------------------------------------------

  const formatTime = (seconds) => {
    const minutes = Math.floor(seconds / 60);

    const secs = seconds % 60;

    return `${String(minutes).padStart(
      2,
      "0"
    )}:${String(secs).padStart(2, "0")}`;
  };

  // -------------------------------------------------------
  // START TIMER
  // -------------------------------------------------------

  const startTimer = () => {
    setElapsedSeconds(0);

    timerRef.current = setInterval(() => {
      setElapsedSeconds(
        (previous) => previous + 1
      );
    }, 1000);
  };

  // -------------------------------------------------------
  // STOP TIMER
  // -------------------------------------------------------

  const stopTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);

      timerRef.current = null;
    }
  };

  // -------------------------------------------------------
  // START LIVE MEETING
  // -------------------------------------------------------

  const startRecording = async () => {
    try {
      setError("");
      setLiveMeetingId(null);

      setStatus(
        "Requesting microphone permission..."
      );

      // ---------------------------------------------------
      // GET MICROPHONE
      // ---------------------------------------------------

      const stream =
        await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });

      chunksRef.current = [];

      // ---------------------------------------------------
      // CHOOSE AUDIO FORMAT
      // ---------------------------------------------------

      let recorderOptions = {};

      if (
        MediaRecorder.isTypeSupported(
          "audio/webm;codecs=opus"
        )
      ) {
        recorderOptions = {
          mimeType: "audio/webm;codecs=opus",
        };
      }

      // ---------------------------------------------------
      // CREATE MEDIA RECORDER
      // ---------------------------------------------------

      const mediaRecorder =
        new MediaRecorder(
          stream,
          recorderOptions
        );

      mediaRecorderRef.current =
        mediaRecorder;

      // ---------------------------------------------------
      // AUDIO CHUNKS
      // ---------------------------------------------------

      mediaRecorder.ondataavailable = (
        event
      ) => {
        if (
          event.data &&
          event.data.size > 0
        ) {
          chunksRef.current.push(
            event.data
          );

          console.log(
            "Audio chunk received:",
            event.data.size,
            "bytes"
          );
        }
      };

      // ---------------------------------------------------
      // RECORDING STARTED
      // ---------------------------------------------------

      mediaRecorder.onstart = () => {
        console.log(
          "Live recording started."
        );

        setIsRecording(true);

        setStatus(
          "Live meeting recording..."
        );

        startTimer();
      };

      // ---------------------------------------------------
      // RECORDING ERROR
      // ---------------------------------------------------

      mediaRecorder.onerror = (
        event
      ) => {
        console.error(
          "MediaRecorder error:",
          event
        );

        stopTimer();

        setIsRecording(false);

        setError(
          "An error occurred while recording."
        );

        setStatus(
          "Live meeting recording failed."
        );
      };

      // ---------------------------------------------------
      // RECORDING STOPPED
      // ---------------------------------------------------

      mediaRecorder.onstop = async () => {
        console.log(
          "Recording stopped."
        );

        stopTimer();

        setIsRecording(false);

        setStatus(
          "Preparing live meeting audio..."
        );

        try {
          // ------------------------------------------------
          // CHECK AUDIO
          // ------------------------------------------------

          if (
            chunksRef.current.length === 0
          ) {
            throw new Error(
              "No audio was captured."
            );
          }

          // ------------------------------------------------
          // CREATE WEBM FILE
          // ------------------------------------------------

          const blob = new Blob(
            chunksRef.current,
            {
              type: "audio/webm",
            }
          );

          console.log(
            "Final recording size:",
            blob.size,
            "bytes"
          );

          if (blob.size === 0) {
            throw new Error(
              "Recorded audio file is empty."
            );
          }

          // ------------------------------------------------
          // CREATE FILE NAME
          // ------------------------------------------------

          const fileName =
            `live-meeting-${Date.now()}.webm`;

          // ------------------------------------------------
          // FORM DATA
          // ------------------------------------------------

          const formData =
            new FormData();

          formData.append(
            "file",
            blob,
            fileName
          );

          console.log(
            "Uploading:",
            fileName
          );

          setStatus(
            "Uploading live meeting..."
          );

          // ------------------------------------------------
          // UPLOAD TO FASTAPI
          // ------------------------------------------------

          const response = await fetch(
            "http://127.0.0.1:8000/api/upload",
            {
              method: "POST",
              body: formData,
            }
          );

          const responseText =
            await response.text();

          console.log(
            "UPLOAD STATUS:",
            response.status
          );

          console.log(
            "UPLOAD RESPONSE:",
            responseText
          );

          // 202 Accepted is SUCCESS.
          if (!response.ok) {
            throw new Error(
              `Upload failed (${response.status}): ${responseText}`
            );
          }

          // ------------------------------------------------
          // PARSE BACKEND RESPONSE
          // ------------------------------------------------

          let data;

          try {
            data = JSON.parse(
              responseText
            );
          } catch {
            throw new Error(
              "Backend returned invalid JSON."
            );
          }

          console.log(
            "LIVE MEETING DATA:",
            data
          );

          // ------------------------------------------------
          // GET MEETING ID
          // ------------------------------------------------

          const meetingId =
            data.meeting_id ||
            data.file_id;

          if (!meetingId) {
            throw new Error(
              "Backend did not return a meeting ID."
            );
          }

          // ------------------------------------------------
          // SAVE LOCALLY
          // ------------------------------------------------

          setLiveMeetingId(
            meetingId
          );

          // ------------------------------------------------
          // SAVE INTO EXISTING MEETING CONTEXT
          // ------------------------------------------------

          setMeetingId(
            meetingId
          );

          setBackendFileId(
            meetingId
          );

          setJobStage(
            "uploaded"
          );

          setJobStatus({
            state:
              data.status ||
              "queued",

            stage:
              "uploaded",

            progress: 0,

            error: null,
          });

          console.log(
            "LIVE MEETING ID SAVED:",
            meetingId
          );

          // ------------------------------------------------
          // SUCCESS
          // ------------------------------------------------

          setError("");

          setStatus(
            "Live meeting uploaded successfully. Ready for processing ✅"
          );

        } catch (uploadError) {
          console.error(
            "LIVE UPLOAD ERROR:",
            uploadError
          );

          setError(
            uploadError?.message ||
              "Live meeting upload failed."
          );

          setStatus(
            "Failed to upload live meeting."
          );
        }
      };

      // ---------------------------------------------------
      // START RECORDING
      // ---------------------------------------------------

      /*
       * Generate an audio chunk approximately
       * every second.
       *
       * Later these chunks can be used for
       * near-real-time transcription.
       */

      mediaRecorder.start(1000);

    } catch (startError) {
      console.error(
        "MICROPHONE ERROR:",
        startError
      );

      stopTimer();

      setIsRecording(false);

      setError(
        startError?.message ||
          "Microphone unavailable."
      );

      setStatus(
        "Microphone permission denied or microphone unavailable."
      );
    }
  };

  // -------------------------------------------------------
  // STOP LIVE MEETING
  // -------------------------------------------------------

  const stopRecording = () => {
    const recorder =
      mediaRecorderRef.current;

    if (
      !recorder ||
      recorder.state === "inactive"
    ) {
      return;
    }

    console.log(
      "Ending live meeting..."
    );

    setStatus(
      "Ending live meeting..."
    );

    setIsRecording(false);

    stopTimer();

    // This triggers mediaRecorder.onstop
    recorder.stop();

    // Stop microphone access
    recorder.stream
      .getTracks()
      .forEach((track) => {
        track.stop();
      });
  };

  // -------------------------------------------------------
  // UI
  // -------------------------------------------------------

  return (
    <div className="live-meeting-page">

      <div className="live-meeting-card">

        {/* LIVE STATUS */}

        <div className="live-indicator">

          <span
            className={
              isRecording
                ? "live-dot recording"
                : "live-dot"
            }
          />

          {isRecording
            ? "LIVE MEETING"
            : "LIVE MEETING READY"}

        </div>

        {/* TITLE */}

        <h1>
          Live Meeting Capture
        </h1>

        <p>
          Start a live meeting and Meet2MoM
          will capture the meeting audio for
          automatic transcription and MoM
          generation.
        </p>

        {/* RECORDING TIMER */}

        {isRecording && (
          <div
            style={{
              fontSize: "32px",
              fontWeight: "700",
              marginTop: "20px",
              marginBottom: "10px",
            }}
          >
            {formatTime(
              elapsedSeconds
            )}
          </div>
        )}

        {/* STATUS MESSAGE */}

        <div className="live-status">
          {status}
        </div>

        {/* ERROR */}

        {error && (
          <div
            style={{
              color: "#dc2626",
              fontSize: "14px",
              marginTop: "10px",
              marginBottom: "15px",
            }}
          >
            {error}
          </div>
        )}

        {/* START / STOP BUTTON */}

        {!isRecording ? (
          <button
            className="start-live-btn"
            onClick={
              startRecording
            }
            disabled={
              status.includes(
                "Uploading"
              )
            }
          >
            🎙️ Start Live Meeting
          </button>
        ) : (
          <button
            className="stop-live-btn"
            onClick={
              stopRecording
            }
          >
            ⏹ End Meeting
          </button>
        )}

        {/* SUCCESS INFORMATION */}

        {liveMeetingId && (
          <div
            style={{
              marginTop: "20px",
              padding: "12px",
              borderRadius: "8px",
              background:
                "rgba(34, 197, 94, 0.08)",
              fontSize: "14px",
            }}
          >
            <strong>
              Meeting captured successfully
            </strong>

            <div
              style={{
                marginTop: "5px",
                opacity: 0.7,
                wordBreak: "break-all",
              }}
            >
              Meeting ID:{" "}
              {liveMeetingId}
            </div>
          </div>
        )}

      </div>

    </div>
  );
}