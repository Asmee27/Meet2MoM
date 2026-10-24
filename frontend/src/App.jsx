import "./App.css";
import "./styles/theme.css";
import "./styles/shell.css";
// Imported here (not only by the lazy pages) so the cascade order is fixed:
// legacy.css re-skins these and must come after them.
import "./components/ClaimVerification.css";
import "./pages/LiveMeeting.css";
import "./styles/legacy.css";

import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";

import AppShell from "./components/AppShell";
import AuthGate, { useAuth } from "./components/AuthGate";
import MeetingHeader from "./components/MeetingHeader";
import { Alert, ConfirmDialog, Icon, SkeletonRows, ToastProvider } from "./components/ui";
import {
  MeetingFlowProvider,
  useMeetingFlow,
} from "./context/MeetingFlowContext";
import { readRoute, routeSearch, stepFromSlug } from "./navigation";
import { getStatus } from "./services/api";
import { meetingStatus, meetingTitle } from "./format";

import Dashboard from "./pages/Dashboard";
import UploadMeeting from "./pages/UploadMeeting";
import Transcript from "./pages/Transcript";
import Decisions from "./pages/Decisions";

// Heavy or later screens load on demand (LiveKit's client is most of the
// bundle and is only needed inside a live room).
const LiveMeeting = lazy(() => import("./pages/LiveMeeting"));
const VerifyMeeting = lazy(() => import("./pages/VerifyMeeting"));
const Summary = lazy(() => import("./pages/Summary"));
const Commitments = lazy(() => import("./pages/Commitments"));
const ScheduleMeeting = lazy(() => import("./pages/ScheduleMeeting"));
const ScheduledMeeting = lazy(() => import("./pages/ScheduledMeeting"));

function initialPage(route) {
  if (route.view === "commitments") return "commitments";
  if (route.view === "schedule") return "schedule";
  if (route.scheduled) return "scheduled";
  return "meetings";
}

function PageFallback() {
  return (
    <div className="page">
      <SkeletonRows rows={3} label="Loading page" />
    </div>
  );
}

function Crumb({ onBack, label }) {
  return (
    <div className="page page--crumb">
      <button type="button" className="crumb-back" onClick={onBack}>
        <Icon name="arrowLeft" /> All meetings
      </button>
      <span className="crumb-current" aria-current="page">{label}</span>
    </div>
  );
}

