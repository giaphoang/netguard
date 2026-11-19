import React, { useEffect, useState } from "react";
import { TopologyPreview } from "./TopologyPreview";
import { parseView, recordPath, type Block, type RecordAction, type RecordRef, type RecordView, type ToolCall } from "./agentProtocol";
import { get, post } from "./localApi";
import "./agentResponse.css";

function Value({ label, value }: { label: string; value: unknown }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details className="agent-data" onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>{label}</summary>
      {expanded && <pre tabIndex={0}>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}

export function ToolActivity({ trace, title = "Tool activity" }: { trace: ToolCall[]; title?: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!trace.length) return null;
  const last = trace[trace.length - 1];
  const stages = Array.from(new Set(trace.map(call => call.stage).filter(Boolean)));
  return (
    <details className="agent-tool-log" onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary><strong>{title}</strong> <span>{trace.length}</span></summary>
      {!!stages.length && <p className="agent-stages">{stages.join(" → ")}</p>}
      {!expanded && last.status === "running" && <p role="status">{last.label} in progress</p>}
      {expanded && (
        <ol className="agent-tools">
          {trace.map((call, i) => {
            const elapsed = call.started !== undefined && call.completed !== undefined
              ? (call.completed - call.started).toFixed(2) + " s" : undefined;
            return (
              <li key={i}>
                <strong>{call.label}{call.target && " · " + call.target}</strong>
                <small>{[call.stage, call.status, elapsed].filter(Boolean).join(" · ")}</small>
                {!!call.arguments?.length && <pre>{call.arguments.join(" ")}</pre>}
                {call.error && <p className="chat-error">{call.error}</p>}
                {call.result != null && <Value label="Tool result" value={call.result} />}
              </li>
            );
          })}
        </ol>
      )}
    </details>
  );
}

function TopologyBlock({ block }: { block: Extract<Block, { type: "topology" }> }) {
  const [expanded, setExpanded] = useState(false);
  return <details className="agent-data" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{block.title}</summary>
    {expanded && <TopologyPreview before={block.before} after={block.after} overlay={block.overlay}
      beforeLabel={block.beforeLabel} afterLabel={block.afterLabel} excludedLinks={block.excludedLinks} />}
  </details>;
}

function ResponseBlock({ block }: { block: Block }) {
  switch (block.type) {
    case "text":
      return <p className="chat-text">{block.text}</p>;
    case "list":
      return <section>
        {block.title && <h4>{block.title}</h4>}
        <ul>{block.items.map((item, i) => <li key={i}>{item}</li>)}</ul>
      </section>;
    case "table":
      return <div className="agent-table-scroll" tabIndex={0} role="region" aria-label={block.title || "Results"}>
        <table className="agent-table">
          {block.title && <caption>{block.title}</caption>}
          <thead><tr>{block.columns.map((column, i) => <th key={i} scope="col">{column}</th>)}</tr></thead>
          <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>
            {row.map((value, columnIndex) => columnIndex === 0
              ? <th scope="row" key={columnIndex}>{value == null ? "—" : String(value)}</th>
              : <td key={columnIndex}>{value == null ? "—" : String(value)}</td>)}
          </tr>)}</tbody>
        </table>
      </div>;
    case "section":
      return block.collapsed
        ? <details className="agent-data"><summary>{block.title}</summary><ResponseBlocks blocks={block.blocks} /></details>
        : <section className="agent-section"><h4>{block.title}</h4><ResponseBlocks blocks={block.blocks} /></section>;
    case "topology":
      return <TopologyBlock block={block} />;
    case "code":
      return <div className="agent-code">
        {block.title && <strong>{block.title}</strong>}
        <pre tabIndex={0}>{block.text}</pre>
      </div>;
    case "data":
      return <Value label={block.title} value={block.value} />;
    case "link":
      return <a href={block.href} target="_blank" rel="noreferrer">{block.label} ↗</a>;
    case "timeline":
      return <ToolActivity title={block.title} trace={block.trace} />;
  }
}

export function ResponseBlocks({ blocks }: { blocks: Block[] }) {
  if (!blocks.length) return null;
  return <div className="agent-blocks">
    {blocks.map((block, i) => <ResponseBlock key={block.type + i} block={block} />)}
  </div>;
}

export function AgentRecord({ record, live }: { record: RecordRef; live: boolean }) {
  const [view, setView] = useState<RecordView>();
  const [problem, setProblem] = useState("");
  const [actionError, setActionError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now);
  const path = recordPath(record);

  useEffect(() => {
    if (!live) return;
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      try {
        const next = parseView(await get<unknown>(path, controller.signal));
        if (controller.signal.aborted) return;
        setView(next);
        setProblem("");
        const seconds = next.refreshSeconds || (submitting ? 1 : 0);
        if (seconds) timer = window.setTimeout(poll, seconds * 1000);
      } catch (error) {
        if (controller.signal.aborted) return;
        setProblem(error instanceof Error ? error.message : "The record could not be loaded.");
      }
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [path, live, refresh, submitting]);

  useEffect(() => {
    setNow(Date.now());
    const deadlines = view?.actions.flatMap(action => action.expires ? [action.expires * 1000] : []) || [];
    if (deadlines.length) {
      const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, Math.min(...deadlines) - Date.now()));
      return () => window.clearTimeout(timer);
    }
  }, [view]);

  async function act(action: RecordAction) {
    if (submitting || !live) return;
    setSubmitting(true);
    setActionError("");
    try {
      await post(path + "/actions/" + encodeURIComponent(action.key), { revision: action.revision });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The controller rejected the action.");
    } finally {
      setSubmitting(false);
      setRefresh(n => n + 1);
    }
  }

  return <section className="agent-record">
    {view ? <>
      <div className="agent-record-heading">
        {view.title && <h3>{view.title}</h3>}
        {view.href && <a href={view.href} target="_blank" rel="noreferrer">Run link ↗</a>}
      </div>
      {view.text && <p className="chat-text" role={view.refreshSeconds ? "status" : undefined}>{view.text}</p>}
      <ToolActivity trace={view.trace} />
      <ResponseBlocks blocks={view.blocks} />
      {!!view.actions.length && (
        <div className="chat-actions">
          {view.actions.map(action => {
            const expired = action.expires !== undefined && action.expires * 1000 <= now;
            return <button key={action.key} type="button" className={action.primary ? "primary" : undefined}
              disabled={!live || submitting || !!problem || expired} onClick={() => void act(action)}>
              {expired ? "Preview expired" : action.label}
            </button>;
          })}
        </div>
      )}
      {submitting && <small role="status">Submitting to the controller…</small>}
    </> : !problem && <p role="status">{live ? "Opening activity and evidence…" : "Connect to the live lab to open this record."}</p>}
    {problem && <div className="chat-actions">
      <p className="chat-error" role="alert">{problem}</p>
      <button type="button" disabled={!live} onClick={() => setRefresh(n => n + 1)}>Reload record</button>
    </div>}
    {actionError && <p className="chat-error" role="alert">{actionError}</p>}
  </section>;
}
