import { useState } from "react";

import { ApiError } from "../lib/api";
import { href, navigate } from "../lib/router";
import { loginRequest, logoutRequest, signupRequest, useSession } from "../lib/session";
import type { MeRepo } from "../lib/session";
import styles from "./Auth.module.css";

/** The three account pages (M6): sign in, create account, account home.
 *  Forms mirror the server's rules (username 3–24 of a-z0-9_-, password
 *  10+ chars) and show the server's own message verbatim on refusal —
 *  the client never invents an error the API did not send. */

function Field(props: {
  label: string;
  type: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  hint?: string;
}) {
  const id = `field-${props.label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <label className={styles.field} htmlFor={id}>
      <span className={styles.fieldLabel}>{props.label}</span>
      <input
        id={id}
        className={styles.input}
        type={props.type}
        value={props.value}
        autoComplete={props.autoComplete}
        onChange={(event) => props.onChange(event.target.value)}
      />
      {props.hint ? <span className={styles.hint}>{props.hint}</span> : null}
    </label>
  );
}

function ErrorStrip({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <p className={styles.error} role="alert">
      {error.message}
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
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError(new ApiError(0, "the two passwords do not match"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await signupRequest(username.trim(), password);
      navigate(href("/account"));
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
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
          <form className={styles.form} onSubmit={submit}>
            <Field
              label="Username"
              type="text"
              value={username}
              onChange={setUsername}
              autoComplete="username"
              hint="3–24 characters: a-z, 0-9, _ or -"
            />
            <Field
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              hint="at least 10 characters"
            />
            <Field
              label="Repeat password"
              type="password"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
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
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginRequest(username.trim(), password);
      navigate(href("/account"));
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.card}>
        <p className={styles.eyebrow}>Sign in</p>
        <h1 className={styles.title}>Welcome back.</h1>
        <form className={styles.form} onSubmit={submit}>
          <Field
            label="Username"
            type="text"
            value={username}
            onChange={setUsername}
            autoComplete="username"
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
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
          <h1 className={styles.title}>{loaded ? "Signed out." : "Reading your session…"}</h1>
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
          <p className={styles.lead}>
            None yet. The self-serve ingest wizard is coming next — then a repo URL
            here becomes your own graph, isolated from the demo.
          </p>
        )}
        <ErrorStrip error={error} />
        <button
          className={styles.ghost}
          type="button"
          onClick={signOut}
          disabled={busy}
        >
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </section>
    </div>
  );
}
