// Phase 8: small, dependency-free UI primitives built on styles/theme.css.
// Status is always shown with an icon and text, never by colour alone.
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Icons (24px grid, stroke-based, decorative unless labelled by the caller)
// ---------------------------------------------------------------------------

const ICON_PATHS = {
  meetings: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M3.5 9h17M8 3v3M16 3v3" /></>,
  ledger: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m3.5 6 1.5 1.5L7.5 5M3.5 12l1.5 1.5 2.5-2.5M3.5 18l1.5 1.5 2.5-2.5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  upload: <><path d="M12 15V4m0 0-4 4m4-4 4 4" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></>,
  mic: <><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" /></>,
  logout: <><path d="M15 4.5h3A1.5 1.5 0 0 1 19.5 6v12a1.5 1.5 0 0 1-1.5 1.5h-3" /><path d="M10 16l-4-4 4-4M6 12h9" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  checkCircle: <><circle cx="12" cy="12" r="8.5" /><path d="m8.5 12.3 2.4 2.4 4.6-4.9" /></>,
  warning: <><path d="M10.3 4.3 2.9 17.2A2 2 0 0 0 4.6 20h14.8a2 2 0 0 0 1.7-2.8L13.7 4.3a2 2 0 0 0-3.4 0Z" /><path d="M12 9.5v4M12 17h.01" /></>,
  error: <><circle cx="12" cy="12" r="8.5" /><path d="m9 9 6 6M15 9l-6 6" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  skip: <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12h7" /></>,
  cache: <><ellipse cx="12" cy="6" rx="7" ry="2.5" /><path d="M5 6v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-6" /></>,
  fallback: <><path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3" /><path d="M18 3v4h-4M6 21v-4h4" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.3-4.6L4 8" /><path d="M4 4v4h4M4 13a8 8 0 0 0 14.3 4.6L20 16" /><path d="M20 20v-4h-4" /></>,
  file: <><path d="M6.5 3.5h7l4 4v13h-11z" /><path d="M13.5 3.5v4h4" /></>,
  users: <><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 5.8a3 3 0 0 1 0 5.4M17.5 14.2a5.5 5.5 0 0 1 3 4.8" /></>,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  arrowLeft: <path d="M19 12H5m0 0 6-6m-6 6 6 6" />,
  arrowRight: <path d="M5 12h14m0 0-6-6m6 6-6 6" />,
  pencil: <><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" /><path d="m13.5 6.5 4 4" /></>,
  history: <><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5" /><path d="M4 4v4.5h4.5M12 8v4l3 2" /></>,
  download: <><path d="M12 4v11m0 0 4-4m-4 4-4-4" /><path d="M4 17v1.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V17" /></>,
  play: <path d="M8 5.5v13l10.5-6.5z" />,
  shield: <><path d="M12 3.5 5 6v5.5c0 4.3 2.9 7.6 7 9 4.1-1.4 7-4.7 7-9V6z" /><path d="m9 12 2.2 2.2L15.5 10" /></>,
  quote: <path d="M7 7h4v5c0 2.5-1.3 4.2-4 5M14 7h4v5c0 2.5-1.3 4.2-4 5" />,
  trash: <><path d="M4.5 7h15M10 11v6M14 11v6" /><path d="M6.5 7l1 12.5h9l1-12.5M9.5 7V4.5h5V7" /></>,
  sparkle: <path d="M12 3.5l1.9 5.6 5.6 1.9-5.6 1.9L12 18.5l-1.9-5.6L4.5 11l5.6-1.9z" />,
  external: <><path d="M14 4.5h5.5V10M19.5 4.5 11 13" /><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" /></>,
};

export function Icon({ name, size, className = "", label }) {
  const paths = ICON_PATHS[name];
  if (!paths) return null;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={label ? undefined : "true"}
      role={label ? "img" : undefined}
      aria-label={label}
      focusable="false"
    >
      {paths}
    </svg>
  );
}

