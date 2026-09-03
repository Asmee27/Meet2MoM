import { NavLink } from "react-router-dom";

import {
  LayoutDashboard,
  Upload,
  MessageSquareText,
  Layers3,
  BadgeCheck,
  ListChecks,
  FileText,
  Sparkles,
} from "lucide-react";

const navItems = [
  {
    name: "Dashboard",
    path: "/",
    icon: LayoutDashboard,
  },
  {
    name: "Upload Meeting",
    path: "/upload",
    icon: Upload,
  },
  {
    name: "Transcript",
    path: "/transcript",
    icon: MessageSquareText,
  },
  {
    name: "Topics",
    path: "/topics",
    icon: Layers3,
  },
  {
    name: "Decisions",
    path: "/decisions",
    icon: BadgeCheck,
  },
  {
    name: "Action Items",
    path: "/actions",
    icon: ListChecks,
  },
  {
    name: "Meeting Summary",
    path: "/summary",
    icon: FileText,
  },
];

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-icon">
          <Sparkles size={23} />
        </div>

        <div>
          <h1>Meet2MoM</h1>
          <span>Meeting Intelligence</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => {
          const Icon = item.icon;

          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              className={({ isActive }) =>
                `nav-item ${isActive ? "active" : ""}`
              }
            >
              <Icon size={19} />
              <span>{item.name}</span>
            </NavLink>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="ai-status">
          <span className="status-dot" />

          <div>
            <strong>AI Pipeline</strong>
            <p>System ready</p>
          </div>
        </div>
      </div>
    </aside>
  );
}

export default Sidebar;