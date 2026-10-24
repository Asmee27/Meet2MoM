// Shared display formatting (Phase 8). Unknown values stay visibly unknown.

// Meeting times are stored in UTC without a zone; show them in local time.
export function parseUtc(value) {
  if (!value) return null;
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  const date = parseUtc(value);
  if (!date) return value || "Date unknown";
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function formatClock(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// "English (en), set" / "Malayalam (ml), detected" from the minutes payload.
// The source is shown only when the pipeline recorded it.
export function formatLanguage(mom) {
  const code = mom?.language;
  if (!code) return "Not specified";
  const name = mom.language_name && !mom.language_name.startsWith("Other") ? mom.language_name : null;
  const label = name ? `${name} (${code})` : code;
  if (mom.language_source === "configured") return `${label}, set`;
  if (mom.language_source === "detected") return `${label}, detected`;
  return label;
}

// Approved snapshot versions are stored as numbers (1, 2, …).
export function formatVersion(version) {
  if (typeof version === "number") return `v${version}`;
  return version || "v1";
}

// A readable name from the stored file name (extension removed).
export function meetingTitle(meeting) {
  if (meeting?.schedule?.title) return meeting.schedule.title;
  const name = meeting?.filename || "";
  const stripped = name.replace(/\.(json|wav|mp3|mp4|webm|ogg|m4a)$/i, "");
  return stripped || `Meeting ${String(meeting?.meeting_id || "").slice(0, 8)}`;
}

// Status shown in the meeting list, derived only from backend fields.
export function meetingStatus(meeting) {
  const job = meeting?.job_state;
  if (meeting?.status === "scheduled") return { key: "upcoming", tone: "accent", label: "Upcoming" };
  if (meeting?.status === "cancelled") return { key: "cancelled", tone: "neutral", label: "Cancelled" };
  // A room that is open now and has nothing processed yet.
  if (meeting?.live_status === "active" && !job && !meeting?.utterance_count) {
    return { key: "live", tone: "live", label: "Live", pulse: true };
  }
  if (job === "queued") return { key: "processing", tone: "info", label: "Queued", pulse: true };
  if (job === "running") return { key: "processing", tone: "info", label: "Processing", pulse: true };
  if (job === "failed") return { key: "failed", tone: "danger", label: "Failed" };
  if (meeting?.review_status === "approved") return { key: "approved", tone: "success", label: "Approved" };
  if (meeting?.review_status === "reopened") return { key: "review", tone: "warning", label: "Reopened" };
  return { key: "review", tone: "warning", label: "Needs review" };
}

// A scheduled start in the meeting's own timezone, e.g.
// "Mon, 26 Oct 2026, 10:30 (Asia/Kolkata)".
export function formatScheduledStart(schedule) {
  if (!schedule?.scheduled_start_utc) return "Time unknown";
  const date = new Date(schedule.scheduled_start_utc);
  if (Number.isNaN(date.getTime())) return schedule.scheduled_start_utc;
  try {
    const text = date.toLocaleString(undefined, {
      weekday: "short", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      timeZone: schedule.timezone,
    });
    return `${text} (${schedule.timezone})`;
  } catch {
    return date.toLocaleString();
  }
}
