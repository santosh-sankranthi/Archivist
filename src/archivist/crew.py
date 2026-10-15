"""Multi-agent crew (M3b): planner → investigator → verifier.

One question, three roles, one shared graph. The **planner** turns the
question into an executable investigation plan; the **investigator** executes
it with the full tool suite; the **verifier** re-runs the load-bearing checks,
classifies claims (confirmed / corrected / unverifiable), and owns the final
answer. M2's single loop remains the unit of work — the crew is coordination
on top of it, not a second engine.

Why this shape:

* Roles are **tool-scoped for real**: the planner sees orientation tools only
  (overview / find_symbols / recall), the verifier sees every read tool but
  never ``remember`` — verification must not author new claims — and the
  investigator runs unrestricted. The Agent enforces the scope even when a
  model reaches for a forbidden tool, so the boundary is a fact, not a prompt
  suggestion.
* Handoffs are **data**: each phase runs as an ``agent-session`` episode
  (the M3a model, with ``role`` + ``run`` props) and consecutive phases are
  linked by ``(:Episode)-[:HANDOFF {note}]->(:Episode)`` — the crew's own
  coordination history is graph state, queryable like everything else
  ("who worked on this, in what order, passing what summary?").
* The crew **never continues past a failed phase**: a broken handoff is
  reported as a partial result, not papered over. Step-budget "partial"
  phases still hand off — their text stands on its own, and the verifier
  is there precisely to catch what an under-budget phase got wrong.

Orchestration is deterministic and thin; judgement is confined to each
phase's model call. Scripted models (tests, demo) drive the exact same code
paths a live model would.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from archivist.agent import DEFAULT_MAX_STEPS, SYSTEM_PROMPT, Agent
from archivist.memory import stamp_utc

PLANNER_MAX_STEPS = 4  # a plan needs orientation, not a deep dive

#: Tool scopes per role (None = everything). Least privilege is the point:
#: the verifier must not author claims (no ``remember``), and the planner
#: has no business running walks before there is a plan.
PLANNER_TOOLS = ["overview", "find_symbols", "recall"]
VERIFIER_TOOLS = [
    "overview",
    "find_symbols",
    "callers",
    "callees",
    "importers",
    "file_history",
    "owners",
    "search_commits",
    "recall",
    "query",
]
TOOL_SETS: dict[str, list[str] | None] = {
    "planner": PLANNER_TOOLS,
    "investigator": None,
    "verifier": VERIFIER_TOOLS,
}

PLANNER_PROMPT = """\
You are the Planner in a three-agent crew (planner → investigator → verifier)
studying the repository "{repo}". Your job is a short, executable plan — not
the answer.

Work in two moves:
1. Orient: `overview` once for the lay of the land; `find_symbols` to pin any
   name the question mentions; `recall` to check what earlier sessions
   already found.
2. Write the plan, exactly in this shape:
   Objective: one line, restated precisely.
   Steps: 2-5 numbered sub-questions — each names the evidence that settles
   it (impact / ownership / archaeology / memory) and the tool to run.
   Verification: which claims the verifier must spot-check, and how.

Only plan work the current toolset can actually do; use real symbol names,
paths, or files. Keep it under 180 words. No preamble, no filler.
"""

INVESTIGATOR_SUFFIX = """\

You are the INVESTIGATOR in a crew, executing the plan you were handed.
Run the queries yourself — the plan is not evidence. When the graph
disagrees with the plan, follow the graph and say so. Every finding carries
exact citations (paths, qualnames, SHAs, counts).
"""

VERIFIER_PROMPT = """\
You are the Verifier in a three-agent crew (planner → investigator → verifier)
studying the repository "{repo}". You own the FINAL answer.

You receive the question, the planner's plan, and the investigator's answer.
Investigator claims may be wrong — verify the load-bearing ones before
anyone believes them:
- Re-run the decisive queries yourself: the top claim, any number used in
  the conclusion, and the ownership/reviewer claim when present.
- Classify each checked claim: confirmed / corrected / unverifiable, with
  the evidence your run produced. State corrected claims in corrected form.
- Then give the final answer: 2-6 sentences or a short list; every number
  and name from YOUR verification runs; a one-line limitations note where
  the graph is honestly incomplete. Never present an unchecked claim as
  fact; mark what you could not check.

Be concrete and terse.
"""


def _clip(text: str, width: int = 240) -> str:
    """One-line digest for handoff notes (full text lives in the episodes)."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


@dataclass
class CrewResult:
    """Final answer plus the crew's phase/handoff record.

    ``phases`` is a compact per-role summary (stable for JSON/logging);
    ``answers`` keeps the full :class:`~archivist.agent.Answer` objects so the
    CLI/UI can render each phase's complete trace without re-querying.
    """

    question: str
    text: str = ""
    run_id: str | None = None
    phases: list[dict] = field(default_factory=list)
    handoffs: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    partial: bool = False
    error: str | None = None
    answers: list = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "text": self.text,
            "run_id": self.run_id,
            "phases": self.phases,
            "handoffs": self.handoffs,
            "llm_calls": self.llm_calls,
            "partial": self.partial,
            "error": self.error,
            "answers": [answer.to_dict() for answer in self.answers],
        }


