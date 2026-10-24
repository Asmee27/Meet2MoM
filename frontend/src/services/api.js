// src/services/api.js
// Single source of truth for all backend API calls.

const defaultBaseUrl =
  typeof window !== "undefined" && window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : "http://127.0.0.1:8000";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || defaultBaseUrl;

// Phase 7: the session is an HttpOnly cookie, so every API request (and the
// <audio> / PDF links, which the browser handles itself) must carry cookies.
if (typeof window !== "undefined" && window.fetch && !window.__m2mCredentialsFetch) {
  const browserFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.startsWith(API_BASE_URL)) {
      return browserFetch(input, { credentials: "include", ...init });
    }
    return browserFetch(input, init);
  };
  window.__m2mCredentialsFetch = true;
}

async function authRequest(path, { method = "GET", body } = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const error = new Error(data?.detail || `Request failed (${res.status})`);
    error.status = res.status;
    throw error;
  }
  return data;
}

// Accounts (Phase 7). getMe resolves to { auth_required, user } or throws 401.
export const getMe = () => authRequest("/api/auth/me");
export const login = (email, password) => authRequest("/api/auth/login", { method: "POST", body: { email, password } });
export const register = (email, password, display_name) =>
  authRequest("/api/auth/register", { method: "POST", body: { email, password, display_name } });
export const logout = () => authRequest("/api/auth/logout", { method: "POST" });
export const createInvitation = (meetingId) =>
  authRequest(`/api/auth/meetings/${encodeURIComponent(meetingId)}/invitations`, { method: "POST" });
export const acceptInvitation = (token) =>
  authRequest("/api/auth/invitations/accept", { method: "POST", body: { token } });

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
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data?.detail || "Failed to create LiveKit room.");
    error.status = res.status;
    throw error;
  }
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
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data?.detail || "Failed to generate LiveKit join token.");
    error.status = res.status;
    throw error;
  }
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

// ---------------------------------------------------------------------------
// Meeting deletion (Phase 10) and commitment ledger (Phase 9)
// ---------------------------------------------------------------------------
export const deleteMeeting = (meetingId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}`, { method: "DELETE" });
export const getMeetingCommitments = (meetingId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/commitments`);
export const proposeCommitmentUpdates = (meetingId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/commitments/propose`, { method: "POST" });
export const reviewCommitmentUpdate = (meetingId, commitmentId, status, note = "", reviewer = "") =>
  authRequest(
    `/api/meetings/${encodeURIComponent(meetingId)}/commitments/${encodeURIComponent(commitmentId)}/review`,
    { method: "POST", body: { status, note, reviewer } },
  );
export const getLedger = () => authRequest("/api/commitments");

// ---------------------------------------------------------------------------
// Transcript correction (Phase 8). The server keeps the ASR text, audits the
// change and holds claims verified against the old words until re-verified.
// ---------------------------------------------------------------------------
// `reviewer` is used only when sign-in is off; otherwise the server records
// the signed-in account.
export const correctTranscriptUtterance = (meetingId, uttId, text, reason = "", reviewer = "") =>
  authRequest(
    `/api/meetings/${encodeURIComponent(meetingId)}/transcript/${encodeURIComponent(uttId)}`,
    { method: "PUT", body: { text, reason, reviewer: reviewer || null } },
  );
export const getTranscriptCorrections = (meetingId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/transcript-corrections`);

// ---------------------------------------------------------------------------
// Scheduled meetings (Phase 8.3). No email is sent: each invitee's link is
// returned to the host to share. Copying a link issues a new one and the
// previous link stops working (only token hashes are stored).
// ---------------------------------------------------------------------------
export const scheduleMeeting = (payload) => authRequest("/api/schedule", { method: "POST", body: payload });
export const getSchedule = (meetingId) => authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/schedule`);
export const addScheduleInvitees = (meetingId, emails) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/schedule/invitees`, { method: "POST", body: { emails } });
export const copyInviteeLink = (meetingId, inviteeId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/schedule/invitees/${inviteeId}/link`, { method: "POST" });
export const revokeInvitee = (meetingId, inviteeId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/schedule/invitees/${inviteeId}`, { method: "DELETE" });
export const cancelScheduledMeeting = (meetingId) =>
  authRequest(`/api/meetings/${encodeURIComponent(meetingId)}/schedule/cancel`, { method: "POST" });
