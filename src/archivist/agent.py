"""Tool-calling agent loop over RepoGraph (M2b).

The loop is deliberately thin: the model chooses tools, we execute them
against the graph, feed compacted envelopes back, and keep a complete trace
(step list + every Cypher query with timings) so every answer is auditable
and renderable. The same loop works with any OpenAI-compatible model (see
``archivist.llm``); tests drive it with scripted responses.

Failure philosophy: unknown tools, bad arguments, and tool errors are fed
*back to the model* as tool results — a competent model can recover — so the
loop only stops on a final answer, the step budget, or an LLM transport
failure (which is recorded on the :class:`Answer`, not raised).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from archivist.llm import LLMError, LLMResponse
from archivist.tools import RepoGraph

MAX_ROWS_IN_CONTEXT = 40  # prompt-sized payloads; count + query trail keep the rest
DEFAULT_MAX_STEPS = 8  # tool rounds before the answer is forced

FINALIZE_NUDGE = (
    "Tool budget reached. Stop calling tools and answer now from the results "
    "you already have; state plainly what could not be determined."
)

SYSTEM_PROMPT = """\
You are Archivist, an engineering-intelligence agent. You answer questions
about the repository "{repo}" by querying its knowledge graph.

Graph model: Repo ->(HEAD)-> Commit; File ->(DEFINES)-> Symbol;
Symbol ->(CALLS)-> Symbol (call counts, `weak` flags guesses);
File ->(IMPORTS)-> Module; Commit ->(MODIFIED)-> File (with +/- churn);
Author ->(AUTHORED)-> Commit; Commit ->(PARENT)-> Commit.

How to work:
- Use `overview` once for orientation, then the most specific tool.
- `callers` / `callees` walk call chains (impact analysis / dependencies);
  `owners` and `file_history` cover reviewer routing and churn;
  `search_commits` finds the "why" behind changes; `find_symbols` locates
  definitions. `query` is a read-only Cypher escape hatch for anything else.
- Answer ONLY from tool results. Never invent names, counts, or SHAs. If the
  tools cannot answer, say what is missing instead of guessing.
- Cite evidence inline: repo-relative paths (src/flask/app.py), qualnames,
  commit SHAs. Precision and honesty are the product.
- Name-guessed (weak) CALLS edges are excluded by default; only opt in with
  include_weak=true when the user explicitly wants best-effort coverage.
- Memory: `recall` searches persisted findings and past sessions — use it
  when the user references earlier work ("last time", "previously").
  `remember` stores one durable finding for later sessions: conclusions
  worth keeping, not raw query results.
- After answering, if a concrete next step is warranted, call `act` to
  record it (review a target, change a file, or add/run a test) anchored to
  the evidence you already cited.
