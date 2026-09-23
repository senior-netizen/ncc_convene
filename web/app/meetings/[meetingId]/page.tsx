import {MeetingWorkspace} from './workspace';
export default async function Page({params}:{params:Promise<{meetingId:string}>}) {const {meetingId}=await params;return <MeetingWorkspace meetingId={meetingId}/>}
