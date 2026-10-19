// src/services/api.js
// Single source of truth for all backend API calls.

const defaultBaseUrl =
  typeof window !== "undefined" && window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : "http://127.0.0.1:8000";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || defaultBaseUrl;

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

// ---------------------------------------------------------------------------
// Claims + persisted verification (Phase 1)
// ---------------------------------------------------------------------------

export async function getClaims(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/meetings/${meetingId}/claims`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to load claims.");
  return data;
}

// action: "accept" | "reject". Returns the updated claims payload, with the
// reviewed claim under `claim`.
export async function reviewEvidenceSuggestion(meetingId, claimId, action) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/claims/${encodeURIComponent(claimId)}/evidence-suggestion`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ action }),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to save the evidence decision.");
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

// ---------------------------------------------------------------------------
// Phase 3: Review Drafts, Item Editing, Approvals, and Audit Log
// ---------------------------------------------------------------------------

export async function getReview(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/meetings/${meetingId}/review`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to load review draft.");
  return data;
}

export async function updateReviewItem(meetingId, itemId, payload) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/review/items/${encodeURIComponent(itemId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to update review item.");
  return data;
}

export async function addReviewItem(meetingId, payload) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/review/items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to add review item.");
  return data;
}

export async function approveReview(meetingId, reviewer, notes = "") {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ reviewer, notes }),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Approval failed.");
  return data;
}

export async function reopenReview(meetingId, reviewer, reason) {
  const res = await fetch(
    `${API_BASE_URL}/api/meetings/${meetingId}/reopen`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ reviewer, reason }),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Reopening review failed.");
  return data;
}

export async function getAuditLog(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/meetings/${meetingId}/audit-log`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to load audit history.");
  return data.audit_log || [];
}

export async function exportApprovedJson(meetingId) {
  const res = await fetch(`${API_BASE_URL}/api/meetings/${meetingId}/export/json`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to export approved JSON.");
  return data;
}

export function getAudioUrl(meetingId) {
  return `${API_BASE_URL}/api/meetings/${meetingId}/audio`;
}

// ---------------------------------------------------------------------------
// Phase 4: LiveKit Live Meeting API
// ---------------------------------------------------------------------------

export async function getLiveConfig() {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}/api/live/config`);
  } catch {
    throw new Error(`Cannot connect to backend server at ${API_BASE_URL}. Please ensure the FastAPI backend is running.`);
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Unable to fetch LiveKit configuration.");
  return data;
}

export async function createLiveRoom(payload = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}/api/live/rooms`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error(`Cannot connect to backend server at ${API_BASE_URL}. Please ensure the FastAPI backend is running.`);
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Failed to create LiveKit room.");
  return data;
}

export async function getLiveToken(payload) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}/api/live/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error(`Cannot connect to backend server at ${API_BASE_URL}. Please ensure the FastAPI backend is running.`);
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Failed to generate LiveKit join token.");
  return data;
}

export async function getLiveRoomStatus(roomName) {
  const res = await fetch(`${API_BASE_URL}/api/live/rooms/${encodeURIComponent(roomName)}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.detail || "Failed to fetch room status.");
  return data;
}

// Live-meeting mutations authenticate with the participant's own LiveKit token,
// so the server can verify who is changing consent or controlling recording.
function liveAuthHeaders(token, extra = {}) {
  return {
    Accept: "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

function liveErrorMessage(data, fallback) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  return fallback;
}

async function liveRequest(path, { method = "GET", token, body, fallback }) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: liveAuthHeaders(token, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(`Cannot connect to backend server at ${API_BASE_URL}. Please ensure the FastAPI backend is running.`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(liveErrorMessage(data, fallback));
    error.status = res.status;
    error.detail = data?.detail;
    throw error;
  }
  return data;
}

export async function endLiveRoom(roomName, token) {
  return liveRequest(`/api/live/rooms/${encodeURIComponent(roomName)}/end`, {
    method: "POST",
    token,
    fallback: "Failed to end LiveKit room.",
  });
}

export async function updateLiveConsent(roomName, identity, consented, token) {
  return liveRequest(`/api/live/rooms/${encodeURIComponent(roomName)}/consent`, {
    method: "POST",
    token,
    body: { identity, consented },
    fallback: "Failed to update participant consent.",
  });
}

// Phase 4.2: server-side recording state and controls

export async function getLiveRecording(meetingId, token) {
  return liveRequest(`/api/live/meetings/${encodeURIComponent(meetingId)}/recording`, {
    token,
    fallback: "Failed to fetch recording state.",
  });
}

export async function startLiveRecording(meetingId, token) {
  return liveRequest(`/api/live/meetings/${encodeURIComponent(meetingId)}/recording/start`, {
    method: "POST",
    token,
    fallback: "Failed to start recording.",
  });
}

export async function stopLiveRecording(meetingId, token) {
  return liveRequest(`/api/live/meetings/${encodeURIComponent(meetingId)}/recording/stop`, {
    method: "POST",
    token,
    fallback: "Failed to stop recording.",
  });
}

export async function processLiveRecording(meetingId, token, { retry = false } = {}) {
  const query = retry ? "?retry=true" : "";
  return liveRequest(`/api/live/meetings/${encodeURIComponent(meetingId)}/recording/process${query}`, {
    method: "POST",
    token,
    fallback: "Failed to start processing.",
  });
}
