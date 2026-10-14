"""Bound malformed/new-intent traffic only; playback/renewal bypass this guard."""
import time
from collections import OrderedDict,deque
class MusicIntentGuard:
    def __init__(self,app):self.app=app;self.peers=OrderedDict()
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method')!='POST' or scope.get('path')!='/api/launcher/music/uploads':return await self.app(scope,receive,send)
        host=(scope.get('client') or ('unknown',0))[0];now=time.monotonic()
        self.peers.setdefault(host,(deque(),deque(),0));self.peers.move_to_end(host)
        if len(self.peers)>4096:self.peers.popitem(last=False)
        attempts,errors,inflight=self.peers[host]
        for queue in [attempts,errors]:
            while queue and queue[0]<=now-60:queue.popleft()
        async def reject(status,code):
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store'),(b'retry-after',b'60')]})
            await send({'type':'http.response.body','body':('{"error":"'+code+'"}').encode()})
        if len(attempts)>=500 or len(errors)>=50 or inflight>=8:return await reject(429,'music_intent_rate_limit')
        attempts.append(now);self.peers[host]=(attempts,errors,inflight+1)
        try:
            body=bytearray()
            while True:
                message=await receive()
                if message['type']=='http.disconnect':return
                body.extend(message.get('body',b''))
                if len(body)>16384:errors.append(time.monotonic());return await reject(413,'music_intent_body_limit')
                if not message.get('more_body',False):break
            sent=False
            async def replay():
                nonlocal sent
                if not sent:sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
                return await receive()
            async def observe(message):
                if message['type']=='http.response.start' and message['status']>=400:errors.append(time.monotonic())
                await send(message)
            await self.app(scope,replay,observe)
        finally:
            # No awaits between peer mutations: one ASGI event-loop owner.
            if host in self.peers:self.peers[host]=(attempts,errors,max(0,self.peers[host][2]-1))
