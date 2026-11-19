import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Run } from "./Incidents";
import type { FabricSpec } from "./TopologyPreview";
import {
  Activity,
  AgentGuide,
  ChatEvidence,
  IncidentReview,
  ProvisionPlan,
  incidentSteps,
  planActions,
  planStatuses,
  planSteps,
} from "./ChatDetails";
import type { Evidence, Plan, PlanAction, Step } from "./ChatDetails";
import { ImpactDashboard } from "./ImpactDashboard";
import { duration, type Impact } from "./impactData";

type Answer = {
  text: string;
  choices?: string[];
  evidence?: Evidence;
  action?: string;
  label?: string;
  incident_id?: string;
  impact?: boolean;
  plan?: Plan;
};

type IncidentRecord = Pick<Run, "events" | "review">;
type Follow = { id: string; from: number };

type Turn = {
  id: number;
  prompt: string;
  steps: Step[];
  answer?: Answer;
  error?: string;
  incident?: IncidentRecord;
  followIncident?: Follow;
  followPlan?: Follow & { result?: Plan };
  measurements?: Impact;
};

type Props = {
  page: string;
  request?: { text: string };
  navigate: (page: string) => void;
  signal: string;
  live: boolean;
  target: string;
  run?: Run;
  replay?: IncidentRecord & { text?: string };
  preview: (topology?: FabricSpec) => void;
  trace: () => Promise<string>;
};

const benchmarkPrompt = "Evaluate the AI operator against our incident test suite.";
const progressMediaType = "application/x-ndjson";
const errorText = (e: unknown) => e instanceof Error && e.message ? e.message : "Operator request failed.";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch("/api/" + path, { cache: "no-store", signal });
  if (!response.ok) throw Error("Controller state is unavailable.");
  return response.json();
}

async function post(path: string, body: object = {}, accept = "application/json") {
  const session = await fetch("/api/session", { cache: "no-store" });
  if (!session.ok) throw Error("The live NetGuard session is unavailable.");
  const { token } = await session.json();
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: {
      Accept: accept,
      "Content-Type": "application/json",
      "X-NetGuard-Session": token,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw Error(typeof data?.detail === "string" ? data.detail : "Operator request failed.");
  }
  return response;
}

async function readAnswer(prompt: string, selection: string, onStep: (step: Step) => void): Promise<Answer> {
  const response = await post("operator", { prompt, selection }, progressMediaType);
  const mediaType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType === "application/json") {
    const answer: Answer = await response.json();
    // JSON-only controllers identify repairs by their approval path.
    answer.incident_id ??= /^incidents\/([^/]+)\/approve$/.exec(answer.action ?? "")?.[1];
    return answer;
  }
  if (mediaType !== progressMediaType) throw Error("The Agent returned an unexpected response.");
  const reader = response.body?.getReader();
  if (!reader) throw Error("The Agent response is unavailable.");

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = done ? "" : lines.pop()!;
      for (const line of lines) {
        if (!line.trim()) continue;
        const event: { step?: Step; answer?: Answer; error?: string } = JSON.parse(line);
        if (event.step) onStep(event.step);
        if (event.error) throw Error(event.error);
        if (event.answer) return event.answer;
      }
      if (done) throw Error("The Agent response ended before an answer arrived.");
    }
  } finally {
    reader.releaseLock();
  }
}

