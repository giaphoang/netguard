import React from "react";
import { Activity, BarChart3, Bot, Boxes, LayoutDashboard, Siren } from "lucide-react";
import routes from "./workspaceRoutes.json";

export type WorkspaceName = keyof typeof routes;

const workspaces = {
  "Command Center": {
    label: "Overview", icon: LayoutDashboard,
    description: "Watch routing, reachability, and incidents across the fabric.",
  },
  "AI Agent": {
    label: "AI Agent", icon: Bot,
    description: "Investigate the network, review the evidence, and approve proposed changes.",
  },
  Incidents: {
    label: "Incidents", icon: Siren,
    description: "Search the incident journal, inspect evidence, and review fault controls.",
  },
  Provisioning: {
    label: "Provisioning", icon: Boxes,
    description: "Preview, approve, and verify fabric changes.",
  },
  "AI Impact": {
    label: "AI Impact", icon: BarChart3,
    description: "Explore recorded workflow timings, recovery outcomes, and AI evaluations.",
  },
} satisfies Record<WorkspaceName, { label: string; icon: typeof Bot; description: string }>;

const names = Object.keys(workspaces) as WorkspaceName[];

export function workspaceAtLocation(): WorkspaceName {
  const path = location.pathname.replace(/\/*$/, "/");
  return names.find((name) => routes[name] === path) ?? "Command Center";
}

export function workspaceHref(page: WorkspaceName) {
  return routes[page] + location.search;
}

type Status = "Live isolated lab" | "Replay" | "Connecting" | "Telemetry offline";

export function WorkspaceShell({ page, navigate, status, environment, children }: {
  page: WorkspaceName;
  navigate: (page: WorkspaceName) => void;
  status: Status;
  environment: string;
  children: React.ReactNode;
}) {
  function follow(event: React.MouseEvent<HTMLAnchorElement>, workspace: WorkspaceName) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(workspace);
  }

  return (
    <div className="shell">
      <aside className="rail">
        <a className="brand" href={workspaceHref("Command Center")} onClick={(e) => follow(e, "Command Center")}>
          <span className="brand-mark"><Activity size={20} aria-hidden="true" /></span>
          <span><strong>NetGuard</strong><small>AI network operations</small></span>
        </a>
        <nav className="workspaces" aria-label="Workspace navigation">
          {names.map((name) => {
            const { icon: Icon, label } = workspaces[name];
            return (
              <a key={name} href={workspaceHref(name)} onClick={(e) => follow(e, name)}
                aria-current={page === name ? "page" : undefined}>
                <Icon size={17} aria-hidden="true" />
                {label}
              </a>
            );
          })}
        </nav>
        <div className="lab-panel">
          <div className="lab-status">
            <strong data-live={status === "Live isolated lab"}>{status}</strong>
            <small>{environment}</small>
          </div>
          <nav className="replays" aria-label="Lab mode">
            <a href="/">Live lab</a>
            <a href="/?replay">Link replay</a>
            <a href="/?replay=handoff">Handoff replay</a>
          </nav>
        </div>
      </aside>
      <main data-page={page}>{children}</main>
    </div>
  );
}

export function WorkspaceHeader({ page, navigate }: {
  page: WorkspaceName;
  navigate: (page: WorkspaceName) => void;
}) {
  return (
    <header className="page-title">
      <div>
        <h1>{page}</h1>
        <p>{workspaces[page].description}</p>
      </div>
      <div className="page-actions">
        {page !== "Incidents" && <button type="button" onClick={() => navigate("Incidents")}>Review incidents</button>}
        {page === "Command Center" && (
          <button type="button" className="primary" onClick={() => navigate("AI Agent")}>
            <Bot size={17} aria-hidden="true" /> Open AI Agent
          </button>
        )}
      </div>
    </header>
  );
}
