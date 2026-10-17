// Display helpers shared by the verification components (Phase 1).

export const STATUS_LABELS = {
  verified: "Verified",
  verified_with_warning: "Verified with warning",
  review: "Needs review",
  rejected: "Rejected",
  unverified: "Not verified",
};

export function formatTimestamp(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return "time unavailable";
  }
  const total = Math.max(0, Math.floor(Number(seconds)));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
