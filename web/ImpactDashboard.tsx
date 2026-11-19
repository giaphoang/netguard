import React, { useEffect, useMemo, useState } from "react";
import { ImpactBar, ImpactEvaluations } from "./ImpactCharts";
import { ImpactEvidence, RecordTime } from "./ImpactEvidence";
import {
  duration, modes, modeLabel, outcomeLabel, sampleLabel, scenarioLabels,
  type EvidenceRef, type EvidenceSelection, type Impact, type Mode, type TimingRow,
} from "./impactData";
import "./impact.css";

const groups = ["Diagnosis", "Recovery", "Provisioning"] as const;
type Group = typeof groups[number];
type OpenEvidence = (selection: EvidenceSelection) => void;

type Series = {
  mode: Mode;
  seconds: number;
  n: number;
  mechanism: string;
  runs: EvidenceRef[];
};

const groupNotes: Record<Group, string> = {
  Diagnosis: "Time from the first fault signal to recorded diagnosis.",
  Recovery: "Timing starts at fault injection. Failover measures traffic recovery. Closure includes controller verification. Bar labels show the recorded incident outcome.",
  Provisioning: "Time from request to verified deployment, including approval wait.",
};

function describeCohort(row: TimingRow, index: number) {
  const evidence = row.evidence;
  const group: Group = row.metric === "Diagnosis" || row.metric === "Provisioning" ? row.metric : "Recovery";
  const topologies = evidence?.topologies ?? [];
  const topology = topologies.map(({ routers, hosts }) =>
    sampleLabel(routers.length, "router") + (hosts ? " · " + sampleLabel(hosts.length, "host") : "")
  ).filter((name, i, names) => names.indexOf(name) === i).join(" / ");
  const topologyDetails = topologies.map(({ routers, hosts }) =>
    [...routers, ...(hosts ?? []).map(host => `host-${host.id} at leaf-${host.leaf}`)].join(", ")
  ).join(" / ");
  const scenario = evidence ? scenarioLabels[evidence.scenario] ?? evidence.scenario : row.scenario;
  const target = evidence?.scenario === "endpoint" ? "Host 2" : evidence?.leaf && evidence?.spine
    ? `Leaf ${evidence.leaf} / Spine ${evidence.spine}` : "";
  const approval = evidence ? evidence.approval === "automatic" ? "Auto-repair enabled" : "Approval required" : "—";
  const series = modes.flatMap((mode, i): Series[] => {
    const seconds = row[mode];
    if (seconds === null || row.n[i] === 0) return [];
    const runs = (evidence?.runs ?? []).filter(run => run.mode === mode).sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
    const outcomes = Array.from(new Set(runs.flatMap(run => run.outcome ? [outcomeLabel(run.outcome)] : [])));
    const mechanism = group === "Provisioning" ? "Verified deployment" : outcomes.join(" / ") || "—";
    return [{ mode, seconds, n: row.n[i], mechanism, runs }];
  });
  return {
    id: `${row.metric}:${index}`, group, metric: row.metric,
    scenario: group === "Provisioning" ? `Add ${row.scenario}` : [scenario, target].filter(Boolean).join(" · "),
    topology: topology || "—", topologyDetails, topologyCount: topologies.length, approval,
    simulation: evidence?.simulation_seconds,
    series,
  };
}

type Cohort = ReturnType<typeof describeCohort>;

function timingEvidence(cohort: Cohort, series: Series): EvidenceSelection {
  return {
    title: cohort.scenario,
    description: [
      cohort.metric, modeLabel[series.mode], series.mechanism, cohort.approval,
      cohort.simulation && `${cohort.simulation} s simulation`,
      `${duration(series.seconds, 2)} median across ${sampleLabel(series.n)}`,
      cohort.topologyDetails,
    ].filter(Boolean).join(" · "),
    records: series.runs,
  };
}

