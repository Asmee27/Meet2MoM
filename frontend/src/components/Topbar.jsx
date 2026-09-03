import { Bell, CircleUserRound } from "lucide-react";

function Topbar() {
  return (
    <header className="topbar">
      <div>
        <p className="topbar-label">AI Meeting Workspace</p>
      </div>

      <div className="topbar-actions">
        <button className="icon-button">
          <Bell size={19} />
        </button>

        <div className="profile">
          <CircleUserRound size={30} />

          <div>
            <strong>Meet2MoM Team</strong>
            <span>Project Workspace</span>
          </div>
        </div>
      </div>
    </header>
  );
}

export default Topbar;