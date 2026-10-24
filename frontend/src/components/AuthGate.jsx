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
        <aside className="auth-brand" aria-hidden="true">
          <div className="auth-brand__logo">
            <BrandMark size={36} />
            <span>Meet2MoM</span>
          </div>
          <div className="auth-brand__copy">
            <h2>Minutes you can trace back to what was said.</h2>
            <ul>
              <li><Icon name="shield" /> Every decision and action item cites the transcript lines it comes from.</li>
              <li><Icon name="mic" /> Live meetings record only after each participant consents.</li>
              <li><Icon name="checkCircle" /> Nothing is final until a reviewer approves the minutes.</li>
            </ul>
          </div>
        </aside>

        <main className="auth-main">
          <form className="auth-card ui-enter" onSubmit={submit} aria-labelledby="auth-title">
            <div className="auth-card__logo"><BrandMark size={32} /></div>
            <h1 id="auth-title">{mode === "signin" ? "Sign in to Meet2MoM" : "Create your account"}</h1>
            <p className="auth-subtitle">
              {mode === "signin" ? "Sign in to your meetings." : "Create an account to host or join meetings."}
            </p>
            {mode === "register" && (
              <div className="ui-field">
                <label className="ui-label" htmlFor="auth-name">Name</label>
                <input id="auth-name" className="ui-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required autoComplete="name" />
              </div>
            )}
            <div className="ui-field">
              <label className="ui-label" htmlFor="auth-email">Email</label>
              <input id="auth-email" className="ui-input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoComplete="email" />
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
              />
              {mode === "register" && <span className="ui-help" id="auth-password-help">At least 8 characters.</span>}
            </div>
            {error && <div className="ui-alert ui-alert--danger auth-error" role="alert"><Icon name="error" /><span>{error}</span></div>}
            <button type="submit" className="ui-btn ui-btn--primary ui-btn--lg auth-submit" disabled={busy}>
              {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
            <button type="button" className="auth-switch" onClick={() => { setMode(mode === "signin" ? "register" : "signin"); setError(""); }}>
              {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
            </button>
          </form>
        </main>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user: state.user, authRequired: state.authRequired, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}