function DurationCharts({ cohorts, saved, onEvidence }: {
  cohorts: Cohort[]; saved: number | null; onEvidence: OpenEvidence;
}) {
  const [group, setGroup] = useState<Group>();
  const [compare, setCompare] = useState(false);
  const matched = cohorts.filter(cohort => cohort.series.length === 2);
  const comparing = compare && matched.length > 0;
  const dataset = comparing ? matched : cohorts;
  const category = group ?? groups.find(name => dataset.some(cohort => cohort.group === name)) ?? groups[0];
  const rows = dataset.filter(cohort => cohort.group === category);
  const peak = Math.max(0, ...rows.flatMap(cohort => cohort.series.map(series => series.seconds)));
  const maximum = peak > 4 ? Math.ceil(peak / 4) * 4 : Math.max(1, Math.ceil(peak));
  return (
    <section className="impact-panel" aria-labelledby="impact-durations-title">
      <div className="impact-panel-head">
        <div>
          <h2 id="impact-durations-title">Workflow durations</h2>
          <p>Median seconds per cohort. Select a bar to see the runs.</p>
        </div>
        {matched.length > 0 && <button type="button" className="impact-action" aria-pressed={comparing}
          onClick={() => { setCompare(!comparing); setGroup(undefined); }}>
          Matched comparison
        </button>}
      </div>
      <div className="impact-tabs" role="group" aria-label="Workflow stage">
        {groups.map(name => <button type="button" key={name} aria-pressed={name === category} onClick={() => setGroup(name)}>{name}</button>)}
      </div>
      {rows.length ? (
        <div className="impact-duration-chart">
          {rows.map(cohort => (
            <div className="impact-cohort" key={cohort.id}>
              <div className="impact-cohort-label">
                <strong>{cohort.scenario}</strong>
                <small>{cohort.metric} · {cohort.topology}</small>
                <small>{cohort.approval}{cohort.simulation ? ` · ${cohort.simulation} s simulation` : ""}</small>
              </div>
              <div>
                {cohort.series.map(series => <ImpactBar key={series.mode}
                  label={modeLabel[series.mode]} detail={`n = ${series.n} · ${series.mechanism}`}
                  value={series.seconds} maximum={maximum} display={duration(series.seconds, 2)}
                  tone={series.mode === "manual" ? "manual" : series.mechanism === "Timer restoration" ? "timer" : undefined}
                  select={() => onEvidence(timingEvidence(cohort, series))}
                />)}
              </div>
            </div>
          ))}
          <div className="impact-axis" aria-hidden="true">
            <div>{[0, 0.25, 0.5, 0.75, 1].map(fraction =>
              <span key={fraction}>{Number((maximum * fraction).toFixed(2))}{fraction === 1 ? " s" : ""}</span>
            )}</div>
          </div>
        </div>
      ) : <p className="impact-empty">No {comparing ? "matched " : ""}{category.toLowerCase()} timings recorded.</p>}
      <p className="impact-note">{groupNotes[category]}</p>
      {comparing && <p className="impact-note">Pairs share the fault target, router set, simulation duration, approval policy and recovery mechanism.</p>}
      {!matched.length && <p className="impact-note">A workflow comparison requires a matched manual baseline.</p>}
      {matched.length > 0 && saved != null && (
        <div className="impact-closure-difference">
          <span>Median difference across matched repair closures <small>Manual − AI-assisted</small></span>
          <strong>{saved > 0 ? "+" : ""}{duration(saved, 2)}</strong>
        </div>
      )}
    </section>
  );
}

function IncidentOutcomes({ impact, onEvidence }: { impact: Impact; onEvidence: OpenEvidence }) {
  const outcomes = Object.entries(impact.outcomes ?? {}).sort((a, b) => b[1] - a[1]);
  const maximum = outcomes[0]?.[1] ?? 0;
  return (
    <section className="impact-panel impact-outcome-panel" aria-labelledby="impact-outcomes-title">
      <div className="impact-panel-head">
        <div>
          <h2 id="impact-outcomes-title">Incident outcomes</h2>
          <p>{sampleLabel(outcomes.reduce((total, [, count]) => total + count, 0), "recorded incident")}</p>
        </div>
      </div>
      {outcomes.length ? <div className="impact-outcomes">
        {outcomes.map(([name, count]) => <ImpactBar key={name}
          label={outcomeLabel(name)} value={count} maximum={maximum} display={String(count)}
          tone={name === "timer_restoration" ? "timer" : name === "human_reset" ? "manual" : undefined}
          select={() => onEvidence({
            title: outcomeLabel(name), description: sampleLabel(count, "incident"),
            records: [...(impact.outcome_evidence?.[name] ?? [])].sort((a, b) => (b.at ?? 0) - (a.at ?? 0)),
          })}
        />)}
      </div> : <p className="impact-empty">No incident outcomes recorded.</p>}
      {!!impact.outcomes?.timer_restoration &&
        <p className="impact-note">Timer restoration re-enables the link when the approved duration expires.</p>}
    </section>
  );
}

