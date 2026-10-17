import { useEffect, useRef, useState } from "react";

import { ApiError } from "../lib/api";
import { href, navigate, useLocation } from "../lib/router";
import { loginRequest, logoutRequest, signupRequest, useSession } from "../lib/session";
import type { MeRepo } from "../lib/session";
import styles from "./Auth.module.css";

/** The three account pages (M6): sign in, create account, account home.
 *  Forms mirror the server's rules (username 3–24 of a-z0-9_-, password
 *  10+ chars) and show the server's own message verbatim on refusal —
 *  the client never invents an error the API did not send. Only the
 *  display sentence-case is ours (Gate A: raw API-speak reads broken). */

type ErrorField = "username" | "password" | "confirm" | null;

function useTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}

/** Sentence-case and punctuate a server message for display. */
function displayMessage(error: ApiError): string {
  const text = error.message.trim();
  if (!text) return "Something went wrong. Try again.";
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/** Which field a refusal most likely belongs to — used only to point the
 *  focus ring and tint; the message itself still comes from the server. */
function fieldForMessage(message: string): ErrorField {
  const text = message.toLowerCase();
  if (text.includes("username")) return "username";
  if (text.includes("password")) return "password";
  return null;
}

function Field(props: {
  label: string;
  type: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  autoCapitalize?: string;
  spellCheck?: boolean;
  hint?: string;
  invalid?: boolean;
  inputRef?: React.RefObject<HTMLInputElement>;
  reveal?: boolean;
}) {
  const id = `field-${props.label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  const hintId = props.hint ? `${id}-hint` : undefined;
  const [shown, setShown] = useState(false);
  const type = props.reveal && shown ? "text" : props.type;
  return (
    <label className={styles.field} htmlFor={id}>
      <span className={styles.fieldLabel}>{props.label}</span>
      <span className={styles.inputWrap}>
        <input
          id={id}
          ref={props.inputRef}
          className={`${styles.input}${props.invalid ? ` ${styles.inputInvalid}` : ""}`}
          type={type}
          value={props.value}
          autoComplete={props.autoComplete}
          autoCapitalize={props.autoCapitalize}
          spellCheck={props.spellCheck}
          aria-invalid={props.invalid || undefined}
          aria-describedby={hintId}
          onChange={(event) => props.onChange(event.target.value)}
        />
        {props.reveal ? (
          <button
            type="button"
            className={styles.reveal}
            aria-pressed={shown}
            aria-label={shown ? "Hide password" : "Show password"}
            onClick={() => setShown((value) => !value)}
          >
            {shown ? "Hide" : "Show"}
          </button>
        ) : null}
      </span>
      {props.hint ? (
        <span id={hintId} className={styles.hint}>
          {props.hint}
        </span>
      ) : null}
    </label>
  );
}

function ErrorStrip({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <p className={styles.error} role="alert">
      {displayMessage(error)}
    </p>
  );
}

function CrossLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <a
      className={styles.link}
      href={href(to)}
      onClick={(event) => {
        event.preventDefault();
        navigate(href(to));
      }}
    >
      {children}
    </a>
  );
}

export function SignupView() {
  const { me } = useSession();
  useTitle("Create account — Archivist");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [errorField, setErrorField] = useState<ErrorField>(null);
  const [busy, setBusy] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  // Move focus to the field a refusal belongs to, so keyboard users do not
  // have to hunt for what changed. The message stays server-authored.
  useEffect(() => {
    if (errorField === "username") usernameRef.current?.focus();
    else if (errorField === "password") passwordRef.current?.focus();
    else if (errorField === "confirm") confirmRef.current?.focus();
  }, [errorField, error]);

  function clearError(): void {
    if (error) setError(null);
    if (errorField) setErrorField(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError(new ApiError(0, "The two passwords do not match."));
      setErrorField("confirm");
      return;
    }
    setBusy(true);
    setError(null);
    setErrorField(null);
    try {
      await signupRequest(username.trim(), password);
      navigate(href("/account", { welcome: "1" }));
    } catch (err) {
      const failure = err instanceof ApiError ? err : new ApiError(0, String(err));
      setError(failure);
      setErrorField(fieldForMessage(failure.message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.card}>
        <p className={styles.eyebrow}>Create account</p>
        <h1 className={styles.title}>Your own corner of the graph.</h1>
        <p className={styles.lead}>
          An account keeps your repositories and what the agent remembers about them
          separate from the shared demo. No email required — this is a demo box.
        </p>
        {me?.signup === "closed" ? (
          <p className={styles.notice}>
            Signups are closed on this server. The demo stays open to everyone.
          </p>
        ) : (
          <form className={styles.form} onSubmit={submit} noValidate>
            <Field
              label="Username"
              type="text"
              value={username}
              onChange={(value) => {
                setUsername(value);
                clearError();
              }}
              autoComplete="username"
              autoCapitalize="off"
              spellCheck={false}
              hint="3–24 characters: a-z, 0-9, _ or -"
              invalid={errorField === "username"}
              inputRef={usernameRef}
            />
            <Field
              label="Password"
              type="password"
              value={password}
              onChange={(value) => {
                setPassword(value);
                clearError();
              }}
              autoComplete="new-password"
              hint="at least 10 characters"
              invalid={errorField === "password"}
              inputRef={passwordRef}
              reveal
            />
            <Field
              label="Repeat password"
              type="password"
              value={confirm}
              onChange={(value) => {
                setConfirm(value);
                clearError();
              }}
              autoComplete="new-password"
              invalid={errorField === "confirm"}
              inputRef={confirmRef}
              reveal
            />
            <ErrorStrip error={error} />
            <button
              className={styles.primary}
              type="submit"
              disabled={busy || !username || !password || !confirm}
            >
              {busy ? "Creating…" : "Create account"}
            </button>
          </form>
        )}
        <p className={styles.foot}>
          Already have one? <CrossLink to="/login">Sign in</CrossLink> — or{" "}
          <CrossLink to="/overview">keep browsing the demo</CrossLink>.
        </p>
      </section>
    </div>
  );
}

export function LoginView() {
  useTitle("Sign in — Archivist");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [errorField, setErrorField] = useState<ErrorField>(null);
  const [busy, setBusy] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (errorField === "username") usernameRef.current?.focus();
    else if (errorField === "password") passwordRef.current?.focus();
  }, [errorField, error]);

  function clearError(): void {
    if (error) setError(null);
    if (errorField) setErrorField(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setErrorField(null);
    try {
      await loginRequest(username.trim(), password);
      navigate(href("/account"));
    } catch (err) {
      const failure = err instanceof ApiError ? err : new ApiError(0, String(err));
      setError(failure);
      setErrorField(fieldForMessage(failure.message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.card}>
        <p className={styles.eyebrow}>Sign in</p>
        <h1 className={styles.title}>Welcome back.</h1>
        <form className={styles.form} onSubmit={submit} noValidate>
          <Field
            label="Username"
            type="text"
            value={username}
            onChange={(value) => {
              setUsername(value);
              clearError();
            }}
            autoComplete="username"
            autoCapitalize="off"
            spellCheck={false}
            invalid={errorField === "username"}
            inputRef={usernameRef}
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={(value) => {
              setPassword(value);
              clearError();
            }}
            autoComplete="current-password"
            invalid={errorField === "password"}
            inputRef={passwordRef}
            reveal
          />
          <ErrorStrip error={error} />
          <button
            className={styles.primary}
            type="submit"
            disabled={busy || !username || !password}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className={styles.foot}>
          New here? <CrossLink to="/signup">Create an account</CrossLink> — or{" "}
          <CrossLink to="/overview">explore the demo</CrossLink>.
        </p>
      </section>
    </div>
  );
}

function RepoRow({ repo }: { repo: MeRepo }) {
  return (
    <li className={styles.repo}>
      <div className={styles.repoTop}>
        <span className={styles.repoName}>{repo.name ?? repo.id}</span>
        <span className={styles.repoStatus}>{repo.status ?? "unknown"}</span>
      </div>
      <div className={styles.repoMeta}>
        {repo.source_url ? <span>{String(repo.source_url)}</span> : null}
        <span className={styles.mono}>{repo.graph}</span>
      </div>
    </li>
  );
}

export function AccountView() {
  const { me, loaded } = useSession();
  const { query } = useLocation();
  const welcome = query.get("welcome") === "1";
  useTitle(
    me?.user ? `@${me.user.username} — Account — Archivist` : "Account — Archivist",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function signOut() {
    setBusy(true);
    setError(null);
    try {
      await logoutRequest();
      navigate(href("/overview"));
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
    } finally {
      setBusy(false);
    }
  }

  if (!loaded || me === null) {
    return (
      <div className={styles.page}>
        <section className={styles.card}>
          <p className={styles.eyebrow}>Account</p>
          <h1 className={styles.title}>
            {loaded ? "Signed out." : "Reading your session…"}
          </h1>
          {loaded ? (
            <p className={styles.lead}>
              <CrossLink to="/login">Sign in</CrossLink> to see your account, or{" "}
              <CrossLink to="/overview">browse the demo</CrossLink>.
            </p>
          ) : null}
        </section>
      </div>
    );
  }

  if (me.anonymous) {
    return (
      <div className={styles.page}>
        <section className={styles.card}>
          <p className={styles.eyebrow}>Account</p>
          <h1 className={styles.title}>You are browsing as a visitor.</h1>
          <p className={styles.lead}>
            Sign in or create an account to keep your own repositories and memories.
            The demo graph stays readable to everyone, no account needed.
          </p>
          <div className={styles.actions}>
            <button
              className={styles.primary}
              type="button"
              onClick={() => navigate(href("/login"))}
            >
              Sign in
            </button>
            {me.signup === "open" ? (
              <button
                className={styles.ghost}
                type="button"
                onClick={() => navigate(href("/signup"))}
              >
                Create account
              </button>
            ) : null}
          </div>
          <p className={styles.foot}>
            Or <CrossLink to="/overview">explore the demo</CrossLink> without one.
          </p>
        </section>
      </div>
    );
  }

  const since = new Date(me.user!.created_at * 1000).toISOString().slice(0, 10);
  return (
    <div className={styles.page}>
      <section className={styles.card}>
        <p className={styles.eyebrow}>Account</p>
        <h1 className={styles.title}>@{me.user!.username}</h1>
        {welcome ? (
          <p className={styles.success} role="status">
            Account created — welcome, @{me.user!.username}.
          </p>
        ) : null}
        <dl className={styles.facts}>
          <div className={styles.fact}>
            <dt>Member since</dt>
            <dd>{since}</dd>
          </div>
          <div className={styles.fact}>
            <dt>Account id</dt>
            <dd className={styles.mono}>{me.user!.uid}</dd>
          </div>
          <div className={styles.fact}>
            <dt>Demo graph</dt>
            <dd className={styles.mono}>{me.demo_graph}</dd>
          </div>
        </dl>

        <h2 className={styles.h2}>Your repositories</h2>
        {me.repos.length ? (
          <ul className={styles.repos}>
            {me.repos.map((repo) => (
              <RepoRow key={repo.id} repo={repo} />
            ))}
          </ul>
        ) : (
          <div className={styles.emptyPanel}>
            <p className={styles.emptyTitle}>No repositories yet.</p>
            <p className={styles.emptyBody}>
              Your own repos will land here once the ingest wizard ships — isolated
              from the shared demo, with their own graph and memory. Until then, the
              demo graph is fully open.
            </p>
            <button
              className={styles.ghost}
              type="button"
              onClick={() => navigate(href("/overview"))}
            >
              Explore the demo
            </button>
          </div>
        )}
        <ErrorStrip error={error} />
        <div className={styles.accountActions}>
          <button
            className={styles.ghost}
            type="button"
            onClick={() => navigate(href("/overview"))}
          >
            Back to the console
          </button>
          <button
            className={styles.ghost}
            type="button"
            onClick={signOut}
            disabled={busy}
          >
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </section>
    </div>
  );
}
