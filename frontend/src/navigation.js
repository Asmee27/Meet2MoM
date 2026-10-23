// Phase 8: URL <-> app state. Query parameters (not paths) so the app works
// from any static host or the API server without server-side route fallback,
// and existing links (?meeting=…, ?room=…&invite=…) keep working.

export const STEP_SLUGS = { 2: "transcript", 3: "extract", 4: "review", 5: "minutes" };
const SLUG_STEPS = Object.fromEntries(Object.entries(STEP_SLUGS).map(([n, slug]) => [slug, Number(n)]));

export function stepFromSlug(slug) {
  return SLUG_STEPS[slug] || null;
}

export function readRoute(search = window.location.search) {
  const params = new URLSearchParams(search);
  return {
    view: params.get("view"),
    meeting: params.get("meeting"),
    step: params.get("step"),
    room: params.get("room"),
    invite: params.get("invite"),
  };
}

// The URL for the current app state. Live-meeting invite parameters are kept
// so a guest's link survives reloads.
export function routeSearch({ page, meetingMode, currentStep, meetingId }, current = readRoute()) {
  const params = new URLSearchParams();
  if (page === "commitments") {
    params.set("view", "commitments");
  } else if (currentStep >= 2 && meetingId) {
    params.set("meeting", meetingId);
    params.set("step", STEP_SLUGS[currentStep]);
  } else if (meetingMode === "recorded") {
    params.set("view", "upload");
  } else if (meetingMode === "live") {
    if (current.room) {
      params.set("room", current.room);
      if (current.invite) params.set("invite", current.invite);
    } else {
      params.set("view", "live");
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}
