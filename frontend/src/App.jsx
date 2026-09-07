import "./App.css";

import Header from "./components/Header";
import { MeetingFlowProvider, useMeetingFlow } from "./context/MeetingFlowContext";

import UploadMeeting from "./pages/UploadMeeting";
import Transcript from "./pages/Transcript";
import Decisions from "./pages/Decisions";
import VerifyMeeting from "./pages/VerifyMeeting";
import Summary from "./pages/Summary";

function MeetingFlow() {
  const { currentStep } = useMeetingFlow();

  return (
    <div className="app">
      <Header />

      {currentStep === 1 && <UploadMeeting />}
      {currentStep === 2 && <Transcript />}
      {currentStep === 3 && <Decisions />}
      {currentStep === 4 && <VerifyMeeting />}
      {currentStep === 5 && <Summary />}
    </div>
  );
}

export default function App() {
  return (
    <MeetingFlowProvider>
      <MeetingFlow />
    </MeetingFlowProvider>
  );
}
