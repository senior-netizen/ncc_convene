'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect,useState} from 'react';
import {SessionHeader} from '../components/session-shell';
import {ApiError,getMeetings,getSession,message,type Meeting,type Session} from '../lib/api';

export default function Home(){const router=useRouter();const [session,setSession]=useState<Session|null>(null);const [meetings,setMeetings]=useState<Meeting[]>([]);const [error,setError]=useState('');
 useEffect(()=>{Promise.all([getSession(),getMeetings()]).then(([s,m])=>{setSession(s);setMeetings(m.items)}).catch(e=>{if(e instanceof ApiError&&e.status===401)router.replace('/login');else setError(message(e))})},[router]);
 if(!session&&!error)return <main className="center" aria-busy="true">Loading your meetings…</main>;
 return <><>{session&&<SessionHeader session={session}/>}</><main className="dashboard"><div className="page-heading"><div><div className="eyebrow">Meeting administration</div><h1>Meetings</h1><p>Open an assigned meeting to prepare papers, record decisions and join its conference.</p></div></div>{error&&<div role="alert" className="alert error">{error}<button onClick={()=>location.reload()}>Retry</button></div>}{!error&&meetings.length===0&&<section className="empty"><h2>No meetings available</h2><p>Meetings assigned to your organisation will appear here.</p></section>}<div className="meeting-grid">{meetings.map(meeting=><Link className="meeting-card" key={meeting.id} href={`/meetings/${meeting.id}`}><div><span className={`badge ${meeting.status}`}>{meeting.status}</span><h2>{meeting.title}</h2></div><dl><div><dt>Date</dt><dd>{new Intl.DateTimeFormat(undefined,{dateStyle:'full',timeStyle:'short'}).format(new Date(meeting.starts_at))}</dd></div><div><dt>Location</dt><dd>{meeting.location||'To be confirmed'}</dd></div></dl><span className="open">Open workspace <span aria-hidden>→</span></span></Link>)}</div></main></>}
