const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const evidencePath = "docs/proof/p3/final-recovery.json";

function readDemoRecording() {
  const { incident } = JSON.parse(readFileSync(join(__dirname, "..", evidencePath), "utf8"));
  const { samples, events, review, metrics, t0 } = incident;
  const baseline = samples.findLast(sample => sample.observed < t0);
  const loss = samples.find(sample => sample.observed >= t0 && sample.flows.some(
    flow => Object.values(flow.probes).includes("failed"),
  ));
  const diagnosis = events.find(event => event.phase === "diagnosed");
  const failover = events.find(event => event.phase === "proposed");
  const resolved = events.find(event => event.phase === "resolved");
  if (!baseline || !loss || !diagnosis || !failover || !resolved || !review?.decision ||
      ![t0, metrics.verified_failover_s, metrics.closure_s].every(Number.isFinite)) {
    throw Error("The Ubuntu incident recording is missing playback evidence.");
  }

  const stages = [
    { label: "Healthy baseline", time: baseline.observed, reason: "Both spine paths are installed. ICMP and HTTP pass in both directions." },
    { label: "Link failure", time: loss.observed, reason: "Leaf 1 loses its link to Spine 1. The recorded host probes show the interruption." },
    { label: "Qwen diagnosis", time: diagnosis.time, reason: diagnosis.reason },
    { label: "Failover verified", time: failover.time, reason: failover.reason },
    { label: "Redundancy restored", time: resolved.time, reason: resolved.reason },
  ];
  const facts = review.observation.facts;
  if (review.decision.citations.some(key => !Object.hasOwn(facts, key))) {
    throw Error("The Ubuntu recording is missing a cited model observation.");
  }
  const citedReview = {
    model: review.model,
    decision: review.decision,
    observation: {
      observed: review.observation.observed,
      facts: Object.fromEntries(review.decision.citations.map(key => [key, facts[key]])),
    },
  };

  const frames = stages.map(({ label, time, reason }, index) => {
    const sample = samples.findLast(observation => observation.observed <= time);
    if (!sample || (index > 0 && stages[index - 1].time >= time)) {
      throw Error(`Invalid timeline for the Ubuntu replay stage ${label}.`);
    }
    return {
      label,
      reason,
      eventTime: time,
      review: time >= diagnosis.time ? citedReview : undefined,
      snapshot: {
        timestamp: new Date(sample.observed * 1000).toISOString(),
        routers: sample.routers,
        hosts: sample.hosts.map(({ name, leaf, error }) => ({ name, leaf, error })),
      },
      probes: sample.flows.map(({ source, timestamp, candidates, probes }) => ({
        source, timestamp, candidates, probes,
      })),
    };
  });

  return {
    startedAt: t0,
    verifiedFailover: metrics.verified_failover_s,
    closure: metrics.closure_s,
    evidenceUrl: "https://github.com/giaphoang/netguard/blob/netguard-ubuntu-arm64/" + evidencePath,
    frames,
  };
}

module.exports = { readDemoRecording };
