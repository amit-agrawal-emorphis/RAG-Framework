import { useState } from "react";

const LOGO_SRC = "/header_logo.png";

function validatePassword(pwd) {
  if (pwd.length < 8) return "Password must be at least 8 characters.";
  if (!/[A-Za-z]/.test(pwd)) return "Password must include a letter.";
  if (!/[0-9]/.test(pwd)) return "Password must include a number.";
  if (!/[^A-Za-z0-9]/.test(pwd)) return "Password must include a special character.";
  return "";
}

export default function Login({ apiBase, onAuthSuccess }) {
  const [mode, setMode] = useState("login");
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  function switchMode(next) {
    setMode(next);
    setError("");
    setInfo("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setInfo("");

    const trimmedId = userId.trim();
    if (!trimmedId || !password) {
      setError("Please enter both User ID and password.");
      return;
    }
    if (mode === "signup") {
      const pwdErr = validatePassword(password);
      if (pwdErr) {
        setError(pwdErr);
        return;
      }
    }

    setBusy(true);
    try {
      const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
      const response = await fetch(`${apiBase}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: trimmedId, password })
      });

      if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try {
          const data = await response.json();
          if (data?.detail) detail = data.detail;
        } catch (_err) {
          // ignore
        }
        setError(detail);
        return;
      }

      const data = await response.json();
      onAuthSuccess(data.userId || trimmedId);
    } catch (_err) {
      setError("Unable to reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === "login";

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-brand">
          <img src={LOGO_SRC} alt="Yuktra" className="auth-logo" />
          <span className="auth-brand-divider" />
          <span className="auth-brand-tag">Equipment Intelligence</span>
        </div>

        <h2 className="auth-title">{isLogin ? "Sign in to your account" : "Create your account"}</h2>
        <p className="auth-subtitle">
          {isLogin
            ? "Enter your credentials to access the admin portal."
            : "Register a new admin to access the portal."}
        </p>

        <form onSubmit={handleSubmit} className="auth-form" noValidate>
          <label className="auth-field">
            <span>User ID</span>
            <input
              type="text"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder="e.g. admin"
              autoComplete="username"
              autoFocus
            />
          </label>
          <label className="auth-field">
            <span>Password</span>
            <div className="auth-password-wrap">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={isLogin ? "Enter your password" : "Min 8 chars · letter · number · special"}
                autoComplete={isLogin ? "current-password" : "new-password"}
              />
              <button
                type="button"
                className="auth-password-toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
              >
                {showPassword ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a19.77 19.77 0 0 1 5.06-5.94" />
                    <path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a19.77 19.77 0 0 1-3.16 4.19" />
                    <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                    <line x1="1" y1="1" x2="23" y2="23" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                )}
              </button>
            </div>
          </label>

          {error && <div className="auth-error">{error}</div>}
          {info && <div className="auth-info">{info}</div>}

          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? "Please wait…" : isLogin ? "Login" : "Sign up"}
          </button>
        </form>

        <div className="auth-toggle">
          {isLogin ? (
            <>
              New here?{" "}
              <button type="button" className="auth-link" onClick={() => switchMode("signup")}>
                Create an account
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button type="button" className="auth-link" onClick={() => switchMode("login")}>
                Sign in
              </button>
            </>
          )}
        </div>

        <div className="auth-footer">© Yuktra · Enterprise admin portal</div>
      </div>
    </div>
  );
}