export function BrandMark({ size = 32 }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <rect width="32" height="32" rx="10" fill="var(--brand, #ff6543)" />
        <path
          d="M9 21.5V12.8c0-.7.8-1.1 1.3-.7l5.7 5.1 5.7-5.1c.5-.4 1.3 0 1.3.7V21.5"
          stroke="#ffffff"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

export function Button({ variant = "secondary", size, icon, iconRight, loading = false, className = "", children, ...props }) {
  const classes = [
    "ui-btn",
    variant !== "secondary" && `ui-btn--${variant}`,
    size && `ui-btn--${size}`,
    !children && "ui-btn--icon",
    className,
  ].filter(Boolean).join(" ");
  return (
    <button type="button" className={classes} aria-busy={loading || undefined} {...props}>
      {loading ? <span className="ui-spinner" aria-hidden="true" /> : icon && <Icon name={icon} />}
      {children}
      {iconRight && !loading && <Icon name={iconRight} />}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Status badge: tone + icon + text. `pulse` only for genuinely live states.
// ---------------------------------------------------------------------------

const TONE_ICON = {
  success: "checkCircle",
  warning: "warning",
  danger: "error",
  info: "info",
  accent: "sparkle",
  neutral: null,
};

export function Badge({ tone = "neutral", icon, pulse = false, children, className = "", ...props }) {
  const iconName = icon === undefined ? TONE_ICON[tone] : icon;
  return (
    <span className={`ui-badge ui-badge--${tone} ${className}`} {...props}>
      {pulse ? <span className="ui-dot ui-dot--pulse" aria-hidden="true" /> : iconName && <Icon name={iconName} />}
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

export function Alert({ tone = "info", title, children, actions, role, className = "", ...props }) {
  const iconName = TONE_ICON[tone] || "info";
  return (
    <div
      className={`ui-alert ui-alert--${tone} ${className}`}
      role={role || (tone === "danger" ? "alert" : undefined)}
      {...props}
    >
      <Icon name={iconName} />
      <div className="ui-alert__body">
        {title && <strong className="ui-alert__title">{title}</strong>}
        {children}
        {actions && <div className="ui-alert__actions">{actions}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

export function EmptyState({ icon = "meetings", title, children, actions }) {
  return (
    <div className="ui-empty">
      <span className="ui-empty__icon"><Icon name={icon} /></span>
      <h3 className="ui-empty__title">{title}</h3>
      {children && <p className="ui-empty__text">{children}</p>}
      {actions && <div className="ui-empty__actions">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows (only rendered while a real request is pending)
// ---------------------------------------------------------------------------

export function Skeleton({ width = "100%", height = 12, style }) {
  return <span className="ui-skeleton" style={{ width, height, ...style }} aria-hidden="true" />;
}

export function SkeletonRows({ rows = 3, label = "Loading" }) {
  return (
    <div className="ui-skeleton-rows" role="status" aria-live="polite">
      <span className="ui-visually-hidden">{label}…</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div className="ui-skeleton-row" key={index}>
          <Skeleton width="38%" height={14} />
          <Skeleton width={`${70 - index * 9}%`} />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tooltip: wraps a control that already has an accessible name.
// ---------------------------------------------------------------------------

export function Tooltip({ text, children }) {
  return (
    <span className="ui-tooltip">
      {children}
      <span className="ui-tooltip__bubble" aria-hidden="true">{text}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Dialog: modal, labelled, Escape closes, focus moves in and is restored.
// ---------------------------------------------------------------------------

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({ title, description, onClose, children, actions, initialFocusRef, busy = false, testId }) {
  const titleId = useId();
  const descId = useId();
  const panelRef = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    const target = initialFocusRef?.current || panelRef.current?.querySelector(FOCUSABLE);
    target?.focus();
    return () => previous?.focus?.();
    // Run once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (event) => {
    if (event.key === "Escape" && !busy) {
      event.stopPropagation();
      onClose?.();
      return;
    }
    if (event.key !== "Tab") return;
    const nodes = Array.from(panelRef.current?.querySelectorAll(FOCUSABLE) || []);
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="ui-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose?.();
      }}
    >
      <div
        ref={panelRef}
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        onKeyDown={onKeyDown}
        data-testid={testId}
      >
        <h2 className="ui-dialog__title" id={titleId}>{title}</h2>
        {description && <p className="ui-dialog__text" id={descId}>{description}</p>}
        {children}
        {actions && <div className="ui-dialog__actions">{actions}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel = "Confirm",
  tone = "primary",
  onConfirm,
  onCancel,
  busy = false,
  error = "",
}) {
  const cancelRef = useRef(null);
  return (
    <Dialog
      title={title}
      description={description}
      onClose={onCancel}
      initialFocusRef={cancelRef}
      busy={busy}
      actions={
        <>
          <Button ref={cancelRef} onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant={tone === "danger" ? "danger-solid" : "primary"} onClick={onConfirm} loading={busy} disabled={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {error && <Alert tone="danger" className="ui-dialog__error">{error}</Alert>}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Toasts: polite live region for transient success / failure messages.
// ---------------------------------------------------------------------------

const ToastContext = createContext({ notify: () => {} });

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => setToasts((items) => items.filter((t) => t.id !== id)), []);

  const notify = useCallback((message, tone = "success") => {
    const id = nextId.current++;
    setToasts((items) => [...items.slice(-2), { id, message, tone }]);
    window.setTimeout(() => dismiss(id), tone === "danger" ? 8000 : 4500);
  }, [dismiss]);

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      <div className="ui-toast-region" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div className={`ui-toast ui-toast--${toast.tone}`} key={toast.id}>
            <Icon name={TONE_ICON[toast.tone] || "info"} />
            <div className="ui-toast__message">{toast.message}</div>
            <Button variant="ghost" size="sm" icon="close" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