class Crew:
    """planner → investigator → verifier over one RepoGraph (+ optional Memory).

    ``memory`` records each phase as an episode and links handoffs; without
    it the crew still runs and reports handoffs in the result (just not
    persisted). ``run_id`` groups the phases and makes episode ids
    deterministic (``{run_id}:{role}``) — re-running a demo converges.
    """

    def __init__(
        self,
        repo,
        llm,
        *,
        memory=None,
        max_steps: int = DEFAULT_MAX_STEPS,
        run_id: str | None = None,
        source: str = "crew.py",
    ) -> None:
        self.repo = repo
        self.llm = llm
        self.memory = memory
        self.max_steps = max(1, int(max_steps))
        self.run_id = run_id
        self.source = source

    def run(self, question: str) -> CrewResult:
        """Ask ``question`` through all three phases; stop honestly on failure."""
        result = CrewResult(question=question)
        run_id = self.run_id or f"crew:{stamp_utc(int(time.time()))}:{uuid4().hex[:8]}"
        result.run_id = run_id

        plan = self._phase("planner", question, result, run_id)
        if plan is None:
            return result

        self._handoff("planner", "investigator", _clip(plan.text), result)
        inv_input = (
            f"QUESTION: {question}\n\n"
            "PLAN from the planner (execute it; adapt only if the graph "
            f"disagrees):\n{plan.text}"
        )
        investigation = self._phase("investigator", inv_input, result, run_id)
        if investigation is None:
            return result

        self._handoff("investigator", "verifier", _clip(investigation.text), result)
        ver_input = (
            f"QUESTION: {question}\n\n"
            f"PLANNER'S PLAN:\n{plan.text}\n\n"
            "INVESTIGATOR'S ANSWER (verify before anyone believes it):\n"
            f"{investigation.text}"
        )
        verdict = self._phase("verifier", ver_input, result, run_id)
        if verdict is None:
            return result

        result.text = verdict.text
        return result

    # -- internals ---------------------------------------------------------

    def _prompt_for(self, role: str) -> str:
        label = getattr(self.repo, "graph_name", "repo")
        if role == "planner":
            return PLANNER_PROMPT.format(repo=label)
        if role == "investigator":
            return SYSTEM_PROMPT.format(repo=label) + INVESTIGATOR_SUFFIX
        return VERIFIER_PROMPT.format(repo=label)

    def _phase(self, role: str, phase_input: str, result: CrewResult, run_id: str):
        """Run one role; append its record to ``result``; None = abort signal.

        A phase aborts the crew only on a hard error (transport failure,
        empty model output). Step-budget partials are reported as ``partial``
        but still hand off: their text is real, and the next role's job is
        exactly to weigh it.
        """
        steps = (
            min(self.max_steps, PLANNER_MAX_STEPS)
            if role == "planner"
            else self.max_steps
        )
        episode_id: str | None = None
        if self.memory is not None:
            episode_id = f"{run_id}:{role}"
            self.memory.record_episode(
                kind="agent-session",
                id=episode_id,
                source=self.source,
                role=role,
                run=run_id,
                question=phase_input,
            )
            self._link_incoming(role, result, run_id)
        started = time.perf_counter()
        answer = Agent(
            self.repo,
            self.llm,
            max_steps=steps,
            memory=self.memory,
            episode_id=episode_id,
            system_prompt=self._prompt_for(role),
            tools=TOOL_SETS[role],
        ).ask(phase_input)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if self.memory is not None:
            self.memory.record_episode(
                id=episode_id,
                answer=answer.text,
                partial=answer.partial,
                error=answer.error,
                llm_calls=answer.llm_calls,
                steps=len(answer.steps),
                queries=len(answer.queries),
                duration_ms=elapsed_ms,
            )

        status = "error" if answer.error else ("partial" if answer.partial else "ok")
        result.phases.append(
            {
                "role": role,
                "status": status,
                "episode_id": episode_id,
                "text": answer.text,
                "error": answer.error,
                "steps": len(answer.steps),
                "llm_calls": answer.llm_calls,
                "queries": len(answer.queries),
                "ms": elapsed_ms,
            }
        )
        result.answers.append(answer)
        result.llm_calls += answer.llm_calls
        if status != "ok":
            result.partial = True
        if answer.error:
            result.error = f"{role}: {answer.error}"
            return None
        return answer

    def _handoff(
        self,
        from_role: str,
        to_role: str,
        note: str,
        result: CrewResult,
    ) -> None:
        """Record one handoff (who passed what to whom) on the result.

        The graph edge is *not* written here: the target episode only exists
        from its own phase's start, so :meth:`_link_incoming` wires the edge
        at that moment — an edge therefore means exactly "this work passed
        into a phase that started". A phase that never starts leaves no edge.
        """
        result.handoffs.append({"from": from_role, "to": to_role, "note": note})

    def _link_incoming(self, role: str, result: CrewResult, run_id: str) -> None:
        """Wire the pending handoff edge into ``role``'s just-created episode.

        Called at phase start, when both endpoints exist (source phase
        finished, target episode recorded). Idempotent on re-runs; the
        outcome is recorded on the handoff as ``linked`` so a dropped edge
        can never look like a clean run (silent filtering is a bug class
        this project treats as a finding, not a detail).
        """
        if not result.handoffs:
            return
        last = result.handoffs[-1]
        if last["to"] != role:
            return
        last["linked"] = self.memory.link_handoff(
            f"{run_id}:{last['from']}", f"{run_id}:{last['to']}", note=last["note"]
        )
