import type { FabricSpec, Overlay } from "./TopologyPreview";

type ObjectValue = Record<string, unknown>;
type Cell = string | number | boolean | null;

export type Block =
  | { type: "text"; text: string }
  | { type: "list"; title?: string; items: string[] }
  | { type: "table"; title?: string; columns: string[]; rows: Cell[][] }
  | { type: "section"; title: string; blocks: Block[]; collapsed?: boolean }
  | { type: "topology"; title: string; before: FabricSpec; after: FabricSpec; overlay?: Overlay;
      beforeLabel?: string; afterLabel?: string; excludedLinks?: string[] }
  | { type: "code"; title?: string; text: string }
  | { type: "data"; title: string; value: unknown }
  | { type: "link"; label: string; href: string }
  | { type: "timeline"; title: string; trace: ToolCall[] };

export type ToolCall = {
  label: string;
  stage?: string;
  target?: string;
  status?: string;
  started?: number;
  completed?: number;
  arguments?: string[];
  result?: unknown;
  error?: string;
};

export type RecordRef = { source: string; id: string };
export type RecordAction = { key: string; label: string; primary?: boolean; revision?: string; expires?: number };
export type RecordView = {
  title?: string;
  text?: string;
  blocks: Block[];
  trace: ToolCall[];
  actions: RecordAction[];
  refreshSeconds: number;
  href?: string;
};

export function object(value: unknown): ObjectValue | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as ObjectValue : undefined;
}

export function string(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function idPart(value: unknown): string | undefined {
  return typeof value === "string" && /^[a-z0-9][a-z0-9_-]{0,63}$/i.test(value) ? value : undefined;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

function safeHref(value: unknown): string | undefined {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//")
    && !/[\\\u0000-\u0020]/.test(value) ? value : undefined;
}

function cell(value: unknown): Cell {
  if (value == null) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  return JSON.stringify(value);
}

function fabric(value: unknown): FabricSpec | undefined {
  const row = object(value);
  if (!row || !Array.isArray(row.spines) || !Array.isArray(row.leaves) || !Array.isArray(row.hosts)) return;
  const identifiers = (values: unknown[]) => values.filter((id): id is number => Number.isSafeInteger(id) && Number(id) > 0);
  const hosts = row.hosts.flatMap(host => {
    const item = object(host);
    return item && typeof item.id === "number" && typeof item.leaf === "number" &&
      Number.isSafeInteger(item.id) && Number.isSafeInteger(item.leaf)
      ? [{ id: item.id, leaf: item.leaf }] : [];
  });
  return { spines: identifiers(row.spines), leaves: identifiers(row.leaves), hosts };
}

function overlay(value: unknown): Overlay | undefined {
  const row = object(value);
  const source = idPart(row?.source), target = idPart(row?.target), label = string(row?.label);
  if (source && target && label) return { source, target, label };
}

export function parseTrace(value: unknown): ToolCall[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(entry => {
    const row = object(entry);
    if (!row) return [];
    const label = string(row.label);
    if (!label) return [];
    return [{
      label, stage: string(row.stage), target: string(row.target), status: string(row.status),
      started: finite(row.started), completed: finite(row.completed),
      arguments: strings(row.arguments), result: row.result, error: string(row.error),
    }];
  });
}

function parseBlock(value: unknown, depth: number): Block | undefined {
  if (typeof value === "string") return { type: "text", text: value };
  const row = object(value);
  if (!row) return;
  const title = string(row.title);
  const text = string(row.text);
  switch (row.type) {
    case "text":
      if (text !== undefined) return { type: "text", text };
      break;
    case "list": {
      const items = strings(row.items);
      if (items.length) return { type: "list", title, items };
      break;
    }
    case "table": {
      const columns = strings(row.columns);
      if (columns.length && Array.isArray(row.rows)) {
        const rows = row.rows.filter(Array.isArray).map(values => columns.map((_, i) => cell(values[i])));
        return { type: "table", title, columns, rows };
      }
      break;
    }
    case "section":
      if (depth < 6 && Array.isArray(row.blocks)) {
        return { type: "section", title: title || "Details", collapsed: row.collapsed === true, blocks: parseBlocks(row.blocks, depth + 1) };
      }
      break;
    case "topology": {
      const before = fabric(row.before), after = fabric(row.after);
      if (before && after) {
        return { type: "topology", title: title || "Topology comparison", before, after,
          overlay: overlay(row.overlay), beforeLabel: string(row.before_label), afterLabel: string(row.after_label),
          excludedLinks: strings(row.excluded_links) };
      }
      break;
    }
    case "code":
      if (text !== undefined) return { type: "code", title, text };
      break;
    case "data":
      return { type: "data", title: title || "Evidence", value: row.value };
    case "link": {
      const label = string(row.label), href = safeHref(row.href);
      if (label && href) return { type: "link", label, href };
      if (label) return { type: "text", text: label };
      break;
    }
    case "timeline": {
      const trace = parseTrace(row.trace);
      if (trace.length) return { type: "timeline", title: title || "Tool activity", trace };
      break;
    }
  }
  return text !== undefined ? { type: "text", text } : { type: "data", title: title || "Additional details", value: row };
}

export function parseBlocks(value: unknown, depth = 0): Block[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(entry => {
    const block = parseBlock(entry, depth);
    return block ? [block] : [];
  });
}

export function parseRef(value: unknown): RecordRef | undefined {
  const row = object(value);
  const source = idPart(row?.source), id = idPart(row?.id);
  if (source && id) return { source, id };
}

export function recordPath({ source, id }: RecordRef) {
  return "operator/records/" + encodeURIComponent(source) + "/" + encodeURIComponent(id);
}

export function parseView(value: unknown): RecordView {
  const row = object(value);
  if (!row) throw Error("The controller returned an unreadable record.");
  const actions = Array.isArray(row.actions) ? row.actions.flatMap(entry => {
    const action = object(entry);
    const key = idPart(action?.key), label = string(action?.label);
    if (!key || !label || !action) return [];
    return [{ key, label, primary: action.primary === true, revision: string(action.revision),
      expires: finite(action.expires) }];
  }) : [];
  const refresh = finite(row.refresh_seconds) ?? 0;
  return {
    title: string(row.title), text: string(row.text), blocks: parseBlocks(row.blocks),
    trace: parseTrace(row.trace), actions, refreshSeconds: refresh > 0 ? Math.min(60, Math.max(0.5, refresh)) : 0,
    href: safeHref(row.href),
  };
}