function MeetingFlow() {
  const flow = useMeetingFlow();
  const {
    currentStep,
    setCurrentStep,
    meetingMode,
    setMeetingMode,
    backendFileId,
    loadExistingMeeting,
    resetFlow,
    watchProcessingMeeting,
    transcriptionError,
    transcriptionComplete,
    goToExtract,
    goToVerify,
    goToExport,
    automaticCheckComplete,
    setVerificationStage,
    loadReviewData,
  } = flow;

  const { user } = useAuth() || {};
  const initial = useRef(readRoute());
  const [page, setPage] = useState(() => initialPage(initial.current));
  // Phase 8.3: the scheduled meeting shown on its own page, and the room code
  // and topic the live room opens with when a host starts or reopens one.
  const [scheduledId, setScheduledId] = useState(initial.current.scheduled);
  const [liveDefaults, setLiveDefaults] = useState(null);
  const [activeMeeting, setActiveMeeting] = useState(null);
  const [leaveAction, setLeaveAction] = useState(null);
  const [openError, setOpenError] = useState("");
  // A ?meeting= link is loading (MeetingFlowContext loads it on start-up).
  const [booting, setBooting] = useState(Boolean(initial.current.meeting));
  // Steps opened in this session for the current meeting. Only these can be
  // revisited from the stepper; later steps run their backend stage first.
  const [visited, setVisited] = useState({ id: null, steps: [] });
  // A ?meeting=…&step=… link restores its step by running the same backend
  // calls as the workflow (extract / verify reuse current results).
  const pendingStep = useRef(stepFromSlug(initial.current.step));
  const openManualReview = useRef(false);

  // Live-meeting invite links (?room=…&invite=…) and ?view= links open their
  // screen. Until that mode is applied the URL must not be rewritten: an
  // early sync would drop ?room and ?invite before the live room reads them.
  const [routeApplied, setRouteApplied] = useState(
    () => !(initial.current.room || initial.current.view === "live" || initial.current.view === "upload")
  );
  useEffect(() => {
    const route = initial.current;
    if (route.room || route.view === "live") setMeetingMode("live");
    else if (route.view === "upload") setMeetingMode("recorded");
    setRouteApplied(true);
  }, [setMeetingMode]);

  useEffect(() => {
    if (!booting) return;
    if (currentStep >= 2) setBooting(false);
    else if (transcriptionError) {
      setOpenError(transcriptionError);
      setBooting(false);
    }
  }, [booting, currentStep, transcriptionError]);

  useEffect(() => {
    // History of the workflow position, not derivable from current state.
    setVisited((v) => {
      if (v.id !== backendFileId) return { id: backendFileId, steps: [currentStep] };
      return v.steps.includes(currentStep) ? v : { id: v.id, steps: [...v.steps, currentStep] };
    });
  }, [backendFileId, currentStep]);

  useEffect(() => {
    const target = pendingStep.current;
    if (!target || currentStep !== 2 || !transcriptionComplete || !backendFileId) return;
    pendingStep.current = null;
    if (target === 3) goToExtract();
    else if (target === 4) {
      openManualReview.current = true;
      goToVerify();
    } else if (target === 5) {
      loadReviewData(backendFileId);
      goToExport();
    }
  }, [currentStep, transcriptionComplete, backendFileId, goToExtract, goToVerify, goToExport, loadReviewData]);

  useEffect(() => {
    if (openManualReview.current && currentStep === 4 && automaticCheckComplete) {
      openManualReview.current = false;
      setVerificationStage("manual");
    }
  }, [currentStep, automaticCheckComplete, setVerificationStage]);

  // Reflect the current screen in the URL (push, so Back/Forward work).
  useEffect(() => {
    // While a ?meeting=…&step=… link is being restored, keep its URL.
    if (!routeApplied || booting || pendingStep.current || openManualReview.current) return;
    const search = routeSearch({ page, meetingMode, currentStep, meetingId: backendFileId, scheduledId });
    if (search !== window.location.search) {
      // A bare ?meeting= link only gains its step: no extra history entry.
      const current = readRoute();
      const sameEntry = current.meeting && current.meeting === backendFileId && !current.step;
      window.history[sameEntry ? "replaceState" : "pushState"](null, "", `${window.location.pathname}${search}`);
    }
  }, [routeApplied, booting, page, meetingMode, currentStep, backendFileId, scheduledId]);

  // Browser Back/Forward: move the app to the screen in the URL.
  useEffect(() => {
    const onPop = () => {
      const route = readRoute();
      setOpenError("");
      if (route.view === "commitments" || route.view === "schedule") {
        setPage(route.view);
        return;
      }
      if (route.scheduled) {
        setScheduledId(route.scheduled);
        setPage("scheduled");
        return;
      }
      setPage("meetings");
      if (route.meeting) {
        if (route.meeting === backendFileId) {
          const wanted = stepFromSlug(route.step) || 2;
          const steps = visited.id === backendFileId ? visited.steps : [];
          const reachable = steps.filter((s) => s >= 2 && s <= wanted);
          setCurrentStep(steps.includes(wanted) ? wanted : Math.max(2, ...reachable));
        } else {
          loadExistingMeeting(route.meeting);
        }
        return;
      }
      resetFlow();
      if (route.view === "upload") setMeetingMode("recorded");
      else if (route.view === "live" || route.room) setMeetingMode("live");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [backendFileId, visited, loadExistingMeeting, resetFlow, setCurrentStep, setMeetingMode]);

  const inLiveRoom = page === "meetings" && currentStep === 1 && meetingMode === "live";

  // Leaving the live room unmounts it (and disconnects); ask first.
  const guarded = useCallback((action) => {
    if (inLiveRoom) setLeaveAction(() => action);
    else action();
  }, [inLiveRoom]);

  const goToMeetings = useCallback(() => guarded(() => {
    setOpenError("");
    setPage("meetings");
    resetFlow();
  }), [guarded, resetFlow]);

  const navigate = (target) => {
    if (target === "commitments") guarded(() => setPage("commitments"));
    else goToMeetings();
  };

  const startUpload = () => guarded(() => {
    setPage("meetings");
    resetFlow();
    setMeetingMode("recorded");
  });

  const startLive = (defaults = null) => {
    setPage("meetings");
    resetFlow();
    setLiveDefaults(defaults && defaults.roomName ? defaults : null);
    setMeetingMode("live");
  };

  const startSchedule = () => guarded(() => {
    resetFlow();
    setPage("schedule");
  });

  const openScheduled = useCallback((meetingId) => {
    resetFlow();
    setScheduledId(meetingId);
    setPage("scheduled");
  }, [resetFlow]);

  const openMeeting = useCallback(async (meeting) => {
    setOpenError("");
    setPage("meetings");
    setActiveMeeting(meeting);
    const status = meetingStatus(meeting);
    if (meeting.schedule) {
      openScheduled(meeting.meeting_id);
      return;
    }
    if (status.key === "live") {
      startLive({ roomName: meeting.live_room_name, topic: meetingTitle(meeting) });
      return;
    }
    if (status.key === "processing" || status.key === "failed") {
      let job = null;
      try {
        job = await getStatus(meeting.meeting_id);
      } catch {
        job = null;
      }
      watchProcessingMeeting(meeting.meeting_id, job);
      return;
    }
    resetFlow();
    loadExistingMeeting(meeting.meeting_id);
  }, [loadExistingMeeting, resetFlow, watchProcessingMeeting, openScheduled]);

  let content;
  if (page === "commitments") {
    content = <Commitments onOpenMeeting={openMeeting} />;
  } else if (page === "schedule") {
    content = (
      <>
        <Crumb onBack={goToMeetings} label="Schedule meeting" />
        <ScheduleMeeting hostEmail={user?.email || ""} onOpenScheduled={openScheduled} onCancel={goToMeetings} />
      </>
    );
  } else if (page === "scheduled" && scheduledId) {
    content = (
      <>
        <Crumb onBack={goToMeetings} label="Scheduled meeting" />
        <ScheduledMeeting
          key={scheduledId}
          meetingId={scheduledId}
          hostEmail={user?.email || ""}
          onStart={(m) => startLive({ roomName: m.room_name, topic: m.title })}
        />
      </>
    );
  } else if (booting) {
    content = <PageFallback />;
  } else if (currentStep === 1 && !meetingMode) {
    content = (
      <>
        {openError && (
          <div className="page page--flush">
            <Alert tone="danger" title="The meeting could not be opened">
              <p>{openError}</p>
            </Alert>
          </div>
        )}
        <Dashboard
          onOpenMeeting={openMeeting}
          onUpload={startUpload}
          onStartLive={() => startLive()}
          onSchedule={startSchedule}
          onOpenCommitments={() => setPage("commitments")}
        />
      </>
    );
  } else if (currentStep === 1 && meetingMode === "recorded") {
    content = (
      <>
        <Crumb onBack={goToMeetings} label="Upload recording" />
        <UploadMeeting />
      </>
    );
  } else if (currentStep === 1 && meetingMode === "live") {
    content = (
      <>
        <Crumb onBack={goToMeetings} label="Live meeting" />
        <LiveMeeting key={liveDefaults?.roomName || "new"} defaults={liveDefaults} />
      </>
    );
  } else {
    content = (
      <>
        <div className="page page--flush">
          <MeetingHeader
            meeting={activeMeeting}
            visitedSteps={visited.id === backendFileId ? visited.steps : []}
            onBack={goToMeetings}
          />
        </div>
        <div className="workspace" key={currentStep}>
          {currentStep === 2 && <Transcript />}
          {currentStep === 3 && <Decisions />}
          {currentStep === 4 && <VerifyMeeting />}
          {currentStep === 5 && <Summary />}
        </div>
      </>
    );
  }

  return (
    <AppShell
      section={page}
      onNavigate={navigate}
      onNewMeeting={startUpload}
    >
      <Suspense fallback={<PageFallback />}>{content}</Suspense>

      {leaveAction && (
        <ConfirmDialog
          title="Leave the meeting room?"
          description="If you are connected to the room, leaving this page disconnects you. To finish the meeting, use End meeting in the room instead."
          confirmLabel="Leave room"
          onConfirm={() => { const action = leaveAction; setLeaveAction(null); action(); }}
          onCancel={() => setLeaveAction(null)}
        />
      )}
    </AppShell>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AuthGate>
        <MeetingFlowProvider>
          <MeetingFlow />
        </MeetingFlowProvider>
      </AuthGate>
    </ToastProvider>
  );
}
