import React from "react";
import { Background, ReactFlow } from "@xyflow/react";
export type FabricSpec = {spines:number[];leaves:number[];hosts:{id:number;leaf:number}[]};
export function TopologyPreview({before,after,applied=false}:{before:FabricSpec;after:FabricSpec;applied?:boolean}) {
  function graph(spec:FabricSpec) {
    const nodes = [
      ...spec.spines.map((n,i)=>({id:`spine-${n}`,position:{x:i*150,y:0},data:{label:`Spine ${n}`}})),
      ...spec.leaves.map((n,i)=>({id:`leaf-${n}`,position:{x:i*150,y:110},data:{label:`Leaf ${n}`}})),
      ...spec.hosts.map((h,i)=>({id:`host-${h.id}`,position:{x:i*150,y:220},data:{label:`Host ${h.id}`}}))
    ];
    const original = new Set([...before.spines.map(n=>`spine-${n}`),...before.leaves.map(n=>`leaf-${n}`),...before.hosts.map(h=>`host-${h.id}`)]);
    return {nodes:nodes.map(n=>({...n,style:{border:original.has(n.id)?'1px solid #93a9d8':'2px dashed #2860ff',background:original.has(n.id)?'#fff':'#e9f0ff'}})),edges:[
      ...spec.spines.flatMap(s=>spec.leaves.map(l=>({id:`${s}-${l}`,source:`spine-${s}`,target:`leaf-${l}`}))),
      ...spec.hosts.map(h=>({id:`h${h.id}`,source:`leaf-${h.leaf}`,target:`host-${h.id}`}))
    ]};
  }
  return <div className="topology-compare">{[[applied?"Before":"Current",before],[applied?"Deployed":"Proposed",after]].map(([label,spec])=><div key={label as string}><h3>{label as string}</h3><div style={{height:250}}><ReactFlow {...graph(spec as FabricSpec)} fitView nodesDraggable={false} nodesConnectable={false} proOptions={{hideAttribution:false}}><Background/></ReactFlow></div></div>)}</div>;
}
