// Phase 8: application shell. Top Navigation Header (Horizontal Navbar)
// across desktop, with a slide-out drawer on small screens.
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

export default function AppShell({ section, onNavigate, onNewMeeting, children }) {
  const { user, signOut, authRequired } = useAuth();
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

      {/* Modern Horizontal Top Navigation Bar */}
      <header className="shell-navbar" aria-label="Top navigation">
        <div className="shell-navbar__inner">
          {/* Left: Brand Logo & Mobile Drawer Toggle */}
          <div className="shell-navbar__brand">
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
            <a
              href="./"
              className="shell-brand"
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                event.preventDefault();
                onNavigate("meetings");
              }}
            >
              <BrandMark size={32} />
              <span className="shell-brand__name">Meet2MoM</span>
            </a>
          </div>

          {/* Center: Horizontal Navigation Links */}
          <nav className="shell-nav shell-nav--horizontal" aria-label="Main">
            {NAV_ITEMS.map((item) => (
              <a
                key={item.id}
                href={item.id === "commitments" ? "?view=commitments" : "./"}
                className={`shell-nav__item${section === item.id ? " is-active" : ""}`}
                aria-current={section === item.id ? "page" : undefined}
                onClick={(event) => {
                  if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                  event.preventDefault();
                  onNavigate(item.id);
                }}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </a>
            ))}
          </nav>

          {/* Right: + New Meeting Button & User Profile Pill */}
          <div className="shell-navbar__right">
            <Button
              variant="primary"
              icon="plus"
              className="shell-new"
              onClick={() => onNewMeeting()}
            >
              New meeting
            </Button>

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
          </div>
        </div>
      </header>

      {/* Mobile Drawer (Accessible on smaller viewports) */}
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
            <div className="shell-brand">
              <BrandMark size={30} />
              <span className="shell-brand__name">Meet2MoM</span>
            </div>
            <Button
              variant="primary"
              icon="plus"
              className="shell-new"
              onClick={() => { closeDrawer(); onNewMeeting(); }}
            >
              New meeting
            </Button>
            <nav className="shell-nav" aria-label="Main">
              {NAV_ITEMS.map((item) => (
                <a
                  key={item.id}
                  href={item.id === "commitments" ? "?view=commitments" : "./"}
                  className={`shell-nav__item${section === item.id ? " is-active" : ""}`}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                    event.preventDefault();
                    closeDrawer();
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
          </div>
        </div>
      )}

      {/* Main Content Region */}
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
