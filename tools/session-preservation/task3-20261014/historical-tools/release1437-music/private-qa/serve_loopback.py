"""Prepared runtime controller; --run is explicit and never used in offline tests.
TLS files are private QA files supplied by the owner, not global CA changes.
stdin accepts only preflight/stop; no control endpoint or public listener.
"""
def main():
    import argparse,os,sys,json,threading,time
    from pathlib import Path
    ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--port',type=int,required=True);ap.add_argument('--cert',type=Path,required=True);ap.add_argument('--key',type=Path,required=True);ap.add_argument('--ready',type=Path,required=True);args=ap.parse_args()
    if not args.run:raise SystemExit('Prepared only; runtime requires assigned MC window and --run')
    if args.ready.exists():raise ValueError('fresh_ready_path_required')
    if not 1024<=args.port<=65535 or not args.cert.is_file() or not args.key.is_file():raise ValueError('private_tls_configuration_required')
    cap=os.environ.get('MUXI_MUSIC_QA_NETWORK_CAP','')
    if len(cap)<32:raise ValueError('ephemeral_qa_capability_required')
    from wiring import loopback_app
    from ssh_rpc import SshMusicRpc
    import uvicorn
    class Error(Exception):
        def __init__(self,status,code):self.status,self.code=status,code
    rpc=SshMusicRpc(Error)
    server=uvicorn.Server(uvicorn.Config(loopback_app(rpc,cap),host='127.0.0.1',port=args.port,
        ssl_certfile=str(args.cert),ssl_keyfile=str(args.key),access_log=False,log_config=None,log_level='critical'))
    def controls():
        try:
            for line in sys.stdin:
                if len(line)>256:break
                value=json.loads(line)
                if value=={'action':'preflight','verifiedCoreUid':10000}:
                    # Operator submits only after observing original native Core
                    # context; original backend still checks Bearer/live binding.
                    result=rpc('preflight',{});print(json.dumps({'preflight':result}),flush=True)
                elif value=={'action':'stop'}:break
                else:break
        finally:server.should_exit=True
    def readiness():
        while not server.started:
            if server.should_exit:return
            time.sleep(0.05)
        with args.ready.open('x',encoding='utf-8') as out:
            json.dump({'url':f'https://127.0.0.1:{args.port}','preflightPassed':False,'productionEnabled':False,'control':'owner stdin only'},out)
        controls()
    thread=threading.Thread(target=readiness,daemon=True);thread.start()
    try:server.run()
    finally:
        receipt=rpc.stop();args.ready.with_name('private-qa-normal-exit.json').write_text(json.dumps(receipt),encoding='utf-8')
        if not receipt.get('normalExit'):raise RuntimeError('qa_exit_not_confirmed')
if __name__=='__main__':main()