function ExperimentTable({ cohorts, onEvidence }: { cohorts: Cohort[]; onEvidence: OpenEvidence }) {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [mechanism, setMechanism] = useState("");
  const rows = cohorts.flatMap(cohort => cohort.series.map(series => ({ cohort, series })));
  const mechanisms = Array.from(new Set(rows.map(({ series }) => series.mechanism))).sort();
  const search = query.trim().toLowerCase();
  const filtered = rows.filter(({ cohort, series }) =>
    (!group || cohort.group === group) &&
    (!mechanism || series.mechanism === mechanism) &&
    [cohort.scenario, cohort.metric, cohort.topology, cohort.topologyDetails, cohort.approval, series.mechanism, modeLabel[series.mode]]
      .some(value => value.toLowerCase().includes(search))
  );
  return (
    <section className="impact-panel" aria-labelledby="impact-table-title">
      <div className="impact-panel-head">
        <div>
          <h2 id="impact-table-title">Experiments</h2>
          <p>Measured cohorts and the conditions under which each timing was recorded.</p>
        </div>
      </div>
      <div className="impact-filters">
        <label>Search experiments
          <input type="search" placeholder="Scenario, topology, approval…" value={query} onChange={event => setQuery(event.target.value)} />
        </label>
        <label>Workflow stage
          <select value={group} onChange={event => setGroup(event.target.value)}>
            <option value="">All stages</option>
            {groups.map(name => <option key={name}>{name}</option>)}
          </select>
        </label>
        <label>Recovery mechanism
          <select value={mechanism} onChange={event => setMechanism(event.target.value)}>
            <option value="">All mechanisms</option>
            {mechanisms.map(name => <option key={name}>{name}</option>)}
          </select>
        </label>
      </div>
      <p className="impact-result-count" role="status">{filtered.length} of {sampleLabel(rows.length, "cohort")}</p>
      {filtered.length > 0 && <p className="impact-table-hint">Scroll the table to see every column.</p>}
      {filtered.length ? (
        <div className="impact-table-scroll" tabIndex={0} role="region" aria-label="Experiment results">
          <table className="impact-table impact-experiment-table">
            <thead><tr>
              <th scope="col">Scenario</th>
              <th scope="col">Topology</th>
              <th scope="col">Approval policy</th>
              <th scope="col">Recovery mechanism</th>
              <th scope="col">Samples</th>
              <th scope="col">Median duration</th>
              <th scope="col">Evidence</th>
            </tr></thead>
            <tbody>
              {filtered.map(({ cohort, series }) => (
                <tr key={`${cohort.id}:${series.mode}`}>
                  <th scope="row">{cohort.scenario}<small>{cohort.metric} · {modeLabel[series.mode]}</small></th>
                  <td title={cohort.topologyDetails}>{cohort.topology}
                    {cohort.topologyCount > 1 && <small>{cohort.topologyCount} recorded topologies</small>}
                  </td>
                  <td>{cohort.approval}{cohort.simulation ? <small>{cohort.simulation} s simulation</small> : null}</td>
                  <td>{series.mechanism}</td>
                  <td>{series.n}</td>
                  <td>{duration(series.seconds, 2)}</td>
                  <td><button type="button" className="impact-action" aria-haspopup="dialog"
                    aria-label={`View runs for ${cohort.scenario}, ${cohort.metric}, ${modeLabel[series.mode]}`}
                    onClick={() => onEvidence(timingEvidence(cohort, series))}>View runs</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="impact-empty">{rows.length ? "No cohorts match these filters." : "No experiment timings recorded yet."}</p>}
    </section>
  );
}

type Metric = { label: string; value: string; detail?: string; evidence?: EvidenceRef[] };

function Metrics({ metrics, onEvidence, primary = false }: { metrics: Metric[]; onEvidence: OpenEvidence; primary?: boolean }) {
  return (
    <div className={primary ? "impact-kpis impact-kpis-primary" : "impact-kpis"} aria-label={primary ? "Workflow KPIs" : "Additional workflow metrics"}>
      {metrics.map(({ label, value, detail, evidence }) => {
        const content = <>
          <span>{label}</span>
          <strong>{value.replace(/(\d)\/(\d)/, "$1 / $2")}</strong>
          {detail && <small>{detail}</small>}
        </>;
        return evidence?.length ? (
          <button type="button" className="impact-kpi" key={label} aria-haspopup="dialog"
            onClick={() => onEvidence({ title: label, description: value, records: evidence })}>
            {content}<span className="impact-kpi-arrow" aria-hidden="true">↗</span>
          </button>
        ) : <div className="impact-kpi" key={label}>{content}</div>;
      })}
    </div>
  );
}

function SupportingRecords({ impact, onEvidence }: { impact: Impact; onEvidence: OpenEvidence }) {
  return (
    <details className="impact-panel impact-support">
      <summary>Recent outcomes and investigations</summary>
      <div className="impact-support-grid">
        <section>
          <h3>Recent outcomes</h3>
          {impact.recent.length ? impact.recent.map((entry, i) => (
            <div key={entry.url ?? i} className="impact-support-row">
              <div>
                <strong>{scenarioLabels[entry.task] || entry.task}</strong>
                <small>{modeLabel[entry.mode]} · {outcomeLabel(entry.outcome)} · {duration(entry.seconds, 2)}</small>
                <RecordTime at={entry.at} />
              </div>
              {entry.url && <button type="button" className="impact-action" aria-haspopup="dialog"
                onClick={() => entry.url && onEvidence({ title: outcomeLabel(entry.outcome), records: [{ ...entry, url: entry.url }] })}>View record</button>}
            </div>
          )) : <p>No recent outcomes.</p>}
        </section>
        <section>
          <h3>Investigations</h3>
          {impact.investigations?.length ? impact.investigations.map(entry => (
            <div className="impact-support-row" key={entry.url}>
              <div>
                <button type="button" className="impact-link" aria-haspopup="dialog"
                  onClick={() => onEvidence({ title: outcomeLabel(entry.kind), records: [entry] })}>
                  {outcomeLabel(entry.kind)} <span aria-hidden="true">↗</span>
                </button>
                <p>{entry.text}</p>
              </div>
            </div>
          )) : <p>No investigations recorded.</p>}
        </section>
      </div>
    </details>
  );
}

export function ImpactDashboard({ busy, live, evaluating, status, evaluate }: {
  busy: boolean; live: boolean; evaluating: boolean; status?: string; evaluate: () => void;
}) {
  const [impact, setImpact] = useState<Impact>();
  const [error, setError] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceSelection>();
  const cohorts = useMemo(() => impact?.rows.map(describeCohort) ?? [], [impact?.rows]);
  useEffect(() => {
    if (!live) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const response = await fetch("/api/impact?evidence=true", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw Error("Measurements unavailable");
        const data: Impact = await response.json();
        if (!Array.isArray(data.rows)) throw Error("Measurements unavailable");
        if (!controller.signal.aborted) {
          setImpact(data);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 3000);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [live, evaluating]);

  if (!impact || !live) return (
    <div className="impact">
      <section className="impact-panel">
        <p className="impact-empty" role="status">
          {!live ? "Measurements are available in the live dashboard." : error ? "Measurements are temporarily unavailable." : "Loading recorded measurements…"}
        </p>
      </section>
    </div>
  );
  const meanDiagnosis = impact.kpis?.find(kpi => kpi.label === "Mean time to correct AI diagnosis");
  const autonomous = impact.kpis?.find(kpi => kpi.label === "Verified autonomous recovery");
  const escalation = impact.kpis?.find(kpi => kpi.label === "Correct required escalation");
  const policy = impact.kpis?.find(kpi => kpi.label.startsWith("Changes blocked by redundancy/availability policy"));
  return (
    <div className="impact">
      {error && <p className="impact-status" role="status">Showing the last loaded measurements. Refresh is temporarily unavailable.</p>}
      <Metrics primary onEvidence={setEvidence} metrics={[
        { label: "Correct AI diagnoses", value: `${impact.correct}/${impact.evaluated}`, detail: "Recorded AI incident reviews" },
        { label: "Verified executions", value: `${impact.verified}/${impact.attempted}`, detail: "Incident and provisioning attempts" },
        {
          label: "Verified autonomous recovery", value: autonomous?.value ?? "—", evidence: autonomous?.evidence,
          detail: autonomous?.value === "0/0" ? "No automatic AI actions recorded" : "Verified first-attempt automatic AI repairs",
        },
        { label: "Correct required escalations", value: `${impact.correct_handoffs}/${impact.required_handoffs}`, detail: "BGP and endpoint reviews", evidence: escalation?.evidence },
      ]} />
      <div className="impact-chart-grid">
        <DurationCharts cohorts={cohorts} saved={impact.saved} onEvidence={setEvidence} />
        <IncidentOutcomes impact={impact} onEvidence={setEvidence} />
      </div>
      <Metrics onEvidence={setEvidence} metrics={[
        { label: "Mean time to correct AI diagnosis", value: meanDiagnosis?.value ?? "—", evidence: meanDiagnosis?.evidence },
        { label: "Repair proposals withheld", value: String(impact.rejected) },
        { label: "AI cases escalated", value: String(impact.escalated) },
        { label: "Changes blocked by policy", value: policy?.value ?? "—", detail: "Redundancy and availability checks", evidence: policy?.evidence },
      ]} />
      <ImpactEvaluations evaluations={impact.evaluations ?? []} busy={busy} evaluating={evaluating}
        status={status} evaluate={evaluate} onEvidence={setEvidence} />
      <ExperimentTable cohorts={cohorts} onEvidence={setEvidence} />
      <SupportingRecords impact={impact} onEvidence={setEvidence} />
      {evidence && <ImpactEvidence selection={evidence} close={() => setEvidence(undefined)} />}
    </div>
  );
}
