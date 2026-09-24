'use client';
import Link from 'next/link';
import {usePathname,useRouter} from 'next/navigation';
import {ReactNode,useState} from 'react';
import type {Session} from '../lib/api';
import {request} from '../lib/api';
import {Icon} from './ui';

const nav=[['Dashboard','/','dashboard'],['Meetings','/#meetings','calendar'],['Board Papers','/#documents','document'],['Decisions','/#decisions','decision'],['Action Items','/#actions','check']];
export function AppShell({session,children,crumb}:{session:Session;children:ReactNode;crumb?:string}){
 const router=useRouter(),path=usePathname();const [open,setOpen]=useState(false);
 async function logout(){await request('/api/v1/session/logout',{method:'POST'});router.replace('/login');router.refresh()}
 return <div className="app-shell"><button className="mobile-menu" onClick={()=>setOpen(true)} aria-label="Open navigation"><Icon name="menu"/></button>{open&&<button className="drawer-scrim" aria-label="Close navigation" onClick={()=>setOpen(false)}/>}<aside className={`sidebar ${open?'open':''}`}><div className="brand-lockup"><span className="brand-mark">N</span><div><Link className="brand" href="/">NCC Convene</Link><small>Board &amp; Committee Portal</small></div><button className="drawer-close" onClick={()=>setOpen(false)} aria-label="Close navigation"><Icon name="close"/></button></div><nav aria-label="Primary navigation">{nav.map(([label,href,icon])=><Link key={label} href={href} onClick={()=>setOpen(false)} className={(href==='/'?path==='/':path.startsWith('/meetings')&&label==='Meetings')?'active':''}><Icon name={icon}/><span>{label}</span></Link>)}</nav><div className="sidebar-foot"><span className="avatar">{session.member.display_name.split(' ').map(x=>x[0]).slice(0,2).join('')}</span><div><strong>{session.member.display_name}</strong><small>{session.roles.join(' · ')}</small></div></div></aside><div className="app-column"><header className="topbar"><div className="breadcrumbs"><Link href="/">NCC Convene</Link>{crumb&&<><span>/</span><strong>{crumb}</strong></>}</div><div className="identity"><div><strong>{session.member.display_name}</strong><small>{session.roles.join(' · ')}</small></div><button onClick={logout}>Sign out</button></div></header>{children}</div></div>
}
