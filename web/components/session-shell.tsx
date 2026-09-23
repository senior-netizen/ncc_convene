'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import type {Session} from '../lib/api';
import {request} from '../lib/api';

export function SessionHeader({session}:{session:Session}){
 const router=useRouter();
 async function logout(){await request('/api/v1/session/logout',{method:'POST'});router.replace('/login');router.refresh()}
 return <header className="topbar"><Link className="brand" href="/">NCC <span>Convene</span></Link><div className="identity"><div><strong>{session.member.display_name}</strong><small>{session.roles.join(' · ')}</small></div><button className="quiet" onClick={logout}>Sign out</button></div></header>
}
