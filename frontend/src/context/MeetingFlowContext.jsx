import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  API_BASE_URL,
  uploadMeeting,
  getStatus,
  getTranscript,
} from "../services/api";

const MeetingFlowContext = createContext(null);

export function MeetingFlowProvider({ children }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [currentStep, setCurrentStep] = useState(1);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef(null);

  // REAL BACKEND FILE / TRANSCRIPT STATE
  const [backendFileId, setBackendFileId] = useState(null);
  // meetingId is the canonical identifier returned by POST /upload
  const [meetingId, setMeetingId] = useState(null);
  const [transcriptSegments, setTranscriptSegments] = useState([]);
  const [transcriptText, setTranscriptText] = useState("");
  const [detectedLanguage, setDetectedLanguage] = useState("");
  const [transcriptionError, setTranscriptionError] = useState("");

  const [transcriptionProgress, setTranscriptionProgress] = useState(0);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcriptionComplete, setTranscriptionComplete] = useState(false);

  // JOB TRACKING STATE (async pipeline)
  const [jobStatus, setJobStatus] = useState(null);   // full JobStatus object
  const [jobStage, setJobStage] = useState("uploaded"); // current stage name
  const [jobError, setJobError] = useState("");
  const _pollTimer = useRef(null);
  const _pollInterval = useRef(2000); // ms; grows to 5000 with backoff

  // REAL BACKEND EXTRACTION STATE
  const [extractedData, setExtractedData] = useState({
    decisions: [],
    actionItems: [],
  });
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState("");

  const [editingDecisions, setEditingDecisions] = useState(false);
  const [editingActions, setEditingActions] = useState(false);

  const [visibleDecisions, setVisibleDecisions] = useState(0);
  const [visibleActions, setVisibleActions] = useState(0);
  const [extractAnimationComplete, setExtractAnimationComplete] = useState(false);

  const [verificationStage, setVerificationStage] = useState("automatic");
  const [verificationProgress, setVerificationProgress] = useState(0);
  const [automaticCheckComplete, setAutomaticCheckComplete] = useState(false);

  // REAL BACKEND VERIFICATION STATE
  const [verificationData, setVerificationData] = useState(null);
  const [isVerifying, setIsVerifying] = useState(false);
  const [verificationError, setVerificationError] = useState("");
  const [exportStatus, setExportStatus] = useState({ pdf: false, word: false, email: false });

  // REAL BACKEND MOM / PDF STATE
  const [momData, setMomData] = useState(null);
  const [isGeneratingMom, setIsGeneratingMom] = useState(false);
  const [momError, setMomError] = useState("");
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);

  /* =========================
     STATUS POLLING
  ========================= */

  const _stopPolling = () => {
    if (_pollTimer.current) {
      clearTimeout(_pollTimer.current);
      _pollTimer.current = null;
    }
    _pollInterval.current = 2000;
  };

  const _startPolling = (mid) => {
    _stopPolling();
    _pollInterval.current = 2000;

    const poll = async () => {
      try {
        const status = await getStatus(mid);
        if (status) {
          setJobStatus(status);
          setJobStage(status.stage || "uploaded");
          if (status.state === "failed") {
            setJobError(status.error || "Pipeline failed.");
            _stopPolling();
            return;
          }
          if (status.state === "done" || status.stage === "done") {
            _stopPolling();
            return;
          }
        }
      } catch {
        // network hiccup — keep polling
      }
      // backoff: grow to 5 s
      _pollInterval.current = Math.min(_pollInterval.current * 1.4, 5000);
      _pollTimer.current = setTimeout(poll, _pollInterval.current);
    };

    _pollTimer.current = setTimeout(poll, _pollInterval.current);
  };

  const loadExistingMeeting = useCallback(async (id) => {
    if (!id) return;

    _stopPolling();
    setError("");
    setMeetingId(id);
    setBackendFileId(id);
    setSelectedFile(null);
    setJobError("");
    setJobStatus({ state: "done", stage: "done", progress: 1, error: null });
    setJobStage("done");
    setIsUploading(false);
    setTranscriptionError("");
    setIsTranscribing(false);

    try {
      const transcript = await getTranscript(id);
      const segments = Array.isArray(transcript) ? transcript : [];
      setTranscriptSegments(segments);
      setTranscriptText(segments.map((segment) => segment.text || "").join(" ").trim());
      setDetectedLanguage("");
      setTranscriptionProgress(100);
      setTranscriptionComplete(true);
      setCurrentStep(2);
    } catch (loadError) {
      console.error("Failed to load meeting", id, loadError);
      setTranscriptionComplete(false);
      setJobError(String(loadError));
      setTranscriptionError(
        loadError?.message || "Unable to load the existing meeting."
      );
    }
  }, []);

  useEffect(() => {
    const existingMeetingId = new URLSearchParams(window.location.search).get(
      "meeting"
    );
    if (existingMeetingId) {
      loadExistingMeeting(existingMeetingId);
    }
  }, [loadExistingMeeting]);

  /* =========================
     FILE VALIDATION
  ========================= */

  const validateFile = (file) => {
    if (!file) return;

    const allowedTypes = [
      "audio/mpeg",
      "audio/wav",
      "audio/wave",
      "video/mp4",
    ];

    const allowedExtensions = [
      ".mp4",
      ".wav",
      ".mp3",
    ];

    const fileName = file.name.toLowerCase();

    const validFile =
      allowedTypes.includes(file.type) ||
      allowedExtensions.some((extension) =>
        fileName.endsWith(extension)
      );

    if (!validFile) {
      setError("Please select an MP4, WAV, or MP3 file.");
      setSelectedFile(null);
      setUploadProgress(0);
      setIsUploading(false);
      return;
    }

    setError("");
    setSelectedFile(file);
    setUploadProgress(0);
    setIsUploading(true);
    setJobStatus(null);
    setJobStage("uploaded");
    setJobError("");

    // REAL BACKEND UPLOAD using the api.js helper
    uploadMeeting(file)
      .then((response) => {
        setUploadProgress(100);
        setIsUploading(false);
        setError("");
        const mid = response.meeting_id || response.file_id;
        if (mid) {
          setMeetingId(mid);
          setBackendFileId(mid);
          _startPolling(mid);
        } else {
          setError("Upload succeeded, but the backend did not return a meeting ID.");
        }
      })
      .catch((err) => {
        setError(err?.message || "Upload failed. Please try again.");
        setIsUploading(false);
        setUploadProgress(0);
      });
  };


  const handleFileChange = (event) => {
    const file = event.target.files[0];
    validateFile(file);
  };

  /* =========================
     DRAG & DROP
  ========================= */

  const handleDragOver = (event) => {
    event.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (event) => {
    event.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setIsDragging(false);

    const file = event.dataTransfer.files[0];
    validateFile(file);
  };

  /* =========================
     REMOVE FILE
  ========================= */

  const removeFile = () => {
    _stopPolling();
    setSelectedFile(null);
    setMeetingId(null);
    setBackendFileId(null);
    setJobStatus(null);
    setJobStage("uploaded");
    setJobError("");
    setTranscriptSegments([]);
    setTranscriptText("");
    setDetectedLanguage("");
    setTranscriptionError("");
    setVerificationData(null);
    setVerificationError("");
    setIsVerifying(false);
    setAutomaticCheckComplete(false);
    setVerificationProgress(0);
    setError("");
    setUploadProgress(0);
    setIsUploading(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  /* =========================
     NAVIGATION
  ========================= */

  const goBack = () => {
    if (
      isUploading ||
      isTranscribing ||
      isExtracting ||
      isVerifying ||
      isGeneratingMom ||
      isDownloadingPdf
    ) {
      return;
    }

    setCurrentStep((previousStep) => Math.max(1, previousStep - 1));
  };

  const goToTranscribe = async () => {
    if (!selectedFile || !backendFileId || isUploading) {
      return;
    }

    setCurrentStep(2);
    setIsTranscribing(true);
    setTranscriptionProgress(5);
    setTranscriptionComplete(false);
    setTranscriptionError("");
    setTranscriptSegments([]);
    setTranscriptText("");
    setDetectedLanguage("");

    try {
      // ------------------------------------------------------
      // REAL BACKEND AUDIO EXTRACTION
      // ------------------------------------------------------
      setTranscriptionProgress(15);

      const extractionResponse = await fetch(
        `${API_BASE_URL}/api/extract-audio/${backendFileId}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
          },
        }
      );

      if (!extractionResponse.ok) {
        let message = "Audio extraction failed.";

        try {
          const response = await extractionResponse.json();
          if (response.detail) {
            message = response.detail;
          }
        } catch {
          // Keep default message.
        }

        throw new Error(message);
      }

      // ------------------------------------------------------
      // REAL BACKEND SPEECH-TO-TEXT
      // ------------------------------------------------------
      setTranscriptionProgress(35);

      const transcriptionResponse = await fetch(
        `${API_BASE_URL}/api/transcribe/${backendFileId}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
          },
        }
      );

      if (!transcriptionResponse.ok) {
        let message = "Speech-to-text transcription failed.";

        try {
          const response = await transcriptionResponse.json();
          if (response.detail) {
            message = response.detail;
          }
        } catch {
          // Keep default message.
        }

        throw new Error(message);
      }

      const transcriptionData = await transcriptionResponse.json();

      setTranscriptSegments(
        Array.isArray(transcriptionData.segments)
          ? transcriptionData.segments
          : []
      );

      setTranscriptText(
        transcriptionData.full_text || ""
      );

      setDetectedLanguage(
        transcriptionData.language || ""
      );

      setTranscriptionProgress(100);
      setIsTranscribing(false);
      setTranscriptionComplete(true);

    } catch (error) {
      console.error("Real transcription pipeline failed:", error);

      setIsTranscribing(false);
      setTranscriptionComplete(false);
      setTranscriptionError(
        error?.message ||
        "Unable to transcribe the meeting. Please make sure the backend is running."
      );
    }
  };

  const goToExtract = async () => {
    if (!transcriptionComplete || !backendFileId) return;

    setCurrentStep(3);
    setIsExtracting(true);
    setExtractionError("");
    setExtractedData({ decisions: [], actionItems: [] });
    setVisibleDecisions(0);
    setVisibleActions(0);
    setExtractAnimationComplete(false);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/extract/${backendFileId}`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail || "Unable to extract decisions and action items."
        );
      }

      setExtractedData({
        decisions: Array.isArray(data.decisions)
          ? data.decisions.map((item) =>
              typeof item === "string" ? item : item.text
            )
          : [],
        actionItems: Array.isArray(data.action_items)
          ? data.action_items.map((item) => ({
              task: item.task || "",
              owner: item.owner || "Not specified",
              dueDate: item.dueDate || "Not specified",
              sourceStart: item.source_start,
              sourceEnd: item.source_end,
              sourceText: item.source_text || "",
            }))
          : [],
      });
    } catch (error) {
      console.error("Real extraction failed:", error);
      setExtractionError(
        error?.message ||
        "Unable to extract meeting decisions and action items."
      );
    } finally {
      setIsExtracting(false);
    }
  };

  const goToVerify = async () => {
    if (!backendFileId || isVerifying) return;

    setCurrentStep(4);
    setVerificationStage("automatic");
    setVerificationProgress(15);
    setAutomaticCheckComplete(false);
    setVerificationData(null);
    setVerificationError("");
    setIsVerifying(true);
    setEditingDecisions(false);
    setEditingActions(false);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/verify/${backendFileId}`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail || "Unable to verify meeting intelligence."
        );
      }

      setVerificationData(data);

      // Replace the extraction-only display data with evidence-grounded data.
      setExtractedData({
        decisions: Array.isArray(data.verified_decisions)
          ? data.verified_decisions
              .filter((item) =>
                ["verified", "verified_with_warning"].includes(
                  item?.verification_status
                )
              )
              .map((item) => item?.text || "")
          : [],
        actionItems: Array.isArray(data.verified_action_items)
          ? data.verified_action_items
              .filter((item) =>
                ["verified", "verified_with_warning"].includes(
                  item?.verification_status
                )
              )
              .map((item) => ({
                task: item?.task || "",
                owner: item?.owner || "Not specified",
                dueDate:
                  item?.dueDate ||
                  item?.due_date ||
                  item?.deadline_text ||
                  "Not specified",
                sourceStart: item?.source_start,
                sourceEnd: item?.source_end,
                sourceText: item?.source_text || "",
              }))
          : [],
      });

      setVerificationProgress(100);
      setAutomaticCheckComplete(true);
    } catch (error) {
      console.error("Real verification failed:", error);
      setVerificationProgress(0);
      setAutomaticCheckComplete(false);
      setVerificationError(
        error?.message ||
          "Unable to verify the meeting against transcript evidence."
      );
    } finally {
      setIsVerifying(false);
    }
  };

  const goToExport = async () => {
    if (!backendFileId) {
      setMomError("Meeting file ID is missing. Please start the meeting again.");
      return;
    }

    setCurrentStep(5);
    setIsGeneratingMom(true);
    setMomError("");
    setMomData(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/generate-mom/${backendFileId}`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail || "Unable to generate the Minutes of Meeting."
        );
      }

      setMomData(data?.mom || null);
    } catch (error) {
      console.error("MoM generation failed:", error);
      setMomError(
        error?.message ||
        "Unable to generate the Minutes of Meeting. Please make sure the backend is running."
      );
    } finally {
      setIsGeneratingMom(false);
    }
  };

  /* =========================
     REAL TRANSCRIPTION
  ========================= */

  // Step 2 no longer uses a hardcoded transcription timer.
  // The page is driven by the real backend response above.

  /* =========================
     EXTRACTION REVEAL ANIMATION
  ========================= */

  useEffect(() => {
    if (currentStep !== 3 || isExtracting || extractionError) {
      return;
    }

    if (
      extractedData.decisions.length === 0 &&
      extractedData.actionItems.length === 0
    ) {
      setExtractAnimationComplete(false);
      return;
    }

    setVisibleDecisions(0);
    setVisibleActions(0);
    setExtractAnimationComplete(false);

    let decisionIndex = 0;
    let actionIndex = 0;
    let actionTimer;
    let actionStartTimer;

    const decisionTimer = setInterval(() => {
      decisionIndex += 1;
      setVisibleDecisions(decisionIndex);

      if (decisionIndex >= extractedData.decisions.length) {
        clearInterval(decisionTimer);

        actionStartTimer = setTimeout(() => {
          if (extractedData.actionItems.length === 0) {
            setExtractAnimationComplete(true);
            return;
          }

          actionTimer = setInterval(() => {
            actionIndex += 1;
            setVisibleActions(actionIndex);

            if (actionIndex >= extractedData.actionItems.length) {
              clearInterval(actionTimer);
              setExtractAnimationComplete(true);
            }
          }, 650);
        }, 350);
      }
    }, 650);

    return () => {
      clearInterval(decisionTimer);
      if (actionTimer) clearInterval(actionTimer);
      if (actionStartTimer) clearTimeout(actionStartTimer);
    };
  }, [
    currentStep,
    isExtracting,
    extractionError,
    extractedData.decisions.length,
    extractedData.actionItems.length,
  ]);

  /* =========================
     REAL FAITHFULNESS CHECK
  ========================= */

  // Step 4 is driven by POST /api/verify/{file_id} in goToVerify.
  // No simulated timer is used.

  /* =========================
     UPDATE DECISION
  ========================= */

  const updateDecision = (index, value) => {
    setExtractedData((previousData) => {
      const updatedDecisions = [
        ...previousData.decisions,
      ];

      updatedDecisions[index] = value;

      return {
        ...previousData,
        decisions: updatedDecisions,
      };
    });
  };

  /* =========================
     UPDATE ACTION ITEM
  ========================= */

  const updateActionItem = (
    index,
    field,
    value
  ) => {
    setExtractedData((previousData) => {
      const updatedActions = [
        ...previousData.actionItems,
      ];

      updatedActions[index] = {
        ...updatedActions[index],
        [field]: value,
      };

      return {
        ...previousData,
        actionItems: updatedActions,
      };
    });
  };

  /* =========================
     EXPORT FUNCTIONS
  ========================= */

  const downloadTextFile = () => {
    const content = `
MEETING MINUTES
==============

Meeting Recording:
${selectedFile?.name || "Meeting Recording"}

DECISIONS
---------

${extractedData.decisions
  .map((decision, index) =>
    `${index + 1}. ${decision}`
  )
  .join("\n")}

ACTION ITEMS
------------

${extractedData.actionItems
  .map(
    (item, index) =>
      `${index + 1}. ${item.task}
   Owner: ${item.owner}
   Due Date: ${item.dueDate}`
  )
  .join("\n\n")}

Generated by Meet2MoM
`;

    const blob = new Blob(
      [content],
      { type: "text/plain" }
    );

    const url = URL.createObjectURL(blob);

    const link = document.createElement("a");

    link.href = url;
    link.download = "Meet2MoM-Meeting-Minutes.txt";

    document.body.appendChild(link);

    link.click();

    document.body.removeChild(link);

    URL.revokeObjectURL(url);
  };

  const downloadMeetingPdf = async () => {
    if (!backendFileId || isDownloadingPdf) return;

    setIsDownloadingPdf(true);
    setMomError("");

    try {
      // Ensure the latest server-side MoM/PDF exists before downloading it.
      const generateResponse = await fetch(
        `${API_BASE_URL}/api/generate-mom/${backendFileId}`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
        }
      );

      const generateData = await generateResponse.json();

      if (!generateResponse.ok) {
        throw new Error(
          generateData?.detail || "Unable to prepare the PDF."
        );
      }

      if (generateData?.mom) {
        setMomData(generateData.mom);
      }

      const pdfResponse = await fetch(
        `${API_BASE_URL}/api/download-mom/${backendFileId}`,
        {
          method: "GET",
          headers: { Accept: "application/pdf" },
        }
      );

      if (!pdfResponse.ok) {
        let message = "Unable to download the PDF.";
        try {
          const errorData = await pdfResponse.json();
          message = errorData?.detail || message;
        } catch {
          // The backend may return a non-JSON error body.
        }
        throw new Error(message);
      }

      const blob = await pdfResponse.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = url;
      link.download = `Meet2MoM-${selectedFile?.name?.replace(/\.[^/.]+$/, "") || "Meeting-Minutes"}.pdf`;

      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      setExportStatus((previous) => ({
        ...previous,
        pdf: true,
      }));
    } catch (error) {
      console.error("PDF download failed:", error);
      setMomError(
        error?.message ||
        "Unable to download the meeting minutes PDF."
      );
    } finally {
      setIsDownloadingPdf(false);
    }
  };

  const exportWordDocument = () => {
    const html = `
      <html>
        <head><meta charset="utf-8"><title>Meet2MoM Meeting Minutes</title></head>
        <body style="font-family: Arial, sans-serif;">
          <h1>Project Development Meeting</h1>
          <h2>Meeting Summary</h2>
          <p>The team discussed the current progress of the project and upcoming deadlines. The development team has completed the first phase and the project is ready to move into the testing phase.</p>
          <h2>Decisions</h2>
          ${extractedData.decisions.map((decision, index) => `<p><strong>${index + 1}.</strong> ${typeof decision === "string" ? decision : decision?.text || ""}</p>`).join("")}
          <h2>Action Items</h2>
          <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;">
            <tr><th>Task</th><th>Owner</th><th>Due Date</th></tr>
            ${extractedData.actionItems.map(item => `<tr><td>${item.task}</td><td>${item.owner}</td><td>${item.dueDate}</td></tr>`).join("")}
          </table>
          <p>Generated by Meet2MoM</p>
        </body>
      </html>
    `;

    const blob = new Blob([html], { type: "application/msword" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "Meet2MoM-Meeting-Minutes.doc";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    setExportStatus((previous) => ({ ...previous, word: true }));
  };

  const sendMeetingEmail = () => {
    const body = `Meeting Minutes - Project Development Meeting\n\nDecisions:\n${extractedData.decisions.map((d, i) => `${i + 1}. ${typeof d === "string" ? d : d?.text || ""}`).join("\n")}\n\nAction Items:\n${extractedData.actionItems.map((item, i) => `${i + 1}. ${item.task} — ${item.owner} — ${item.dueDate}`).join("\n")}`;
    window.location.href = `mailto:?subject=${encodeURIComponent("Meet2MoM - Meeting Minutes")}&body=${encodeURIComponent(body)}`;
    setExportStatus((previous) => ({ ...previous, email: true }));
  };


  const value = {
    selectedFile,
    setSelectedFile,
    error,
    setError,
    isDragging,
    setIsDragging,
    currentStep,
    setCurrentStep,
    uploadProgress,
    setUploadProgress,
    isUploading,
    setIsUploading,
    fileInputRef,
    backendFileId,
    setBackendFileId,
    meetingId,
    setMeetingId,
    jobStatus,
    setJobStatus,
    jobStage,
    setJobStage,
    jobError,
    setJobError,
    transcriptSegments,
    setTranscriptSegments,
    transcriptText,
    setTranscriptText,
    detectedLanguage,
    setDetectedLanguage,
    transcriptionError,
    setTranscriptionError,
    transcriptionProgress,
    setTranscriptionProgress,
    isTranscribing,
    setIsTranscribing,
    transcriptionComplete,
    setTranscriptionComplete,
    extractedData,
    setExtractedData,
    isExtracting,
    setIsExtracting,
    extractionError,
    setExtractionError,
    editingDecisions,
    setEditingDecisions,
    editingActions,
    setEditingActions,
    visibleDecisions,
    setVisibleDecisions,
    visibleActions,
    setVisibleActions,
    extractAnimationComplete,
    setExtractAnimationComplete,
    verificationStage,
    setVerificationStage,
    verificationProgress,
    setVerificationProgress,
    automaticCheckComplete,
    setAutomaticCheckComplete,
    verificationData,
    setVerificationData,
    isVerifying,
    setIsVerifying,
    verificationError,
    setVerificationError,
    exportStatus,
    setExportStatus,
    momData,
    setMomData,
    isGeneratingMom,
    setIsGeneratingMom,
    momError,
    setMomError,
    isDownloadingPdf,
    setIsDownloadingPdf,
    validateFile,
    handleFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    removeFile,
    goBack,
    goToTranscribe,
    goToExtract,
    goToVerify,
    goToExport,
    updateDecision,
    updateActionItem,
    downloadTextFile,
    downloadMeetingPdf,
    exportWordDocument,
    sendMeetingEmail,
    loadExistingMeeting,
  };

  return (
    <MeetingFlowContext.Provider value={value}>
      {children}
    </MeetingFlowContext.Provider>
  );
}

export function useMeetingFlow() {
  const context = useContext(MeetingFlowContext);

  if (!context) {
    throw new Error("useMeetingFlow must be used inside MeetingFlowProvider");
  }

  return context;
}
