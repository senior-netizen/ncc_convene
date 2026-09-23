'use client';
import {FormEvent,useState} from 'react';
import {useRouter} from 'next/navigation';
import {message,request} from '../../lib/api';

export default function Login(){const router=useRouter();const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 async function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();setBusy(true);setError('');const form=new FormData(event.currentTarget);try{await request('/api/v1/session/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.get('email'),password:form.get('password')})});router.replace('/');router.refresh()}catch(e){setError(message(e))}finally{setBusy(false)}}
 return <main className="login-page"><section className="login-card"><div className="eyebrow">National Competitiveness Commission</div><h1>Board meeting workspace</h1><p>Sign in with your NCC Convene account. Your organisation is determined by the secure server session.</p>{error&&<div className="alert error" role="alert">{error}</div>}<form onSubmit={submit}><label>Email address<input name="email" type="email" autoComplete="username" required autoFocus defaultValue="secretariat@ncc.example"/></label><label>Password<input name="password" type="password" autoComplete="current-password" required defaultValue="ChangeMe123!"/></label><button className="primary" disabled={busy}>{busy?'Signing in…':'Sign in'}</button></form><small>Demonstration accounts use sample data. Do not use these credentials outside a local demo.</small></section></main>}
