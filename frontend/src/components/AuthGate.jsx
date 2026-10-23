// Phase 7: sign-in screen in front of the app. The backend enforces access;
// this only collects credentials when the API answers 401. With
// AUTH_REQUIRED=false the app renders exactly as before.
import { createContext, useContext, useEffect, useState } from "react";
import { getMe, login, logout, register } from "../services/api";

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
    return <div className="auth-shell" role="status">Loading…</div>;
  }

  if (state.status === "signed-out") {
    return (
      <div className="auth-shell">
        <form className="auth-card" onSubmit={submit} aria-labelledby="auth-title">
          <h1 id="auth-title">Meet2MoM</h1>
          <p className="auth-subtitle">
            {mode === "signin" ? "Sign in to your meetings." : "Create an account to host or join meetings."}
          </p>
          {mode === "register" && (
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required autoComplete="name" />
            </label>
          )}
          <label>
            Email
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoComplete="email" />
          </label>
          <label>
            Password
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
            />
          </label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button type="submit" disabled={busy}>
            {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
          <button type="button" className="auth-switch" onClick={() => { setMode(mode === "signin" ? "register" : "signin"); setError(""); }}>
            {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user: state.user, authRequired: state.authRequired, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}
