import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function Header() {
  const { currentStep, meetingMode } = useMeetingFlow();

  return (
    <>
      <header className="header">

        <div className="brand">

          <div className="brand-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <rect x="8" y="4" width="8" height="11" rx="4" stroke="currentColor" strokeWidth="1.8"/>
              <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          </div>

          <div className="brand-name">
            Meet2MoM
          </div>

        </div>

        <nav className="steps">

          <div
            className={`step ${
              currentStep === 1 ? "active" : ""
            } ${
              currentStep > 1 ? "completed" : ""
            }`}
          >
            <span className="step-number">1</span>
            <span>{meetingMode === "live" ? "Live Meeting" : "Upload"}</span>
          </div>

          <div
            className={`step ${
              currentStep === 2 ? "active" : ""
            } ${
              currentStep > 2 ? "completed" : ""
            }`}
          >
            <span className="step-number">2</span>
            <span>Transcribe</span>
          </div>

          <div
            className={`step ${
              currentStep === 3 ? "active" : ""
            } ${
              currentStep > 3 ? "completed" : ""
            }`}
          >
            <span className="step-number">3</span>
            <span>Extract</span>
          </div>

          <div
            className={`step ${
              currentStep === 4 ? "active" : ""
            } ${
              currentStep > 4 ? "completed" : ""
            }`}
          >
            <span className="step-number">4</span>
            <span>Verify</span>
          </div>

          <div
            className={`step ${
              currentStep === 5 ? "active" : ""
            } ${
              currentStep > 5 ? "completed" : ""
            }`}
          >
            <span className="step-number">5</span>
            <span>Export</span>
          </div>

        </nav>

      </header>
    </>
  );
}
