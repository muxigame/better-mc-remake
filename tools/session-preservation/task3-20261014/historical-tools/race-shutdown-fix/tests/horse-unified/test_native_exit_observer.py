"""Synthetic Windows children only: verify bounded observer and native fault capture without MC."""
from pathlib import Path
import argparse,ctypes,datetime,json,subprocess,sys,time,struct
import psutil
from native_exit_observer import Observer,Event,Record,Frame,DumpInfo

def exercise(root,fault):
 folder=root/('native-fault' if fault else 'normal-exit');folder.mkdir(parents=True)
 ready=folder/'child-ready';release=folder/'release'
 code="""import ctypes as c,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from native_exit_observer import Record
k=c.WinDLL('kernel32');k.SetErrorMode(3)
Path(sys.argv[2]).write_text('ready')
end=time.monotonic()+20
while not Path(sys.argv[3]).exists() and time.monotonic()<end:time.sleep(.02)
if sys.argv[4]=='fault':
 record=Record();record.code=0xc0000409;record.flags=1
 api=k.RaiseFailFastException;api.argtypes=[c.POINTER(Record),c.c_void_p,c.c_uint32]
 api(c.byref(record),None,0)
"""
 process=subprocess.Popen([sys.executable,'-B','-X','utf8','-c',code,str(Path(__file__).parent.resolve()),str(ready),str(release),'fault' if fault else 'normal'],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 end=time.monotonic()+10
 while not ready.exists() and process.poll() is None and time.monotonic()<end:time.sleep(.02)
 if not ready.exists():raise AssertionError('Synthetic child did not become ready')
 observer=Observer(psutil.Process(process.pid),folder).start()
 assert observer.ready.wait(10),'Observer attach timeout'
 release.write_text('continue')
 code=process.wait(timeout=20);assert observer.done.wait(10),'Observer did not finish after child exit'
 report=json.loads((folder/'native-exit.json').read_text());assert 'observerError' not in report,report
 assert report.get('exited') and report.get('killOnDebuggerExit') is False,report
 if fault:
  assert code==0xc0000409 and report['events'],(code,report)
  row=report['events'][0];assert row['code']=='0xc0000409' and row.get('dumpWritten') and len(row.get('frames',[]))>=2,row
  data=Path(row['dumpPath']).read_bytes();assert data[:4]==b'MDMP'
  count,directory=struct.unpack_from('<II',data,8);exceptionCodes=[]
  for i in range(count):
   kind,size,rva=struct.unpack_from('<III',data,directory+i*12)
   if kind==6:exceptionCodes.append(struct.unpack_from('<I',data,rva+8)[0])
  assert exceptionCodes==[0xc0000409],exceptionCodes
 else:assert code==0 and not report['events'],(code,report)
 return {'syntheticOnly':True,'fault':fault,'pid':process.pid,'exitCode':code,'stackFrames':len(report['events'][0]['frames']) if fault else 0,'exceptionMinidumpVerified':fault,'childGone':not observer.same()}

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 assert ctypes.sizeof(Event)==176 and ctypes.sizeof(Record)==152 and ctypes.sizeof(Frame)==264 and ctypes.sizeof(DumpInfo)==16
 rows=[exercise(args.output.resolve(),False),exercise(args.output.resolve(),True)]
 (args.output/'summary.json').write_text(json.dumps({'success':True,'syntheticOnly':True,'cases':rows},indent=2));print(json.dumps({'success':True,'syntheticOnly':True,'cases':rows}))
