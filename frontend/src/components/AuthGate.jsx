// Phase 7: sign-in screen in front of the app. The backend enforces access;
// this only collects credentials when the API answers 401. With
// AUTH_REQUIRED=false the app renders exactly as before.
import { createContext, useContext, useEffect, useState } from "react";
import { getMe, login, logout, register } from "../services/api";
import { BrandMark, Icon } from "./ui";

const AuthContext = createContext({ user: null, authRequired: false, signOut: () => {} });

export const useAuth = () => useContext(AuthContext);

export default function AuthGate({ children }) {
  const [state, setState] = useState({ status: "checking", user: null, authRequired: false });
  const [mode, setMode] = useState("signin");
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => !cancelled && setState({ status: "ready", user: me.user, authRequired: me.auth_required }))
      .catch((err) => {
        if (cancelled) return;
        // Only an explicit 401 asks for sign-in; other failures leave the app's
        // own "backend unreachable" handling in charge.
        setState(err.status === 401
          ? { status: "signed-out", user: null, authRequired: true }
          : { status: "ready", user: null, authRequired: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result = mode === "signin"
        ? await login(form.email, form.password)
        : await register(form.email, form.password, form.name);
      setState({ status: "ready", user: result.user, authRequired: true });
    } catch (err) {
      setError(err.message || "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    try {
      await logout();
    } finally {
      setState({ status: "signed-out", user: null, authRequired: true });
    }
  };

  if (state.status === "checking") {
    return (
      <div className="auth-loading" role="status">
        <span className="ui-spinner" aria-hidden="true" />
        <span>Loading…</span>
      </div>
    );
  }

  if (state.status === "signed-out") {
    return (
      <div className="auth-screen">
        {/* Full-width Top Navbar */}
        <header className="auth-topbar">
          <div className="auth-brand__logo">
            <BrandMark size={34} />
            <span>Meet2MoM</span>
          </div>
          <div className="auth-live-pill">
            <span className="auth-live-dot" /> LIVE, TOGETHER
          </div>
        </header>

        {/* Seamless 2-Column Hero Section - 50/50 exact balance, no dividing line */}
        <div className="auth-container">
          <aside className="auth-brand" aria-hidden="true">
            <div className="auth-kicker-pill">
              <span className="auth-live-dot" /> LIVE INTELLIGENCE
            </div>

            <h1 className="auth-brand__title">Make room for the conversations that matter.</h1>
            
            <p className="auth-brand__tagline">
              Speak freely. We’ll capture the truth, extract decisions, verify evidence, and hold the commitment ledger.
            </p>

            <ul className="auth-brand__features">
              <li>
                <span className="auth-feature-bullet"><Icon name="shield" size={16} /></span>
                <div>
                  <strong>Audit-grade Grounding</strong>
                  <span>Every decision and action item cites the exact transcript lines it comes from.</span>
                </div>
              </li>
              <li>
                <span className="auth-feature-bullet"><Icon name="mic" size={16} /></span>
                <div>
                  <strong>Private &amp; Consensual</strong>
                  <span>Audio recording begins only after every participant grants explicit consent.</span>
                </div>
              </li>
              <li>
                <span className="auth-feature-bullet"><Icon name="checkCircle" size={16} /></span>
                <div>
                  <strong>Human-in-the-Loop Review</strong>
                  <span>Nothing is final or sent until a reviewer verifies and approves the minutes.</span>
                </div>
              </li>
            </ul>
          </aside>

          <main className="auth-main">
            <div className="auth-card-wrapper">
              {/* Floating Badge Top-Right (aligned to card corner) */}
              <div className="auth-floating-badge auth-floating-badge--top">
                <span className="auth-floating-badge__icon auth-floating-badge__icon--coral">
                  <Icon name="mic" size={16} />
                </span>
                <div className="auth-floating-badge__text">
                  <strong>Crystal clear</strong>
                  <small>Audio &amp; video</small>
                </div>
              </div>

              {/* Enhanced, Prominent Sign In / Sign Up Card */}
              <form className="auth-card ui-enter" onSubmit={submit} aria-labelledby="auth-title">
                <div className="auth-card__topbar">
                  <span className="auth-card__kicker">YOUR MEETING SPACE</span>
                  <span className="auth-card__pill">Private by default</span>
                </div>

                <div className="auth-card__header">
                  <div className="auth-card__logo">
                    <BrandMark size={32} />
                    <span>Meet2MoM</span>
                  </div>
                  <h2 id="auth-title">{mode === "signin" ? "Sign in to Meet2MoM" : "Create your account"}</h2>
                  <p className="auth-subtitle">
                    {mode === "signin" ? "Sign in to your meetings." : "Create an account to host or join meetings."}
                  </p>
                </div>

                {mode === "register" && (
                  <div className="ui-field">
                    <label className="ui-label" htmlFor="auth-name">Name</label>
                    <input id="auth-name" className="ui-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required autoComplete="name" placeholder="Alex Morgan" />
                  </div>
                )}
                <div className="ui-field">
                  <label className="ui-label" htmlFor="auth-email">Email</label>
                  <input id="auth-email" className="ui-input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoComplete="email" placeholder="name@company.com" />
                </div>
                <div className="ui-field">
                  <label className="ui-label" htmlFor="auth-password">Password</label>
                  <input
                    id="auth-password"
                    className="ui-input"
                    type="password"
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                    required
                    minLength={mode === "register" ? 8 : undefined}
                    autoComplete={mode === "signin" ? "current-password" : "new-password"}
                    aria-describedby={mode === "register" ? "auth-password-help" : undefined}
                    placeholder="••••••••"
                  />
                  {mode === "register" && <span className="ui-help" id="auth-password-help">At least 8 characters.</span>}
                </div>
                {error && <div className="ui-alert ui-alert--danger auth-error" role="alert"><Icon name="error" /><span>{error}</span></div>}
                <button type="submit" className="ui-btn ui-btn--primary ui-btn--lg auth-submit" disabled={busy}>
                  <span>{busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}</span>
                  <span className="auth-submit-arrow" aria-hidden="true">→</span>
                </button>
                <button type="button" className="auth-switch" onClick={() => { setMode(mode === "signin" ? "register" : "signin"); setError(""); }}>
                  {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
                </button>
              </form>

              {/* Floating Badge Bottom-Left (aligned to card corner) */}
              <div className="auth-floating-badge auth-floating-badge--bottom">
                <span className="auth-floating-badge__icon auth-floating-badge__icon--mint">
                  <Icon name="check" size={16} />
                </span>
                <div className="auth-floating-badge__text">
                  <strong>Zero friction</strong>
                  <small>Just click and connect</small>
                </div>
              </div>
            </div>
          </main>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user: state.user, authRequired: state.authRequired, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}
