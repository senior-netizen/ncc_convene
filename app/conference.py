"""Small LiveKit boundary: browser grants here, privileged operations only here."""
import os
from datetime import timedelta


class ConferenceConfigurationError(RuntimeError): pass


class LiveKitProvider:
    def __init__(self, url=None, api_key=None, api_secret=None):
        self.url = url or os.getenv("LIVEKIT_URL")
        self.api_key = api_key or os.getenv("LIVEKIT_API_KEY")
        self.api_secret = api_secret or os.getenv("LIVEKIT_API_SECRET")

    @property
    def configured(self): return bool(self.url and self.api_key and self.api_secret)

    def participant_token(self, room, identity, name, *, can_publish, can_subscribe=True, metadata=""):
        if not self.configured: raise ConferenceConfigurationError("LiveKit is not configured")
        from livekit import api
        grants = api.VideoGrants(room_join=True, room=room, can_publish=can_publish, can_subscribe=can_subscribe, can_publish_data=True)
        token = api.AccessToken(self.api_key, self.api_secret).with_identity(identity).with_name(name).with_metadata(metadata).with_ttl(timedelta(minutes=5)).with_grants(grants)
        return token.to_jwt()

    async def remove(self, room, identity):
        from livekit import api
        client = api.LiveKitAPI(self.url, self.api_key, self.api_secret)
        try: await client.room.remove_participant(api.RoomParticipantIdentity(room=room, identity=identity))
        finally: await client.aclose()

    async def mute_track(self, room, identity, track_sid, muted=True):
        from livekit import api
        client = api.LiveKitAPI(self.url, self.api_key, self.api_secret)
        try: await client.room.mute_published_track(api.MuteRoomTrackRequest(room=room, identity=identity, track_sid=track_sid, muted=muted))
        finally: await client.aclose()

    async def delete_room(self, room):
        from livekit import api
        client = api.LiveKitAPI(self.url, self.api_key, self.api_secret)
        try: await client.room.delete_room(api.DeleteRoomRequest(room=room))
        finally: await client.aclose()


provider = LiveKitProvider()
