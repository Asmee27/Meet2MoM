// Phase 8: application shell. Sidebar navigation on desktop, a top bar with a
// drawer on small screens. Only implemented sections are listed.
import { useEffect, useRef, useState } from "react";
import { useAuth } from "./AuthGate";
import { BrandMark, Button, Icon } from "./ui";

const NAV_ITEMS = [
  { id: "meetings", label: "Meetings", icon: "meetings" },
  { id: "commitments", label: "Commitments", icon: "ledger" },
];

function initials(name = "") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] || "?").toUpperCase() + (parts[1]?.[0] || "").toUpperCase();
}

function SidebarContent({ section, onNavigate, onNewMeeting, onItemClick }) {
  const { user, signOut, authRequired } = useAuth();
  const activeIndex = Math.max(0, NAV_ITEMS.findIndex((item) => item.id === section));

  return (
    <>
      <div className="shell-brand">
        <BrandMark size={30} />
        <span className="shell-brand__name">Meet2MoM</span>
      </div>

      <Button
        variant="primary"
        icon="plus"
        className="shell-new"
        onClick={() => { onItemClick?.(); onNewMeeting(); }}
      >
        New meeting
      </Button>

      <nav className="shell-nav" aria-label="Main">
        <span
          className="shell-nav__indicator"
          style={{ "--nav-index": activeIndex, opacity: section ? 1 : 0 }}
          aria-hidden="true"
        />
        {NAV_ITEMS.map((item) => (
          <a
            key={item.id}
            href={item.id === "commitments" ? "?view=commitments" : "./"}
            className={`shell-nav__item${section === item.id ? " is-active" : ""}`}
            aria-current={section === item.id ? "page" : undefined}
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey) return;
              event.preventDefault();
              onItemClick?.();
              onNavigate(item.id);
            }}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </a>
        ))}
      </nav>

      <div className="shell-user">
        {user ? (
          <>
            <span className="shell-user__avatar" aria-hidden="true">{initials(user.display_name)}</span>
            <span className="shell-user__text">
              <strong title={user.display_name}>{user.display_name}</strong>
              <small title={user.email}>{user.email}</small>
            </span>
            <Button
              variant="ghost"
              size="sm"
              icon="logout"
              className="shell-user__signout"
              onClick={signOut}
              aria-label="Sign out"
              title="Sign out"
            />
          </>
        ) : (
          <span className="shell-user__local">
            {authRequired ? "Not signed in" : "Local mode · sign-in is off"}
          </span>
        )}
      </div>
    </>
  );
}

export default function AppShell({ section, onNavigate, onNewMeeting, children }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef(null);
  const menuButtonRef = useRef(null);

  useEffect(() => {
    if (!drawerOpen) return undefined;
    drawerRef.current?.querySelector("a, button")?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        setDrawerOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const closeDrawer = () => setDrawerOpen(false);

  return (
    <div className="shell">
      <a className="ui-skip-link" href="#main-content">Skip to content</a>

      <aside className="shell-sidebar" aria-label="Sidebar">
        <SidebarContent section={section} onNavigate={onNavigate} onNewMeeting={onNewMeeting} />
      </aside>

      <header className="shell-topbar">
        <Button
          ref={menuButtonRef}
          variant="ghost"
          icon="menu"
          className="shell-topbar__menu"
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
          aria-controls="shell-drawer"
          onClick={() => setDrawerOpen(true)}
        />
        <span className="shell-brand shell-brand--compact">
          <BrandMark size={26} />
          <span className="shell-brand__name">Meet2MoM</span>
        </span>
      </header>

      {drawerOpen && (
        <div className="shell-drawer-backdrop" onMouseDown={closeDrawer}>
          <div
            id="shell-drawer"
            ref={drawerRef}
            className="shell-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <Button
              variant="ghost"
              icon="close"
              className="shell-drawer__close"
              aria-label="Close navigation"
              onClick={() => { closeDrawer(); menuButtonRef.current?.focus(); }}
            />
            <SidebarContent
              section={section}
              onNavigate={onNavigate}
              onNewMeeting={onNewMeeting}
              onItemClick={closeDrawer}
            />
          </div>
        </div>
      )}

      {/* Each page renders its own <main> landmark inside this region. */}
      <div className="shell-main" id="main-content" tabIndex={-1}>
        {children}
      </div>
    </div>
  );
}

// Consistent page heading: optional eyebrow, title, description and actions.
export function PageHeader({ eyebrow, title, description, actions, children }) {
  return (
    <div className="page-header">
      <div className="page-header__text">
        {eyebrow && <div className="page-header__eyebrow">{eyebrow}</div>}
        <h1 className="page-header__title">{title}</h1>
        {description && <p className="page-header__description">{description}</p>}
        {children}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </div>
  );
}
