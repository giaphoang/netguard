import React from "react";
import { Background, ReactFlow, type Edge } from "@xyflow/react";
import { edgeTypes, type TopologyEdge } from "./TrafficEdge";

export type FabricSpec = {spines:number[];leaves:number[];hosts:{id:number;leaf:number}[]};
export type Overlay = Pick<Edge, "source" | "target"> & { label: string };
export function TopologyPreview({before,after,applied=false,overlay,beforeLabel,afterLabel,excludedLinks=[]}:{
  before:FabricSpec;after:FabricSpec;applied?:boolean;overlay?:Overlay;
  beforeLabel?:string;afterLabel?:string;excludedLinks?:string[];
}) {
  function graph(spec:FabricSpec, proposed: boolean, tunnel?:Overlay) {
    const spacing = overlay ? 200 : 150;
    const nodes = [
      ...spec.spines.map((n,i)=>({id:`spine-${n}`,position:{x:i*spacing,y:0},data:{label:`Spine ${n}`}})),
      ...spec.leaves.map((n,i)=>({id:`leaf-${n}`,position:{x:i*spacing,y:110},data:{label:`Leaf ${n}`}})),
      ...spec.hosts.map((h,i)=>({id:`host-${h.id}`,position:{x:i*spacing,y:220},data:{label:`Host ${h.id}`}}))
    ];
    const original = new Set([...before.spines.map(n=>`spine-${n}`),...before.leaves.map(n=>`leaf-${n}`),...before.hosts.map(h=>`host-${h.id}`)]);
    const edges: TopologyEdge[] = [
      ...spec.spines.flatMap(s => spec.leaves.map(l => ({
        id: `${s}-${l}`, source: `spine-${s}`, target: `leaf-${l}`, type: "traffic",
        data: { protocol: "BGP", curved: true }, ariaLabel: `Spine ${s} to Leaf ${l}, BGP`,
      }))),
      ...spec.hosts.map(h => ({
        id: `h${h.id}`, source: `leaf-${h.leaf}`, target: `host-${h.id}`, type: "traffic",
        data: { protocol: "Ethernet", curved: true }, ariaLabel: `Leaf ${h.leaf} to Host ${h.id}, Ethernet`,
      })),
    ];
    if (tunnel) {
      edges.push({
        id: "tunnel", source: tunnel.source, target: tunnel.target, type: "traffic",
        data: { protocol: tunnel.label }, ariaLabel: `${tunnel.source} to ${tunnel.target}, ${tunnel.label}`,
        style: { stroke: "var(--accent)", strokeDasharray: "6 4", strokeWidth: 2 },
      });
    }
    return {
      nodes:nodes.map(n=>({...n,className:original.has(n.id) ? undefined : "pending"})),
      edges: proposed ? edges.filter(edge => !excludedLinks.includes(edge.target + "/" + edge.source)) : edges,
    };
  }
  return (
    <div className="topology-compare">
      {([[beforeLabel ?? (applied ? "Before" : "Current"), before, undefined],
         [afterLabel ?? (applied ? "Deployed" : "Proposed"), after, overlay]] as const).map(([label, spec, tunnel], i) => (
        <div key={i}>
          <h3>{label}</h3>
          <div style={{ height: 250 }}>
            <ReactFlow
              {...graph(spec, i === 1, tunnel)}
              colorMode="dark"
              edgeTypes={edgeTypes}
              fitView
              nodesDraggable={false}
              nodesConnectable={false}
              zoomOnScroll={false}
              preventScrolling={false}
            >
              <Background />
            </ReactFlow>
          </div>
          {tunnel && <small className="topology-overlay-key">
            {tunnel.label} · {tunnel.source.replace("leaf-", "Leaf ")} ↔ {tunnel.target.replace("leaf-", "Leaf ")}
          </small>}
        </div>
      ))}
    </div>
  );
}
