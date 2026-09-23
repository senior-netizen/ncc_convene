'use client';
import {LiveKitRoom,PreJoin,RoomAudioRenderer,StartAudio,VideoConference} from '@livekit/components-react';
import type {LocalUserChoices} from '@livekit/components-react';
import {useCallback,useEffect,useState} from 'react';
import {message,mutate,request,type Session} from '../../../lib/api';

type Conference={id:string;state:string;locked:number;admission_required:number};
type Person={member_id:string;admission_state:string;blocked:number;can_moderate:number;can_present:number};
type Status={conference:Conference|null;participant:Person|null;capabilities:string[];participants:Person[];provider_configured:boolean};
export function ConferencePanel({meetingId,session,visible,onError}:{meetingId:string;session:Session;visible:boolean;onError:(x:string)=>void}){
 const base=`/api/v1/meetings/${meetingId}/conference`;const [status,setStatus]=useState<Status|null>(null);const [credentials,setCredentials]=useState<{token:string;url:string}|null>(null);const [choices,setChoices]=useState<LocalUserChoices|null>(null);const [busy,setBusy]=useState(false);
 const refresh=useCallback(()=>request<Status>(base).then(setStatus).catch(e=>onError(message(e))),[base,onError]);
 useEffect(()=>{refresh();const timer=setInterval(refresh,5000);return()=>clearInterval(timer)},[refresh]);
 async function post(path:string,data:Record<string,unknown>={}){setBusy(true);try{await mutate(base+path,session.csrf,{session_id:status?.conference?.id,...data});await refresh()}catch(e){onError(message(e))}finally{setBusy(false)}}
 async function join(selected:LocalUserChoices){setChoices(selected);try{let next=status;if(!next?.participant){await post('/admission');next=await request<Status>(base);setStatus(next)}if(next?.participant?.admission_state!=='admitted')return;const token=await mutate<{token:string;url:string}>(base+'/token',session.csrf,{session_id:next.conference?.id});setCredentials(token)}catch(e){onError(message(e))}}
 const moderator=status?.capabilities.includes('conference.moderate');
 return <section className={`call-panel ${visible?'':'call-docked'}`} aria-label="Conference"><div className="conference-strip"><div><strong>Conference</strong><span>{status?.conference?status.conference.state:'Not started'}</span><small>Call presence is not formal attendance.</small></div>{status?.conference&&<span className={`badge ${status.conference.locked?'warning':'active'}`}>{status.conference.locked?'Locked':'Open'}</span>}</div>
 {!status?.provider_configured&&<div className="call-notice">LiveKit is not configured. Governance work remains available; media exchange is not being claimed.</div>}
 {!status?.conference&&moderator&&visible&&<div className="call-empty"><h2>Start the meeting conference</h2><p>Participants will request admission before receiving room credentials.</p><button className="primary" disabled={busy} onClick={()=>post('/start',{admission_required:true})}>Start conference</button></div>}
 {status?.conference&&!credentials&&status.participant?.admission_state==='waiting'&&<div className="call-empty"><h2>Waiting for admission</h2><p>A moderator must admit you before any room media is available.</p><button onClick={refresh}>Check admission</button></div>}
 {status?.conference&&!credentials&&status.participant?.admission_state!=='waiting'&&visible&&<PreJoin onSubmit={join} onError={e=>onError(e.message)} defaults={{username:session.member.display_name,videoEnabled:true,audioEnabled:true}} joinLabel="Join conference"/>}
 {credentials&&choices&&<LiveKitRoom serverUrl={credentials.url} token={credentials.token} connect audio={choices.audioEnabled} video={choices.videoEnabled} options={{adaptiveStream:true,dynacast:true}} onDisconnected={()=>{setCredentials(null);setChoices(null)}}><VideoConference/><RoomAudioRenderer/><StartAudio label="Enable conference audio"/></LiveKitRoom>}
 {visible&&status?.conference&&moderator&&<aside className="moderation"><h3>Moderator controls</h3><div className="button-row"><button disabled={busy} onClick={()=>post('/lock',{locked:!status.conference?.locked})}>{status.conference.locked?'Unlock admission':'Lock admission'}</button><button className="danger" disabled={busy} onClick={()=>confirm('End the call for everyone?')&&post('/end')}>End call</button></div><h4>Admission queue</h4>{status.participants.length===0?<p>No participants have requested entry.</p>:<ul>{status.participants.map(p=><li key={p.member_id}><span>{p.member_id===session.member.id?session.member.display_name:p.member_id.slice(0,8)} · {p.blocked?'removed':p.admission_state}</span><span>{p.admission_state==='waiting'&&<><button onClick={()=>post('/moderate',{member_id:p.member_id,action:'admit'})}>Admit</button><button onClick={()=>post('/moderate',{member_id:p.member_id,action:'reject'})}>Reject</button></>}{p.admission_state==='admitted'&&!p.blocked&&<button onClick={()=>post('/moderate',{member_id:p.member_id,action:'remove'})}>Remove</button>}</span></li>)}</ul>}</aside>}
 </section>
}
