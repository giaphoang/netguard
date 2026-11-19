export type Mode = "manual" | "assisted";

export type EvidenceRef = {
  url: string;
  label?: string;
  at?: number;
  task?: string;
  mode?: Mode | "rule";
  outcome?: string;
  seconds?: number | null;
};

export type RecentOutcome = Omit<EvidenceRef, "url" | "label"> & {
  url?: string;
  task: string;
  mode: Mode | "rule";
  outcome: string;
};

export type RecordedTopology = {
  routers: string[];
  hosts?: { id: number; leaf: number }[];
};

export type TimingRow = {
  metric: string;
  scenario: string;
  manual: number | null;
  assisted: number | null;
  n: [number, number];
  evidence?: {
    scenario: string;
    leaf?: number;
    spine?: number;
    simulation_seconds?: number | null;
    approval: "automatic" | "approval";
    topologies: RecordedTopology[];
    runs: EvidenceRef[];
  };
};

export type EvaluationScore = "diagnosis_correct" | "escalation_correct";

export type EvaluationCase = {
  id: string;
  kind: string;
  version: string;
  passed?: boolean;
  diagnosis_correct?: boolean;
  error?: string;
  latency_s?: number;
};

export type Evaluation = {
  id: string;
  observed: number;
  summary: Record<string, {
    n: number;
    diagnosis_correct: number;
    escalation_correct: number;
    unsafe_proposals: number;
    unsupported_citations?: number;
    mean_latency_s: number;
    model_calls: number;
  }>;
  results: EvaluationCase[];
  limits: string;
  url: string;
};

export type Impact = {
  evaluations?: Evaluation[];
  outcomes?: Record<string, number>;
  outcome_evidence?: Record<string, EvidenceRef[]>;
  kpis?: { label: string; value: string; evidence: EvidenceRef[] }[];
  investigations?: { kind: string; text: string; url: string }[];
  saved: number | null;
  correct: number;
  evaluated: number;
  verified: number;
  attempted: number;
  rejected: number;
  escalated: number;
  required_handoffs: number;
  correct_handoffs: number;
  rows: TimingRow[];
  recent: RecentOutcome[];
};

export type EvidenceSelection = {
  title: string;
  description?: string;
  records: EvidenceRef[];
  evaluation?: { kinds?: string[]; version?: string; score?: EvaluationScore };
};

export const modes = ["manual", "assisted"] as const;
export const modeLabel = { manual: "Manual", assisted: "AI-assisted", rule: "Rule-based" };

export const scenarioLabels: Record<string, string> = {
  link: "Link failure",
  bgp: "BGP neighbor down",
  endpoint: "Endpoint outage",
  Provisioning: "Provisioning",
};

const outcomeLabels: Record<string, string> = {
  ai_repair: "AI-assisted repair",
  timer_restoration: "Timer restoration",
  manual_repair: "Manual repair",
  rule_repair: "Rule-based repair",
  human_reset: "Human reset",
  observed_recovery: "Observed recovery",
  handoff: "Human handoff",
  verified: "Verified deployment",
  rolled_back: "Rolled back",
  needs_review: "Needs review",
};

export function outcomeLabel(value: string) {
  return outcomeLabels[value] || value.replace(/_/g, " ").replace(/^./, char => char.toUpperCase());
}

export function duration(seconds?: number | null, precision = 1) {
  return seconds == null || !Number.isFinite(seconds) ? "—" : `${seconds.toFixed(precision)} s`;
}

export function sampleLabel(n: number, noun = "run") {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function recordDate(at?: number) {
  return at && Number.isFinite(at) ? new Date(at * 1000) : undefined;
}

export function shortRecordId(url: string) {
  return url.slice(url.lastIndexOf("/") + 1, url.lastIndexOf("/") + 9);
}
