/** Settings panel (M7a): a visitor brings their own LLM key. The base URL /
 *  model / key are stored in the browser and sent per request — the server
 *  never stores or logs them. "Test key" runs the server's tiny-completion
 *  probe (`POST /api/llm/test`) with exactly the settings an ask would use,
 *  so a pass here means asks will work. */

import type { FormEvent } from "react";
import { useState } from "react";

import { ApiError, postJSON } from "../lib/api";
import {
  clearLLMKey,
  hasLLMKey,
  saveLLMKey,
  type LLMKeySettings,
  llmHeadersFor,
} from "../lib/llmKeys";
import styles from "./Settings.module.css";

interface TestResult {
  ok: boolean;
  model?: string | null;
  reply?: string;
  error?: string;
  ms?: number;
}

export function Settings({ onClose }: { onClose: () => void }) {
  const [base, setBase] = useState(() => (typeof window === "undefined" ? "" : (window.localStorage.getItem("archivist.llm.base") ?? "")));
  const [model, setModel] = useState(() => (typeof window === "undefined" ? "" : (window.localStorage.getItem("archivist.llm.model") ?? "")));
  const [key, setKey] = useState(() => (typeof window === "undefined" ? "" : (window.localStorage.getItem("archivist.llm.key") ?? "")));
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  function apply(settings: LLMKeySettings): void {
    saveLLMKey(settings);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1800);
  }

  function onSubmit(event: FormEvent): void {
    event.preventDefault();
    apply({ base, model, key });
  }

  function onClear(): void {
    clearLLMKey();
    setBase("");
    setModel("");
    setKey("");
    setTestResult(null);
    setTestError(null);
  }

  async function onTest(): Promise<void> {
    const settings: LLMKeySettings = { base, model, key };
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      setTestResult(
        await postJSON<TestResult>("/api/llm/test", {}, llmHeadersFor(settings)),
      );
    } catch (error) {
      setTestError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setTesting(false);
    }
  }

  const anyKey = hasLLMKey();

  return (
    <div className={styles.backdrop} onClick={onClose}>
      <section
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label="Settings — bring your own LLM key"
        onClick={(event) => event.stopPropagation()}
      >
        <header className={styles.head}>
          <div>
            <span className={styles.eyebrow}>Settings</span>
            <h2 className={styles.title}>Bring your own key</h2>
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="Close settings">
            ×
          </button>
        </header>

        <p className={styles.lede}>
          The shared demo key is capped per visitor. Add your own OpenAI-compatible
          endpoint to keep asking past the cap. These values stay in your browser and are
          sent with each ask — the server never stores or logs them.
        </p>

        <form className={styles.form} onSubmit={onSubmit}>
          <label className={styles.field}>
            <span className={styles.label}>Base URL</span>
            <input
              className={styles.input}
              type="text"
              value={base}
              onChange={(event) => setBase(event.target.value)}
              placeholder="https://api.openai.com/v1"
              autoComplete="off"
              spellCheck={false}
            />
            <span className={styles.hint}>https:// required (loopback http is allowed)</span>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Model</span>
            <input
              className={styles.input}
              type="text"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="gpt-4o-mini"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>API key</span>
            <input
              className={styles.input}
              type="password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="sk-…"
              autoComplete="off"
              spellCheck={false}
            />
            <span className={styles.hint}>never logged; sent only to the endpoint above</span>
          </label>

          <div className={styles.actions}>
            <button type="submit" className={styles.primary} disabled={!base.trim() || !model.trim()}>
              {saved ? "saved ✓" : "save settings"}
            </button>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => void onTest()}
              disabled={testing || !base.trim() || !model.trim()}
            >
              {testing ? "testing…" : "Test key"}
            </button>
            {anyKey ? (
              <button type="button" className={styles.ghost} onClick={onClear}>
                clear
              </button>
            ) : null}
          </div>
        </form>

        {testResult ? (
          <p className={testResult.ok ? styles.ok : styles.err} role="status">
            {testResult.ok
              ? `ok — ${testResult.model ?? "model"} replied “${(testResult.reply ?? "").slice(0, 60)}” in ${testResult.ms ?? "?"}ms`
              : (testResult.error ?? "test failed")}
          </p>
        ) : null}
        {testError ? <p className={styles.err} role="status">{testError}</p> : null}
      </section>
    </div>
  );
}
