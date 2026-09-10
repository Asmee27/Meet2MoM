// src/services/api.js
// Single source of truth for all backend API calls.

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function checkBackendHealth() {
  for (const endpoint of ["/api/health", "/health", "/"]) {
    try {
      const res = await fetch(`${API_BASE_URL}${endpoint}`);
      if (res.ok) return true;
    } catch {
      // try next
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Upload  →  202 { meeting_id, status: "queued" }
// ---------------------------------------------------------------------------

export async function uploadMeeting(file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE_URL}/api/upload`, {
    method: "POST",
    body: formData,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Upload failed.");
  return data; // { meeting_id, status, file_id, ... }
}

// ---------------------------------------------------------------------------
// Status  →  { meeting_id, stage, progress, state, error, updated_at }
// ---------------------------------------------------------------------------

export async function getStatus(meetingId) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/status`
  );
  if (!res.ok) {
    // Fall back to the legacy pipeline_status endpoint
    const legacy = await fetch(`${API_BASE_URL}/api/status/${meetingId}`);
    if (!legacy.ok) return null;
    return legacy.json();
  }
  return res.json();
}

// Previously processed meetings with indexed transcripts
export async function listMeetings() {
  const res = await fetch(`${API_BASE_URL}/api/meetings`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to list meetings.");
  return data;
}

// ---------------------------------------------------------------------------
// Transcript  →  list of Utterance objects from DB
// ---------------------------------------------------------------------------

export async function getTranscript(meetingId) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/transcript`
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Transcript not available.");
  return data; // list[Utterance]
}

// ---------------------------------------------------------------------------
// Legacy helpers (used by existing context functions)
// ---------------------------------------------------------------------------

export async function extractAudio(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/extract-audio/${meetingId}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Audio extraction failed.");
  return data;
}

export async function transcribeMeeting(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/transcribe/${meetingId}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Transcription failed.");
  return data;
}

export async function extractIntelligence(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/extract/${meetingId}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Extraction failed.");
  return data;
}

export async function verifyMeeting(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/verify/${meetingId}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Verification failed.");
  return data;
}

export async function generateMom(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/generate-mom/${meetingId}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "MoM generation failed.");
  return data;
}
