export type Member = {id:string; display_name:string; title?:string|null; organisation_name:string};
export type Session = {member:Member; roles:string[]; permissions:string[]; csrf:string};
export type Meeting = {id:string; title:string; starts_at:string; location:string; status:string; quorum_percent:number; video_provider?:string};
export type AgendaItem = {id:string; title:string; position:number; parent_id?:string|null};
export type Participant = {id:string; member_id:string; display_name:string; status:string; observer:number};
export type Document = {id:string; agenda_item_id?:string|null; title:string; classification:string; status:string; version_number:number; size_bytes:number; content_type:string};
export type Conflict = {id:string; agenda_item_id?:string|null; member_id:string; interest:string; management_action:string; status:string};
export type Motion = {id:string; agenda_item_id:string; proposer_member_id:string; text:string; status:string; tally:{for:number;against:number;abstain:number;total:number;passed?:boolean}};
export type Resolution = {id:string; agenda_item_id:string; motion_id?:string|null; text:string; outcome:string; status:string; resolution_year:number; resolution_number:number};
export type Minute = {id:string; agenda_item_id:string; body:string; status:string; items:{id:string;item_type:string;body:string;position:number}[]};
export type Action = {id:string; agenda_item_id:string; resolution_id?:string|null; owner_member_id:string; owner_name?:string; description:string; due_at?:string|null; priority:string; status:string; action_year:number; action_number:number; update_count:number; evidence_count:number; is_overdue:number};
export type Workspace = {meeting:Meeting; agenda:AgendaItem[]; participants:Participant[]; quorum:{eligible:number;present:number;required:number;met:boolean}; documents:Document[]; conflicts:Conflict[]; motions:Motion[]; resolutions:Resolution[]; minutes:Minute[]; actions:Action[]};
export type Paper = {id:string;document_id:string;meeting_id:string;agenda_item_id:string;title:string;purpose:string;background:string;recommendation:string;implications:string;classification:string;status:string;version_number:number;current_revision_id:string;lock_version:number;agenda_title:string;author_member_id:string};

export class ApiError extends Error { constructor(public status:number, public code:string, message:string){super(message)} }

export async function request<T>(url:string, init:RequestInit={}):Promise<T>{
  const response=await fetch(url,{...init,credentials:'include',headers:{Accept:'application/json',...init.headers}});
  let value:unknown;
  try{value=await response.json()}catch{value=null}
  if(!response.ok){
    const payload=value as {error?:string|{code?:string;message?:string}}|null;
    const nested=typeof payload?.error==='object'?payload.error:null;
    throw new ApiError(response.status,nested?.code||String(payload?.error||'request_failed'),nested?.message||String(payload?.error||`Request failed (${response.status})`));
  }
  return value as T;
}

export const getSession=()=>request<Session>('/api/v1/session/me');
export const getMeetings=()=>request<{items:Meeting[]}>('/api/v1/meetings');
export const getWorkspace=(id:string)=>request<Workspace>(`/api/v1/meetings/${encodeURIComponent(id)}/workspace`);
export function mutate<T>(url:string, csrf:string, data:Record<string,unknown>={}){return request<T>(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)})}
export function message(error:unknown){return error instanceof ApiError?error.message:error instanceof Error?error.message:'Something went wrong.'}
