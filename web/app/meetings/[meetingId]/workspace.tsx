'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useCallback,useEffect,useState} from 'react';
import {SessionHeader} from '../../../components/session-shell';
import {ApiError,getSession,getWorkspace,message,mutate,type Session,type Workspace} from '../../../lib/api';
import {ConferencePanel} from './conference-panel';
import {GovernancePanel} from './governance-panels';
import {PapersPanel} from './papers-panel';
const tabs=['overview','conference','agenda','papers','participants','conflicts','motions','resolutions','minutes','actions'];
export function MeetingWorkspace({meetingId}:{meetingId:string}){const router=useRouter();const [session,setSession]=useState<Session|null>(null);const [workspace,setWorkspace]=useState<Workspace|null>(null);const [tab,setTab]=useState('overview');const [error,setError]=useState('');const [loading,setLoading]=useState(true);
 const fail=useCallback((text:string)=>setError(text),[]);const refresh=useCallback(async()=>{try{setWorkspace(await getWorkspace(meetingId));setError('')}catch(e){if(e instanceof ApiError&&e.status===401)router.replace('/login');else setError(message(e))}finally{setLoading(false)}},[meetingId,router]);
 useEffect(()=>{getSession().then(setSession).catch(e=>{if(e instanceof ApiError&&e.status===401)router.replace('/login');else fail(message(e))});refresh()},[refresh,router,fail]);
 if(loading||!session||!workspace)return <main className="center">{error?<div className="alert error" role="alert">{error}<Link href="/">Back to meetings</Link></div>:'Loading meeting workspace…'}</main>;
 const canWrite=session.permissions.includes('*')||session.permissions.includes('meetings.write');
 return <><SessionHeader session={session}/><main className="workspace"><header className="meeting-header"><div><Link href="/" className="back">← Meetings</Link><div className="eyebrow">{new Intl.DateTimeFormat(undefined,{dateStyle:'long',timeStyle:'short'}).format(new Date(workspace.meeting.starts_at))}</div><h1>{workspace.meeting.title}</h1><p>{workspace.meeting.location||'Location to be confirmed'} · <span className={`badge ${workspace.meeting.status}`}>{workspace.meeting.status}</span></p></div>{canWrite&&<div className="button-row">{workspace.meeting.status==='draft'&&<button onClick={async()=>{await mutate(`/meetings/${meetingId}/transition`,session.csrf,{status:'scheduled'});await refresh()}}>Schedule</button>}{workspace.meeting.status==='scheduled'&&<button onClick={async()=>{await mutate(`/meetings/${meetingId}/transition`,session.csrf,{status:'published'});await refresh()}}>Publish</button>}{workspace.meeting.status==='published'&&<button onClick={async()=>{await mutate(`/meetings/${meetingId}/transition`,session.csrf,{status:'completed'});await refresh()}}>Complete</button>}</div>}</header>{error&&<div className="alert error" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error">×</button></div>}<nav className="tabs" aria-label="Meeting workspace">{tabs.map(x=><button key={x} aria-current={tab===x?'page':undefined} onClick={()=>setTab(x)}>{x}</button>)}</nav>
 <ConferencePanel meetingId={meetingId} session={session} visible={tab==='conference'} onError={fail}/>
 {tab!=='conference'&&(tab==='papers'?<PapersPanel workspace={workspace} session={session} onError={fail}/>:<GovernancePanel tab={tab} workspace={workspace} session={session} refresh={refresh} onError={fail}/>)}</main></>}