- Keep the final answer tight: 2-6 sentences or a short list, with citations.
"""


def _fn(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
) -> dict:
    """Build one OpenAI function-tool schema (keeps the list below readable)."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _fn(
        "overview",
        "Graph at a glance: repo name/head plus node and edge counts. Call once at the start for orientation.",
    ),
    _fn(
        "find_symbols",
        "Find symbols (functions, classes, methods) by name — exact match first, else case-insensitive substring. Scope with file= when a name is common.",
        {
            "query": {
                "type": "string",
                "description": "Symbol name or a substring of it.",
            },
            "file": {
                "type": "string",
                "description": "Optional repo-relative path to scope the search.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 10).",
            },
        },
        ["query"],
    ),
    _fn(
        "callers",
        "Who calls a symbol (upstream walk over CALLS edges). Depth 1 = direct callers; 2-5 = transitive — the impact-analysis primitive.",
        {
            "symbol": {"type": "string", "description": "Symbol name or qualname."},
            "file": {
                "type": "string",
                "description": "Optional file to disambiguate the symbol.",
            },
            "depth": {"type": "integer", "description": "Walk depth 1-5 (default 1)."},
            "include_weak": {
                "type": "boolean",
                "description": "Include name-guessed edges (default false).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 25).",
            },
        },
        ["symbol"],
    ),
    _fn(
        "callees",
        "What a symbol calls (downstream walk over CALLS edges) — its dependencies, transitive up to depth 5.",
        {
            "symbol": {"type": "string", "description": "Symbol name or qualname."},
            "file": {
                "type": "string",
                "description": "Optional file to disambiguate the symbol.",
            },
            "depth": {"type": "integer", "description": "Walk depth 1-5 (default 1)."},
            "include_weak": {
                "type": "boolean",
                "description": "Include name-guessed edges (default false).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 25).",
            },
        },
        ["symbol"],
    ),
    _fn(
        "importers",
        "Files that import a dotted module path (e.g. 'flask.helpers'), with import line numbers.",
        {
            "module": {
                "type": "string",
                "description": "Dotted module path as ingested.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 50).",
            },
        },
        ["module"],
    ),
    _fn(
        "file_history",
        "Commits that touched a file, newest first, with author and +/- churn — the archaeology entry point.",
        {
            "path": {"type": "string", "description": "Repo-relative file path."},
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 20).",
            },
        },
        ["path"],
    ),
    _fn(
        "owners",
        "Reviewer candidates for a path: authors ranked by commits touching it (with first/last activity).",
        {
            "path": {"type": "string", "description": "Repo-relative file path."},
            "limit": {"type": "integer", "description": "Max rows, 1-100 (default 5)."},
        },
        ["path"],
    ),
    _fn(
        "search_commits",
        "Case-insensitive substring search over commit messages — for 'why is this like this' questions.",
        {
            "text": {"type": "string", "description": "Substring to look for."},
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 20).",
            },
        },
        ["text"],
    ),
    _fn(
        "recall",
        "Search Archivist's persisted memory: findings (facts) and past sessions, newest first. Use when the user references earlier work ('last time', 'previously') or asks what was already found. Defaults to what is true now; include_invalid=true shows full history, as_of=<epoch> asks what was true at that event time.",
        {
            "query": {
                "type": "string",
                "description": "Optional substring matched against fact text and session questions/answers.",
            },
            "include_invalid": {
                "type": "boolean",
                "description": "Include invalidated facts with their windows (default false).",
            },
            "as_of": {
                "type": "integer",
                "description": "Unix epoch seconds: what was true at that event time.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 10).",
            },
        },
    ),
    _fn(
        "remember",
        "Persist a durable finding to graph memory so later sessions can recall it. One short claim per fact; attach refs to anchor it to symbols/files/commits. Use sparingly — only conclusions worth keeping.",
        {
            "text": {"type": "string", "description": "The finding, one sentence."},
            "kind": {
                "type": "string",
                "description": "Category, e.g. finding / reviewer / convention.",
            },
            "refs": {
                "type": "array",
                "items": {"type": "object"},
                "description": 'Graph anchors, e.g. [{"kind": "symbol", "file": ..., "qualname": ...}, {"kind": "file", "path": ...}, {"kind": "commit", "sha": ...}].',
            },
        },
        ["text"],
    ),
    _fn(
        "act",
        "Record a concrete next step the evidence warrants — review a target, change a file, or add/run a test — anchored to the cited evidence. Use after answering when a follow-up is warranted.",
        {
            "type": {
                "type": "string",
                "enum": ["review", "change", "test"],
                "description": "Action type.",
            },
            "summary": {
                "type": "string",
                "description": "One-sentence description of the step.",
            },
            "targets": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Graph anchors the action targets, e.g. symbols/files/commits.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Graph anchors cited as evidence for this step.",
            },
        },
        ["type", "summary"],
    ),
    _fn(
        "query",
        "Read-only Cypher escape hatch for questions the curated tools do not fit. Writes are rejected by the database.",
        {
            "cypher": {
                "type": "string",
                "description": "A read-only Cypher statement.",
            },
            "params": {"type": "object", "description": "Optional query parameters."},
            "limit": {
                "type": "integer",
                "description": "Max rows, 1-100 (default 100).",
            },
        },
        ["cypher"],
    ),
]

TOOL_METHODS: dict[str, str] = {
    schema["function"]["name"]: schema["function"]["name"] for schema in TOOL_SCHEMAS
}


def _compact(envelope: dict, rows_cap: int = MAX_ROWS_IN_CONTEXT) -> dict:
    """Envelope minus the query audit trail, with a row cap.

    The model sees tool/status/count/notes/results; the Cypher + timings stay
    in the Answer trace — they are for humans and the UI, not the model, and
    would needlessly bloat context.
    """
    out = {
        "tool": envelope.get("tool"),
        "status": envelope.get("status"),
        "count": envelope.get("count"),
        "notes": list(envelope.get("notes") or []),
    }
    results = envelope.get("results")
    if isinstance(results, list) and len(results) > rows_cap:
        out["results"] = results[:rows_cap]
        out["notes"].append(f"truncated {len(results)} rows to {rows_cap} for context")
    else:
        out["results"] = results
    return out


