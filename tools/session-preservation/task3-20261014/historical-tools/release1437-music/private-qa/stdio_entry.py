"""Explicit runtime only, inside original backend container through existing SSH.
No public listener. Never prints request headers, proof, signed URL or traceback.
"""
def main():
    import argparse,sys,json,time,select,logging,asyncio,importlib.util
    from pathlib import Path
    ap=argparse.ArgumentParser();ap.add_argument('--data-directory',required=True);ap.add_argument('--allow-cloud-preflight',action='store_true');args=ap.parse_args()
    if Path.cwd()!=Path('/app'):raise ValueError('original_backend_required')
    sys.path.insert(0,'/app');logging.disable(logging.CRITICAL)
    # Loading stays in the original container. No key/config values leave it.
    from app import main as baseline
    spec=importlib.util.spec_from_file_location('app.controlled_native_private_qa',Path(__file__).with_name('controlled_native_private_qa.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from wiring import Dispatch
    qa=module.ControlledNativeQa(baseline,args.data_directory,132344,'436c5777f4c35a4de3d0678ad272ec116b7018f8fb975e9d2638c2b7c09a9985','/usr/bin/ffmpeg')
    dispatch=Dispatch(qa,preflight_authorized=args.allow_cloud_preflight)
    print(json.dumps({'ready':True,**dispatch.state()}),flush=True);end=time.monotonic()+1200
    try:
        while not dispatch.closed and time.monotonic()<end:
            ready,_,_=select.select([sys.stdin],[],[],min(5,max(0,end-time.monotonic())))
            if not ready:continue
            line=sys.stdin.buffer.readline(32769)
            if not line or len(line)>32768:break
            identifier=None
            try:
                f=json.loads(line);identifier=f.get('id')
                if set(f)!={'id','action','body'} or type(identifier) is not int:raise ValueError()
                result=asyncio.run(dispatch.call(f['action'],f['body']))
                response={'id':identifier,'ok':True,'result':result}
            except Exception:response={'id':identifier,'ok':False,'status':503,'code':'qa_dispatch_refused'}
            encoded=json.dumps(response,separators=(',',':'))
            if len(encoded.encode())>65536:break
            print(encoded,flush=True)
    finally:
        qa.stop();print(json.dumps({'closed':True,'budget':qa.budget.snapshot()}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception:
        print('{"ready":false,"code":"qa_start_refused"}',flush=True);raise SystemExit(1)
