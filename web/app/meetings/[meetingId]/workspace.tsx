'use client';
import {useEffect,useState} from 'react';
import {PreJoin,LiveKitRoom,VideoConference,RoomAudioRenderer,StartAudio} from '@livekit/components-react';
import type {LocalUserChoices} from '@livekit/components-react';

type Conference={id:string,state:string,locked:number};
type Status={conference:Conference|null;participant:{admission_state:string;blocked:number}|null;capabilities:string[];provider_configured:boolean};
type Me={csrf:string};
async function json<T>(url:string, init?:RequestInit):Promise<T>{const r=await fetch(url,{...init,credentials:'include'});const value=await r.json();if(!r.ok)throw new Error(value.error?.message||'Request failed');return value}

export function MeetingWorkspace({meetingId}:{meetingId:string}){
 const [tab,setTab]=useState('call'),[status,setStatus]=useState<Status|null>(null),[csrf,setCsrf]=useState(''),[error,setError]=useState('');
 const [credentials,setCredentials]=useState<{token:string,url:string}|null>(null),[choices,setChoices]=useState<LocalUserChoices|null>(null);
 const api=`/api/v1/meetings/${meetingId}/conference`;
 const refresh=()=>json<Status>(api).then(setStatus).catch(e=>setError(e.message));
 useEffect(()=>{json<Me>('/api/v1/session/me').then(x=>setCsrf(x.csrf)).then(refresh).catch(e=>setError(e.message))},[meetingId]);
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
  {tab!=='call'&&<section className="governance"><h2>{tab[0].toUpperCase()+tab.slice(1)}</h2><p>This panel uses the existing tenant-scoped governance API. The call remains mounted while you work.</p></section>}
 </main>
}
