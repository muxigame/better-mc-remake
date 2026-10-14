"""Offline-prepared, injected private QA wiring. Import starts nothing.

Music requests only; authentication remains inside the original Auth backend.
No synthetic account, caller UID, persistent public route, or cloud permission.
"""
import json,re,hmac,asyncio

FIXED_JOB='ea8bbe98-43f0-462c-bd51-fec644838448'
MAX_FRAME=32768
def allowed(method,path):
    return (method=='GET' and path=='/api/music/capabilities') or (method=='POST' and (
        path in ['/api/launcher/music/uploads','/api/launcher/music/playback','/api/minecraft/music/burn-receipt/redeem']
        or path in [f'/api/launcher/music/uploads/{FIXED_JOB}/complete',f'/api/launcher/music/uploads/{FIXED_JOB}/cancel']))

class Dispatch:
    def __init__(self,qa,*,preflight_authorized=False):
        from fastapi import FastAPI
        self.qa=qa;self.preflight_authorized=preflight_authorized;self.closed=False
        self.app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        self.app.include_router(qa.router)
    def state(self):
        return {'closed':self.closed,'preflightPassed':self.qa.preflight_passed,'budget':self.qa.budget.snapshot()}
    async def call(self,action,body):
        if not isinstance(body,dict):raise ValueError('qa_frame_refused')
        if action=='state' and not body:return self.state()
        if action=='stop' and not body:
            self.qa.stop();self.closed=True;return self.state()
        if self.closed:raise ValueError('qa_closed')
        if action=='preflight' and not body:
            if not self.preflight_authorized:raise ValueError('qa_preflight_not_authorized')
            if self.qa.preflight_passed:raise ValueError('qa_preflight_already_consumed')
            return await asyncio.to_thread(self.qa.preflight)
        if action!='http' or set(body)!={'method','path','headers','body'}:raise ValueError('qa_action_refused')
        method,path,headers,data=(body[k] for k in ['method','path','headers','body'])
        if not isinstance(method,str) or not isinstance(path,str) or not allowed(method,path):raise ValueError('qa_route_refused')
        if not isinstance(headers,dict) or any(k not in ['authorization','content-type'] for k in headers):raise ValueError('qa_headers_refused')
        if any(not isinstance(v,str) or len(v)>16384 or '\r' in v or '\n' in v for v in headers.values()):raise ValueError('qa_headers_refused')
        if not isinstance(data,str) or len(data.encode())>MAX_FRAME:raise ValueError('qa_body_refused')
        if not self.qa.preflight_passed and path!='/api/music/capabilities':return {'status':503,'body':{'error':'music_qa_preflight_required'}}
        if not self.qa.preflight_passed:return {'status':200,'body':{'enabled':False,'playback':'qa-preflight-required'}}
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://private-stdio.invalid',follow_redirects=False,timeout=55) as client:
            response=await client.request(method,path,headers=headers,content=data)
            if len(response.content)>65536:raise ValueError('qa_response_limit')
            return {'status':response.status_code,'body':response.json()}

def loopback_app(rpc,capability):
    """Caller owns scoped TLS socket and SSH lifecycle. No listener is created."""
    from fastapi import FastAPI,Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    if not isinstance(capability,str) or len(capability)<32:raise ValueError('qa_ephemeral_capability_required')
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    async def relay(request:Request):
        if request.client is None or request.client.host!='127.0.0.1':return JSONResponse({'error':'qa_loopback_required'},403)
        if not hmac.compare_digest(request.headers.get('x-muxi-music-qa',''),capability):return JSONResponse({'error':'qa_capability_required'},403)
        path=request.url.path
        if request.url.query or not allowed(request.method,path):return JSONResponse({'error':'qa_route_refused'},404)
        chunks=bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks)>MAX_FRAME:return JSONResponse({'error':'qa_body_limit'},413)
        headers={k:request.headers[k] for k in ['authorization','content-type'] if k in request.headers}
        try:
            data=bytes(chunks).decode('utf-8')
            result=await run_in_threadpool(rpc,'http',{'method':request.method,'path':path,'headers':headers,'body':data})
            return JSONResponse(result['body'],result['status'],headers={'Cache-Control':'no-store'})
        except Exception:return JSONResponse({'error':'qa_private_transport_unavailable'},503)
    app.add_api_route('/{path:path}',relay,methods=['GET','POST'])
    return app
