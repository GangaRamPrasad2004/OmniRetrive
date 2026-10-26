import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { login, register, sessionError, clearSessionError } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const destination = (location.state as { from?: string } | null)?.from ?? "/";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    clearSessionError();
    setBusy(true);
    try {
      if (mode === "register") await register(email, password, fullName);
      else await login(email, password);
      navigate(destination, { replace: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-story">
        <div className="auth-brand"><span className="brand-mark">O</span> OmniRetrive</div>
        <div className="auth-story-copy">
          <span className="eyebrow">KNOWLEDGE, WITH CONTEXT</span>
          <h1>Your documents.<br />Ready to answer.</h1>
          <p>Bring your PDFs together, find the details that matter, and get answers grounded in your team's documents.</p>
        </div>
        <div className="auth-footnote">Private workspaces · Source-linked answers</div>
      </section>
      <section className="auth-form-side">
        <form className="auth-card" onSubmit={handleSubmit}>
          <div className="auth-heading">
            <span className="eyebrow">{mode === "login" ? "WELCOME BACK" : "GET STARTED"}</span>
            <h2>{mode === "login" ? "Sign in to your account" : "Create your account"}</h2>
            <p>{mode === "login" ? "Continue to your document workspace." : "Create an account and your first workspace."}</p>
          </div>
          {(error || sessionError) && <div className="alert alert-error" role="alert">{error || sessionError}</div>}
          {mode === "register" && (
            <label className="field">Full name<input autoComplete="name" value={fullName} onChange={(event) => setFullName(event.target.value)} required maxLength={120} /></label>
          )}
          <label className="field">Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <label className="field">Password<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} required minLength={mode === "register" ? 8 : undefined} /></label>
          <button className="button button-primary button-wide" type="submit" disabled={busy}>
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
          <p className="auth-switch">
            {mode === "login" ? "New to OmniRetrive?" : "Already have an account?"}
            {" "}<button type="button" className="text-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </form>
      </section>
    </main>
  );
}
