"""Bounded in-memory RPC over already trusted SSH; never logs RPC bodies."""
import json,subprocess,threading,queue,time
from pathlib import Path

HOP=(r'C:\Windows\System32\OpenSSH\ssh.exe -T -o BatchMode=yes -o ConnectTimeout=5 '
     r'-o StrictHostKeyChecking=yes root@<PRIVATE_HOST> docker exec -i muxi-auth-muxi-auth-1 '
     r'python /tmp/muxi-music-private-native-20261013-release/stdio_entry.py --data-directory /tmp/muxi-music-private-native-20261013-release --allow-cloud-preflight')


class SshMusicRpc:
    def __init__(self,error):
        self.error=error;self.lock=threading.RLock();self.frames=queue.Queue(maxsize=8);self.next_id=0;self.closed=False
        self.process=subprocess.Popen(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
            '-o','StrictHostKeyChecking=yes','administrator@<PRIVATE_HOST>',HOP],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
        threading.Thread(target=self._discard_errors,daemon=True).start()
        try:
            value=self.frames.get(timeout=20)
            if not value.get('ready') or value['budget']['requests']!=45:
                failure=self.error(503,'qa_trusted_dispatch_unavailable')
                failure.safe_metadata={k:value[k] for k in ['errorType','missingPublicModule'] if k in value}
                raise failure
            self.ready=value  # safe counters only
        except self.error:
            self.process.stdin.close();self.closed=True;raise
        except Exception:
            self.process.stdin.close();self.closed=True
            raise self.error(503,'qa_trusted_dispatch_unavailable') from None

    def _discard_errors(self):
        # Raw libraries' stderr might contain provider parameters. Discard it;
        # only stable music errors/safe state receipts reach callers.
        while self.process.stderr.read(4096):pass

    def _read(self):
        try:
            while True:
                line=self.process.stdout.readline(65537)
                if not line:break
                if len(line)>65536:break
                value=json.loads(line)
                self.frames.put(value,timeout=1)
        except Exception:pass
        finally:
            try:self.frames.put({'closed':True},timeout=1)
            except queue.Full:pass

    def __call__(self,action,body):
        with self.lock:
            if self.closed:raise self.error(503,'qa_rpc_closed')
            self.next_id+=1;identifier=self.next_id
            frame=json.dumps({'id':identifier,'action':action,'body':body},separators=(',',':')).encode()+b'\n'
            if len(frame)>32768:raise self.error(503,'qa_rpc_frame_limit')
            try:
                self.process.stdin.write(frame);self.process.stdin.flush()
                value=self.frames.get(timeout=55)
                if value.get('id')!=identifier:raise ValueError()
                if not value.get('ok'):
                    status=value.get('status');code=value.get('code')
                    if type(status) is not int or not isinstance(code,str) or not code.replace('_','').isalnum():raise ValueError()
                    raise self.error(status,code)
                return value['result']
            except self.error:raise
            except Exception:
                self.closed=True;self.process.stdin.close()
                raise self.error(503,'qa_rpc_unavailable') from None

    def stop(self):
        with self.lock:
            result={}
            if not self.closed:
                try:result=self('stop',{})
                finally:self.closed=True;self.process.stdin.close()
            try:code=self.process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                # No kill/terminate and no success claim for a stalled exit.
                return {'normalExit':False,'status':'qa_rpc_exit_pending'}
            return {'normalExit':code==0,'exitCode':code,'state':result}
