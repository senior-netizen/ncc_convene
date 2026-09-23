'use client';
import {useEffect,useState} from 'react';
import {PreJoin,LiveKitRoom,VideoConference,RoomAudioRenderer,StartAudio} from '@livekit/components-react';
import type {LocalUserChoices} from '@livekit/components-react';

type Conference={id:string,state:string,locked:number};
type Status={conference:Conference|null;participant:{admission_state:string;blocked:number}|null;capabilities:string[];provider_configured:boolean};
type Me={csrf:string;member:{id:string};permissions:string[]};
type Agenda={id:string;title:string};
type Paper={id:string;title:string;purpose:string;status:string;version_number:number;current_revision_id:string;lock_version:number;review_deadline?:string|null;agenda_title:string};
async function json<T>(url:string, init?:RequestInit):Promise<T>{const r=await fetch(url,{...init,credentials:'include'});const value=await r.json();if(!r.ok)throw new Error(value.error?.message||'Request failed');return value}

export function MeetingWorkspace({meetingId}:{meetingId:string}){
 const [tab,setTab]=useState('call'),[status,setStatus]=useState<Status|null>(null),[csrf,setCsrf]=useState(''),[permissions,setPermissions]=useState<string[]>([]),[error,setError]=useState('');
 const [papers,setPapers]=useState<Paper[]>([]),[agenda,setAgenda]=useState<Agenda[]>([]),[paperState,setPaperState]=useState<'idle'|'loading'|'saving'|'saved'|'failed'>('idle');
 const [credentials,setCredentials]=useState<{token:string,url:string}|null>(null),[choices,setChoices]=useState<LocalUserChoices|null>(null);
 const api=`/api/v1/meetings/${meetingId}/conference`;
 const refresh=()=>json<Status>(api).then(setStatus).catch(e=>setError(e.message));
 const refreshPapers=async()=>{setPaperState('loading');try{const [p,a]=await Promise.all([json<{items:Paper[]}>(`/api/v1/papers?meeting_id=${encodeURIComponent(meetingId)}`),json<{items:Agenda[]}>(`/api/v1/meetings/${meetingId}/agenda`)]);setPapers(p.items);setAgenda(a.items);setPaperState('saved')}catch(e){setPaperState('failed');setError(e instanceof Error?e.message:'Unable to load papers')}};
 useEffect(()=>{json<Me>('/api/v1/session/me').then(x=>{setCsrf(x.csrf);setPermissions(x.permissions)}).then(()=>Promise.all([refresh(),refreshPapers()])).catch(e=>setError(e.message))},[meetingId]);
 const post=async(path:string,data:object={})=>{await json(api+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});await refresh()};
 const join=async(selected:LocalUserChoices)=>{setChoices(selected);if(!status?.participant)await post('/admission');const next=await json<Status>(api);if(next.participant?.admission_state!=='admitted'){setStatus(next);return}const token=await json<{token:string,url:string}>(api+'/token',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:'{}'});setCredentials(token)};
 return <main className="workspace"><header><h1>Meeting workspace</h1><nav>{['call','agenda','papers','participants','motions','minutes','actions'].map(x=><button aria-pressed={tab===x} onClick={()=>setTab(x)} key={x}>{x}</button>)}</nav></header>
  {error&&<p role="alert">{error}</p>}
  <section className={tab==='call'?'call':'call call-hidden'} aria-label="Conference">
   {!status?.conference&&status?.capabilities.includes('conference.moderate')&&<button onClick={()=>post('/start',{admission_required:true})}>Start conference</button>}
   {status?.conference&&!credentials&&status.participant?.admission_state==='waiting'&&<div className="waiting"><h2>Waiting for admission</h2><p>You cannot send or receive room media until a moderator admits you.</p><button onClick={refresh}>Check again</button></div>}
   {status?.conference&&!credentials&&status.participant?.admission_state!=='waiting'&&<PreJoin onSubmit={join} onError={e=>setError(e.message)} defaults={{username:'NCC participant',videoEnabled:true,audioEnabled:true}} joinLabel="Join call" />}
   {credentials&&choices&&<LiveKitRoom serverUrl={credentials.url} token={credentials.token} connect={true} audio={choices.audioEnabled} video={choices.videoEnabled} options={{adaptiveStream:true,dynacast:true}} onDisconnected={()=>{setCredentials(null);setChoices(null)}}><VideoConference/><RoomAudioRenderer/><StartAudio label="Enable meeting audio"/></LiveKitRoom>}
  </section>
  {tab==='papers'&&<section className="governance" aria-labelledby="papers-title"><div className="section-heading"><div><h2 id="papers-title">Board papers</h2><p>Revision-bound submission and review for this meeting.</p></div><span role="status">{paperState==='loading'?'Loading…':paperState==='saving'?'Saving…':paperState==='failed'?'Failed':'Saved'}</span></div>
   {paperState==='failed'&&<button onClick={refreshPapers}>Retry</button>}
   {papers.length===0&&paperState!=='loading'?<p>No board papers have been submitted for this meeting.</p>:<ul className="paper-list">{papers.map(p=><li key={p.id}><div><strong>{p.title}</strong><p>{p.agenda_title} · Revision {p.version_number}</p></div><span className={`status status-${p.status}`}>{p.status.replaceAll('_',' ')}</span>{permissions.includes('papers.submit')&&['draft','changes_requested'].includes(p.status)&&<button onClick={async()=>{setPaperState('saving');try{await json(`/api/v1/papers/${p.id}/submit`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({revision_id:p.current_revision_id,lock_version:p.lock_version})});await refreshPapers()}catch(e){setPaperState('failed');setError(e instanceof Error?e.message:'Submission failed')}}}>Submit revision {p.version_number}</button>}</li>)}</ul>}
   {permissions.includes('papers.submit')&&<form className="paper-form" onSubmit={async event=>{event.preventDefault();setPaperState('saving');const data=new FormData(event.currentTarget);try{await json('/api/v1/papers',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(Object.fromEntries(data))});event.currentTarget.reset();await refreshPapers()}catch(e){setPaperState('failed');setError(e instanceof Error?e.message:'Draft was not saved')}}}><h3>New paper draft</h3><input type="hidden" name="meeting_id" value={meetingId}/><label>Agenda item<select name="agenda_item_id" required><option value="">Select an item</option>{agenda.map(a=><option key={a.id} value={a.id}>{a.title}</option>)}</select></label>{['title','purpose','background','recommendation','implications'].map(field=><label key={field}>{field[0].toUpperCase()+field.slice(1)}{field==='title'?<input name={field} required/>:<textarea name={field} required/>}</label>)}<label>PDF content for this demonstrator<textarea name="content" required aria-describedby="paper-content-help"/></label><small id="paper-content-help">Production clients should upload a PDF using the protected document endpoint.</small><button disabled={paperState==='saving'} type="submit">Save draft</button></form>}
  </section>}
  {tab!=='call'&&tab!=='papers'&&<section className="governance"><h2>{tab[0].toUpperCase()+tab.slice(1)}</h2><p>This panel uses the existing tenant-scoped governance API. The call remains mounted while you work.</p></section>}
 </main>
}