@dataclass
class Answer:
    """Final answer plus the full audit trail of how it was produced."""

    question: str
    text: str
    steps: list[dict] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    usage: dict = field(default_factory=dict)
    partial: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Agent:
    """Runs questions through an LLM with RepoGraph tools; records everything."""

    def __init__(
        self,
        repo: RepoGraph,
        llm: Any,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        repo_label: str | None = None,
        memory: Any = None,
        actions: Any = None,
        episode_id: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
    ) -> None:
        """``system_prompt`` overrides the default text; ``tools`` restricts
        the tool set (both are how ``archivist.crew`` builds role-scoped
        agents — least privilege per role, enforced here, not by convention).
        """
        self.repo = repo
        self.llm = llm
        self.max_steps = max(1, int(max_steps))  # 0/negatives would run nothing
        self.repo_label = repo_label or getattr(repo, "graph_name", "repo")
        self.memory = memory  # optional memory.Memory; enables the `remember` tool
        self.actions = actions  # optional actions.Actions; enables the `act` tool
        self.episode_id = episode_id  # provenance for facts this run records
        self.system_prompt = system_prompt
        if tools is None:
            self.tool_names = set(TOOL_METHODS)
            if actions is None:
                self.tool_names.discard("act")
        else:
            unknown = sorted(set(tools) - set(TOOL_METHODS))
            if unknown:
                raise ValueError(f"unknown tool names: {unknown}")
            self.tool_names = set(tools)
        self.schemas = [
            schema
            for schema in TOOL_SCHEMAS
            if schema["function"]["name"] in self.tool_names
        ]

    def system_message(self) -> dict:
        if self.system_prompt is not None:
            return {"role": "system", "content": self.system_prompt}
        return {"role": "system", "content": SYSTEM_PROMPT.format(repo=self.repo_label)}

    def ask(self, question: str) -> Answer:
        """Run one question through the tool-calling loop."""
        answer = Answer(question=question, text="")
        messages: list[dict] = [
            self.system_message(),
            {"role": "user", "content": question},
        ]
        for round_ in range(self.max_steps + 1):
            finalizing = round_ == self.max_steps
            if finalizing:
                messages.append({"role": "system", "content": FINALIZE_NUDGE})
            try:
                response = self.llm.chat(
                    messages, tools=None if finalizing else self.schemas
                )
            except LLMError as exc:
                answer.error = str(exc)
                answer.text = f"(LLM failure: {exc})"
                answer.partial = True
                break
            answer.llm_calls += 1
            if response.tool_calls and not finalizing:
                messages.append(self._assistant_message(response))
                for call in response.tool_calls:
                    payload, step = self._execute(call, round_, answer)
                    answer.steps.append(step)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": payload}
                    )
            else:
                content = response.content
                text = content.strip() if isinstance(content, str) else ""
                if text:
                    answer.text = text
                    answer.partial = finalizing
                else:  # an empty 200 must not look like a successful answer
                    answer.text = "(no answer produced)"
                    answer.partial = True
                    answer.error = answer.error or "empty response from model"
                break
        answer.usage = dict(getattr(self.llm, "usage", {}) or {})
        return answer

    # -- internals ---------------------------------------------------------

    def _assistant_message(self, response: LLMResponse) -> dict:
        return {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.raw_arguments},
                }
                for call in response.tool_calls
            ],
        }

    def _execute(self, call, round_: int, answer: Answer) -> tuple[str, dict]:
        """Run one tool call; return (payload JSON for the model, step record).

        The step keeps the compacted result the model saw, so the Answer's
        trace can be rendered (CLI panel, UI) without re-running any query.
        """
        step: dict = {
            "round": round_,
            "tool": call.name,
            "params": call.arguments or {},
            "status": "error",
            "count": None,
            "ms": 0,
            "notes": [],
            "error": None,
            "result": None,
        }

        def payload_for(data: dict) -> str:
            step["result"] = data
            return json.dumps(data, default=str)

        if call.parse_error:
            step["params"] = {"raw": call.raw_arguments}
            step["error"] = f"arguments were not valid JSON: {call.parse_error}"
            return payload_for({"status": "error", "error": step["error"]}), step
        if call.name not in TOOL_METHODS:
            step["error"] = f"unknown tool {call.name!r}"
            return (
                payload_for(
                    {
                        "status": "error",
                        "error": step["error"],
                        "available": sorted(TOOL_METHODS),
                    }
                ),
                step,
            )
        if call.name not in self.tool_names:
            step["error"] = f"tool {call.name!r} is not available to this agent"
            return (
                payload_for(
                    {
                        "status": "error",
                        "error": step["error"],
                        "available": sorted(self.tool_names),
                    }
                ),
                step,
            )
        started = time.perf_counter()
        mark = self._trace_mark()
        try:
            envelope = self._dispatch(call.name, call.arguments or {})
        except TypeError as exc:  # bad/missing parameters: let the model retry
            step["error"] = f"bad arguments: {exc}"
            step["ms"] = round((time.perf_counter() - started) * 1000)
            self._harvest_queries(mark, round_, call.name, answer)
            return payload_for({"status": "error", "error": step["error"]}), step
        except Exception as exc:  # noqa: BLE001 — surfaced to the model, it can adapt
            step["error"] = f"{type(exc).__name__}: {exc}"[:300]
            step["ms"] = round((time.perf_counter() - started) * 1000)
            self._harvest_queries(mark, round_, call.name, answer)
            return payload_for({"status": "error", "error": step["error"]}), step
        step["ms"] = round((time.perf_counter() - started) * 1000)
        step["status"] = envelope.get("status", "ok")
        step["count"] = envelope.get("count")
        step["notes"] = list(envelope.get("notes") or [])
        if step["status"] == "error" and not step["error"]:
            step["error"] = "; ".join(step["notes"]) or "tool error"
        for query in envelope.get("queries") or []:
            answer.queries.append({"round": round_, "tool": call.name, **query})
        return payload_for(_compact(envelope)), step

    # -- tool dispatch ------------------------------------------------------

    def _dispatch(self, name: str, arguments: dict) -> dict:
        """Route one tool call; ``remember``/``act`` write through memory."""
        if name == "remember":
            return self._remember(arguments)
        if name == "act":
            return self._act(arguments)
        return getattr(self.repo, TOOL_METHODS[name])(**arguments)

    def _act(self, arguments: dict) -> dict:
        """Envelope for the write tool: plan one action via ``self.actions``.

        Validation errors come back as error envelopes (not exceptions) so
        the model can recover, mirroring the read tools' contract. The write
        carries no ``queries`` — it is memory, not a read of the graph.
        """

        def shell(status: str, results: Any, notes: list[str]) -> dict:
            return {
                "tool": "act",
                "status": status,
                "params": arguments,
                "count": None,
                "results": results,
                "notes": notes,
                "queries": [],
            }

        if self.actions is None:
            return shell("error", None, ["actions are not enabled for this run"])
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            return shell("error", None, ["summary is required"])
        type_ = str(arguments.get("type") or "").strip().lower()
        targets = [t for t in (arguments.get("targets") or []) if isinstance(t, dict)]
        evidence = [e for e in (arguments.get("evidence") or []) if isinstance(e, dict)]
        try:
            result = self.actions.plan_action(
                summary=summary,
                type=type_,
                targets=targets,
                evidence=evidence,
                episode_id=self.episode_id,
            )
        except ValueError as exc:
            return shell("error", None, [str(exc)])
        notes = [f"planned as {result['fact_id']}"]
        if self.episode_id and not result["provenance"]:
            notes.append("episode link missing — action has no provenance")
        if result["refs_missing"]:
            notes.append(
                "missing refs: " + ", ".join(str(m) for m in result["refs_missing"])
            )
        return {**shell("ok", result, notes), "count": 1}

    def _remember(self, arguments: dict) -> dict:
        """Envelope for the write tool: persist one fact via ``self.memory``.

        Errors come back as envelopes (not exceptions) so the model can
        recover, mirroring the read tools' contract. The write carries no
        ``queries`` — it is memory, not a read of the graph.
        """

        def shell(status: str, results: Any, notes: list[str]) -> dict:
            return {
                "tool": "remember",
                "status": status,
                "params": arguments,
                "count": None,
                "results": results,
                "notes": notes,
                "queries": [],
            }

        if self.memory is None:
            return shell("error", None, ["memory is not enabled for this run"])
        text = str(arguments.get("text") or "").strip()
        if not text:
            return shell("error", None, ["text is required"])
        raw_refs = arguments.get("refs") or []
        refs = [ref for ref in raw_refs if isinstance(ref, dict)]
        result = self.memory.remember_fact(
            text=text,
            kind=str(arguments.get("kind") or "finding"),
            episode_id=self.episode_id,
            refs=refs or None,
        )
        notes = [f"recorded as {result['fact_id']}"]
        if self.episode_id and not result["provenance"]:
            notes.append("episode link missing — fact has no provenance")
        if result["refs_missing"]:
            notes.append(
                "missing refs: " + ", ".join(str(m) for m in result["refs_missing"])
            )
        if len(raw_refs) - len(refs):
            notes.append(f"{len(raw_refs) - len(refs)} non-object refs ignored")
        return {**shell("ok", result, notes), "count": 1}

    # -- trace helpers -----------------------------------------------------

    def _trace_mark(self) -> int | None:
        """Index into ``repo.trace`` before a tool call, when supported."""
        mark = getattr(self.repo, "_mark", None)
        return mark() if callable(mark) else None

    def _harvest_queries(
        self, mark: int | None, round_: int, tool: str, answer: Answer
    ) -> None:
        """Copy a failed tool's already-executed queries into the trace.

        Tools append to ``repo.trace`` as they query, so even when one raises
        mid-call the executed statements stay auditable on the Answer.
        """
        trace = getattr(self.repo, "trace", None)
        if mark is None or not isinstance(trace, list):
            return
        for record in trace[mark:]:
            if is_dataclass(record):
                payload = asdict(record)
            elif isinstance(record, dict):
                payload = dict(record)
            else:
                continue
            answer.queries.append({"round": round_, "tool": tool, **payload})