export function Operator({
  page, request, navigate, signal, live, target, run, replay, preview, trace,
}: Props) {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [plans, setPlans] = useState<Record<string, Plan>>({});
  const [currentPlanId, setCurrentPlanId] = useState<string>();
  const [incidents, setIncidents] = useState<Record<string, IncidentRecord>>({});
  const [atLatest, setAtLatest] = useState(true);
  const working = useRef(false);
  const nextTurnId = useRef(0);
  const lastRequest = useRef<Props["request"]>();
  const planRead = useRef(0);
  const transcript = useRef<HTMLDivElement>(null);
  const messages = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const help = useRef<HTMLDialogElement>(null);
  const followLatest = useRef(true);
  const feedSize = useRef<{ width: number; height: number }>();

  const latest = turns[turns.length - 1];
  const busy = !!latest && !latest.answer && !latest.error;
  const currentPlan = currentPlanId ? plans[currentPlanId] : undefined;

  useLayoutEffect(() => {
    const feed = transcript.current;
    if (followLatest.current && feed?.clientHeight) feed.scrollTop = feed.scrollHeight;
  }, [turns, incidents, plans, page]);

  useEffect(() => {
    const feed = transcript.current;
    const content = messages.current;
    if (!feed || !content) return;
    const resize = new ResizeObserver(checkScroll);
    resize.observe(feed);
    resize.observe(content);
    return () => resize.disconnect();
  }, []);

  function jumpToLatest() {
    followLatest.current = true;
    setAtLatest(true);
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }

  function checkScroll() {
    const feed = transcript.current;
    if (!feed?.clientHeight) return;
    const { clientHeight: height, clientWidth: width } = feed;
    const resized = feedSize.current?.width !== width || feedSize.current?.height !== height;
    feedSize.current = { width, height };
    if (resized && followLatest.current) feed.scrollTop = feed.scrollHeight;
    const nearEnd = feed.scrollHeight - feed.scrollTop - height <= 24;
    followLatest.current = nearEnd;
    setAtLatest(nearEnd);
  }

  function rememberPlan(next: Plan) {
    setPlans((previous) => {
      const known = previous[next.id];
      if (known && (known.events?.length ?? 0) >= (next.events?.length ?? 0)) return previous;
      return { ...previous, [next.id]: next };
    });
  }

  async function refreshPlan(signal?: AbortSignal) {
    const revision = ++planRead.current;
    const next = await get<Plan | null>("provision", signal);
    if (signal?.aborted || revision !== planRead.current) return;
    if (next) rememberPlan(next);
    setCurrentPlanId(next?.id);
  }

  useEffect(() => {
    if (!live) return;
    const controller = new AbortController();
    const poll = () => { void refreshPlan(controller.signal).catch(() => {}); };
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [live]);

  useEffect(() => {
    preview(live && currentPlan?.phase === "preview" ? currentPlan.topology : undefined);
  }, [live, currentPlan, preview]);

  useEffect(() => {
    if (!run) return;
    setIncidents((previous) => {
      if ((previous[run.id]?.events.length ?? -1) >= run.events.length) return previous;
      return { ...previous, [run.id]: { events: run.events, review: run.review } };
    });
  }, [run]);

  function patchTurn(id: number, change: Partial<Turn> | ((turn: Turn) => Partial<Turn>)) {
    setTurns((previous) => previous.map((turn) => turn.id === id
      ? { ...turn, ...(typeof change === "function" ? change(turn) : change) }
      : turn));
  }

  function begin(prompt: string) {
    if (working.current || !prompt) return undefined;
    working.current = true;
    const id = ++nextTurnId.current;
    jumpToLatest();
    setTurns((previous) => [...previous, { id, prompt, steps: [] }]);
    return id;
  }

  function recordStep(id: number, step: Step) {
    patchTurn(id, (turn) => ({ steps: [...turn.steps, step] }));
  }

  function startStep(id: number, label: string, detail?: string) {
    recordStep(id, { label, detail, time: Date.now() / 1000 });
  }

  async function ask(text: string, diagnosis?: Run, fromInput = false) {
    const prompt = text.trim();
    const id = begin(prompt);
    if (id === undefined) return;
    if (fromInput) setInput("");

    try {
      let answer: Answer;
      if (prompt === "Inspect traffic") {
        startStep(id, replay ? "Read recorded probe" : "Run selected probe");
        answer = { text: await trace() };
      } else if (prompt === "Evaluate impact") {
        answer = live
          ? { text: "Measured workflow outcomes from the lab.", impact: true }
          : { text: "Workflow measurements are available in the live dashboard." };
      } else if (!live) {
        answer = { text: replay?.text || "Live telemetry is required for this operation." };
      } else {
        answer = await readAnswer(prompt, target, (step) => recordStep(id, step));
      }

      let measurements: Impact | undefined;
      if (answer.impact) {
        startStep(id, "Read recorded workflow measurements");
        measurements = await get<Impact>("impact").catch(() => undefined);
      }
      if (answer.plan) {
        planRead.current += 1;
        rememberPlan(answer.plan);
        setCurrentPlanId(answer.plan.id);
      }
      const incident = answer.incident_id ? incidents[answer.incident_id] : diagnosis || replay;
      patchTurn(id, { answer, incident, measurements });
    } catch (e) {
      patchTurn(id, { error: errorText(e), incident: diagnosis || replay });
    } finally {
      working.current = false;
    }
  }

  useEffect(() => {
    if (busy || !request || request === lastRequest.current) return;
    lastRequest.current = request;
    void ask(request.text);
  }, [busy, request]);

  async function applyIncident(answer: Answer) {
    if (!live || !answer.action || !answer.label) return;
    const id = begin(answer.label);
    if (id === undefined) return;
    const knownId = answer.incident_id;
    if (knownId) {
      const from = incidents[knownId]?.events.length ?? 0;
      patchTurn(id, { followIncident: { id: knownId, from } });
    }
    startStep(id, "Submit approval to the controller");
    try {
      const response = await post(answer.action);
      const data: { incident_id?: string } = await response.json();
      if (data.incident_id) {
        patchTurn(id, { followIncident: { id: data.incident_id, from: 0 } });
      }
      patchTurn(id, { answer: { text: "The controller accepted the request." } });
    } catch (e) {
      patchTurn(id, { error: errorText(e) });
    } finally {
      working.current = false;
    }
  }

  async function applyPlan(plan: Plan, action: PlanAction) {
    if (!live || plan.id !== currentPlanId) return;
    const id = begin(planActions[action]);
    if (id === undefined) return;

    const followPlan = { id: plan.id, from: plan.events?.length ?? 0 };
    patchTurn(id, { followPlan });
    startStep(id, "Submit topology request to the controller", (plan.added || []).join(", "));
    const path = "provision/" + encodeURIComponent(plan.id);
    async function readPlan() {
      const saved = await get<Plan>(path).catch(() => undefined);
      if (saved) return saved;
      const latest = await get<Plan | null>("provision").catch(() => undefined);
      return latest?.id === plan.id ? latest : undefined;
    }
    let polling = true;
    const timer = setInterval(async () => {
      const next = await readPlan();
      if (polling && next) rememberPlan(next);
    }, 1000);
    let error: string | undefined;
    try {
      await post(path + "/" + action);
    } catch (e) {
      error = errorText(e);
    } finally {
      polling = false;
      clearInterval(timer);
    }

    const result = await readPlan();
    if (result) rememberPlan(result);
    await refreshPlan().catch(() => {});
    patchTurn(id, {
      followPlan: { ...followPlan, result },
      ...(error ? { error } : {
        answer: { text: result
          ? planStatuses[result.phase] || result.phase
          : "The controller accepted the request. The plan status is unavailable." },
      }),
    });
    working.current = false;
  }

  const active = run && run.phase !== "resolved";
  const overview = active && run.events[run.events.length - 1]?.reason;
  const planOutsideChat = page === "Provisioning" && currentPlan && live
    && !["rolled_back", "cancelled"].includes(currentPlan.phase)
    && !turns.some((turn) => turn.answer?.plan?.id === currentPlan.id);
  const evaluating = latest?.prompt === benchmarkPrompt && busy;
  const benchmarkStatus = latest?.prompt === benchmarkPrompt
    ? latest.error || latest.answer?.text || latest.steps[latest.steps.length - 1]?.detail
    : undefined;

  return (
    <section className="operator" aria-label={page === "AI Impact" ? undefined : "NetGuard Agent"}>
      <div className="chat-panel">
        <div className="operator-head">
          <b className="op-icon" aria-hidden="true">✳</b>
          <div>
            <h2>NetGuard Agent</h2>
            <small>{page === "Provisioning" ? "Topology planning" : "Network operations"} · Local model</small>
          </div>
          <span className={"chat-mode" + (live ? " is-live" : "")}>
            {live ? "Live" : replay ? "Replay" : "Connecting"}
          </span>
        </div>
        <div className="chat-feed">
          <div
            className="chat-transcript"
            role="log"
            aria-label="Chat history"
            tabIndex={0}
            ref={transcript}
            onScroll={checkScroll}
          >
            <div className="chat-messages" ref={messages}>
              {!turns.length && (
                <div className="chat-welcome">
                  <h3>{active ? "Current incident" : "What would you like to check?"}</h3>
                  <p>{replay?.text || overview || signal || "Ask about a route, investigate an incident, or prepare a topology change."}</p>
                </div>
              )}
              {turns.map((turn) => {
                const answer = turn.answer;
                const plan = answer?.plan && plans[answer.plan.id];
                const followedPlan = turn.followPlan && (turn.followPlan.result || plans[turn.followPlan.id]);
                const followedIncident = turn.followIncident && incidents[turn.followIncident.id];
                const review = followedIncident?.review || turn.incident?.review;
                const verification = followedPlan?.phase === "rolled_back"
                  ? followedPlan.rollback_verification
                  : followedPlan?.phase === "verified" ? followedPlan.verification : undefined;
                const { observed, ...verificationFacts } = verification || {};
                const pending = !answer && !turn.error;
                const steps = [
                  ...turn.steps,
                  ...(followedPlan ? planSteps(followedPlan, turn.followPlan?.from) : []),
                  ...(followedIncident ? incidentSteps(followedIncident.events.slice(turn.followIncident?.from)) : []),
                ];
                const canApprove = live && latest?.id === turn.id && answer?.action && answer.label
                  && (answer.incident_id
                    ? run?.id === answer.incident_id && run.phase === "proposed"
                    : !active);
                return (
                  <article className="chat-turn" key={turn.id}>
                    <div className="chat-prompt">
                      <span className="chat-author">You</span>
                      <p>{turn.prompt}</p>
                    </div>
                    <div className="chat-reply">
                      <div className="chat-byline">
                        <span className="chat-author">NetGuard Agent</span>
                        {pending && <span className="chat-working" role="status">Working…</span>}
                      </div>
                      <Activity
                        label={turn.followPlan || turn.followIncident ? "Controller activity" : "Activity"}
                        steps={steps}
                      />
                      {answer && <p className="chat-text">{answer.text}</p>}
                      {turn.error && <p className="chat-error">{turn.error}</p>}
                      {turn.incident && <Activity label="Incident timeline" steps={incidentSteps(turn.incident.events)} />}
                      {review && <IncidentReview review={review} />}
                      {answer?.evidence && <ChatEvidence evidence={answer.evidence} />}
                      {observed !== undefined && (
                        <ChatEvidence
                          label="Verification evidence"
                          evidence={{ observed, facts: verificationFacts }}
                        />
                      )}
                      {plan && (
                        <ProvisionPlan
                          plan={plan}
                          current={live && plan.id === currentPlanId}
                          busy={busy}
                          act={(action) => applyPlan(plan, action)}
                        />
                      )}
                      {answer?.impact && (
                        <div className="chat-impact">
                          {turn.measurements ? (
                            <dl>
                              <div><dt>Correct diagnoses</dt><dd>{turn.measurements.correct}/{turn.measurements.evaluated}</dd></div>
                              <div><dt>Verified executions</dt><dd>{turn.measurements.verified}/{turn.measurements.attempted}</dd></div>
                              <div><dt>Time saved</dt><dd>{duration(turn.measurements.saved)}</dd></div>
                            </dl>
                          ) : <p>Workflow measurements are temporarily unavailable.</p>}
                          <button type="button" onClick={() => navigate("AI Impact")}>Open AI Impact</button>
                        </div>
                      )}
                      {(canApprove || !!answer?.choices?.length) && (
                        <div className="chat-actions">
                          {answer?.choices?.map((choice) => (
                            <button type="button" key={choice} disabled={busy} onClick={() => ask(choice)}>{choice}</button>
                          ))}
                          {canApprove && answer && (
                            <button type="button" className="primary" disabled={busy} onClick={() => applyIncident(answer)}>{answer.label}</button>
                          )}
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
          {!atLatest && (
            <button type="button" className="chat-jump" onClick={jumpToLatest}>↓ Latest message</button>
          )}
        </div>
        <div className="chat-composer">
          <form onSubmit={(e) => {
            e.preventDefault();
            if (busy || !input.trim()) return;
            void ask(input, undefined, true);
            composer.current?.focus({ preventScroll: true });
          }}>
            <textarea
              ref={composer}
              aria-label="Ask NetGuard"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
              maxLength={500}
              rows={2}
              placeholder="Ask about the network…"
            />
            <button type="submit" aria-label="Send request" disabled={busy || !input.trim()}>↑</button>
          </form>
          <div className="chips">
            <button type="button" disabled={busy} onClick={() => ask("Explain root cause", active ? run : undefined)}>
              Diagnose incident
            </button>
            <button type="button" disabled={busy || !live} onClick={() => ask("Add one leaf connected to both spines with BGP.")}>
              Add a leaf
            </button>
            <button type="button" disabled={busy} onClick={() => ask("Evaluate impact")}>Show impact</button>
            <button type="button" aria-haspopup="dialog" aria-controls="netguard-guide" onClick={() => help.current?.showModal()}>
              How to use NetGuard Agent
            </button>
          </div>
          <small className="chat-policy">Changes require a validated plan and approval.</small>
        </div>
        <AgentGuide dialog={help} />
      </div>
      {planOutsideChat && (
        <div className="drawer chat-current-plan">
          <ProvisionPlan plan={currentPlan} current busy={busy} showJournal act={(action) => applyPlan(currentPlan, action)} />
        </div>
      )}
      {page === "AI Impact" && (
        <ImpactDashboard
          busy={busy}
          live={live}
          evaluating={evaluating}
          status={benchmarkStatus}
          evaluate={() => ask(benchmarkPrompt)}
        />
      )}
    </section>
  );
}
