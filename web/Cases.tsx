import React, {useEffect,useState} from 'react';
import type { WorkspaceName } from "./WorkspaceShell";
type Case = {id:string;kind:string;text:string;status?:string;verified_healthy_at?:number;observed:number;incident_id?:string;affected?:string[];decision?:{next_check:string;diagnosis:string};outcome?:string;events?:{time:number;stage:string;tool:string;result:unknown}[];facts:{history?:{facts:Record<string,unknown>}[]}};
export function Cases({compact=false,live,navigate}:{compact?:boolean;live:boolean;navigate:(page:WorkspaceName)=>void}) {
 const [rows,setRows]=useState<Case[]>([]),[query,setQuery]=useState(''),[selected,setSelected]=useState(''),[error,setError]=useState('');
 useEffect(()=>{if(!live)return;let alive=true;async function poll(){try{const r=await fetch('/api/monitoring');if(r.ok&&alive){setRows((await r.json()).investigations.filter((r:Case)=>['monitor','configuration drift'].includes(r.kind)).map((r:Case)=>({...r,status:r.status||(r.verified_healthy_at?'verified':'recorded')})));setError('');}}catch{if(alive)setError('Monitoring connection unavailable');}}poll();const timer=setInterval(poll,2500);return()=>{alive=false;clearInterval(timer);};},[live]);
 const matching=rows.filter(r=>`${r.id} ${r.text} ${r.status} ${r.affected}`.toLowerCase().includes(query.toLowerCase()));
 const row=matching.find(r=>r.id===selected)||matching[0];
 async function action(kind:string){if(!row?.incident_id)return;try{const token=(await(await fetch('/api/session',{cache:'no-store'})).json()).token;const r=await fetch(`/api/incidents/${row.incident_id}/${kind}`,{method:'POST',headers:{'X-Netguard-Session':token}});if(!r.ok)throw Error((await r.json()).detail);}catch(e){setError(String(e));}}
 if (compact) {
  const active = rows.find(r => r.status && !['verified', 'recorded'].includes(r.status));
  const summary = live
   ? 'Watching BGP transitions, link state, probes and configuration drift.'
   : 'Available in the live lab.';
  return (
   <div className="monitor-notice" aria-live="polite">
    <strong>{active ? 'Investigation in progress' : 'Proactive monitoring'}</strong>
    <p>{error || active?.text || summary}</p>
    {active && <small>{active.events?.[active.events.length - 1]?.stage} · {new Date(active.observed * 1000).toLocaleTimeString()}</small>}
    <button type="button" onClick={() => navigate('Incidents')}>Review investigations</button>
   </div>
  );
 }
 return <section className="case-workspace"><div className="case-list"><label>Search incidents<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Device, finding or status"/></label>{matching.map(r=><button key={r.id} aria-pressed={r.id===row?.id} onClick={()=>setSelected(r.id)}><b>{r.status==='verified'?'Resolved':r.status==='recorded'?'Recorded':'Needs attention'} · {r.kind}</b><span>{r.text.slice(0,130)}</span><small>{r.status||'Review'} · {new Date(r.observed*1000).toLocaleString()}</small></button>)}</div><div className="case-detail">{error&&<p role="alert">{error}</p>}{row?<><span className="tag">{row.status==='verified'?'VERIFIED':row.status==='recorded'?'RECORDED':'WARNING'}</span><h2>{row.text}</h2>{row.affected&&<p>Affected: {row.affected.join(', ')}</p>}<ul>{Object.entries(row.facts.history?.[row.facts.history.length-1]?.facts||{}).filter(([k])=>!row.affected?.length||row.affected.includes(k)).slice(0,3).map(([k,v])=><li key={k}><b>{k}</b> · {Object.entries(v as Record<string,unknown>).filter(([field])=>["interface","leaf_peer","spine_peer","probes"].includes(field)).map(([field,value])=>field.replace(/_/g," ")+": "+(typeof value === "object"?JSON.stringify(value):String(value))).join(" · ")}</li>)}</ul><p><b>Action:</b> {row.decision?.next_check||'Review the observed evidence and controller disposition.'}</p><p><b>Verification:</b> {row.outcome||'Pending verified closure'}</p>{row.status==='proposed'&&row.incident_id&&<><button onClick={()=>action('approve')}>Approve repair</button><button onClick={()=>action('reject')}>Escalate to human</button></>}<div className="case-events">{row.events?.map((e,i)=><details key={i}><summary><time>{new Date(e.time*1000).toLocaleTimeString()}</time> {e.stage} · {e.tool}</summary><pre>{JSON.stringify(e.result,null,2)}</pre></details>)}</div><details><summary>Full timestamped evidence and logs</summary><p><a href={'/api/investigations/'+row.id} target="_blank" rel="noreferrer">Investigation record</a></p>{row.incident_id&&<a href={'/api/incidents/'+row.incident_id} target="_blank" rel="noreferrer">Controller execution and probe history</a>}</details></>:<p>No matching investigations. Live anomalies appear automatically.</p>}</div></section>;
}
