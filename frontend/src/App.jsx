import { Navigate, Route, Routes } from "react-router-dom";

import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";

import Dashboard from "./pages/Dashboard";
import UploadMeeting from "./pages/UploadMeeting";
import Transcript from "./pages/Transcript";
import Topics from "./pages/Topics";
import Decisions from "./pages/Decisions";
import ActionItems from "./pages/ActionItems";
import Summary from "./pages/Summary";

function App() {
  return (
    <div className="app-layout">
      <Sidebar />

      <div className="main-area">
        <Topbar />

        <main className="page-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/upload" element={<UploadMeeting />} />
            <Route path="/transcript" element={<Transcript />} />
            <Route path="/topics" element={<Topics />} />
            <Route path="/decisions" element={<Decisions />} />
            <Route path="/actions" element={<ActionItems />} />
            <Route path="/summary" element={<Summary />} />

            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default App;