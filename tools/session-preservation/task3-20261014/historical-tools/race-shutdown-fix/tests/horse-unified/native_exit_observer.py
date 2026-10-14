"""Local-only Windows exit diagnostics for direct owned children; never terminate a target.
Attach only for the coordinated shutdown diagnostic, not a performance benchmark.
"""
from pathlib import Path
import ctypes as c,json,os,struct,threading,time
import psutil
D=c.c_uint32;Q=c.c_uint64;P=c.c_void_p;B=c.c_int;W=c.c_uint16
DBG_LOCK=threading.Lock()
class Record(c.Structure):_fields_=[('code',D),('flags',D),('next',P),('address',P),('count',D),('info',Q*15)]
class ExceptionInfo(c.Structure):_fields_=[('record',Record),('first',D)]
class CreateProcess(c.Structure):_fields_=[('file',P),('process',P),('thread',P),('base',P),('offset',D),('size',D),('tls',P),('start',P),('image',P),('unicode',W)]
class LoadDll(c.Structure):_fields_=[('file',P),('base',P),('offset',D),('size',D),('image',P),('unicode',W)]
class EventData(c.Union):_fields_=[('exception',ExceptionInfo),('process',CreateProcess),('dll',LoadDll),('exitCode',D),('raw',c.c_byte*160)]
class Event(c.Structure):_fields_=[('code',D),('pid',D),('tid',D),('data',EventData)]
class ModInfo(c.Structure):_fields_=[('base',P),('size',D),('entry',P)]
class Address(c.Structure):_fields_=[('offset',Q),('segment',W),('mode',D)]
class KdHelp(c.Structure):_fields_=[('thread',Q),('callbackStack',D),('callbackBStore',D),('next',D),('frame',D),('addresses',Q*6),('build',D),('retSize',D),('retTable',Q),('retOffset',D),('retStubSize',D),('reserved',Q*2)]
class Frame(c.Structure):_fields_=[('pc',Address),('returnAddress',Address),('frame',Address),('stack',Address),('bstore',Address),('table',P),('params',Q*4),('far',B),('virtual',B),('reserved',Q*3),('help',KdHelp)]
class Pointers(c.Structure):_fields_=[('record',P),('context',P)]
class DumpInfo(c.Structure):
 _pack_=4 # Windows SDK minidumpapiset.h wraps this declaration in pshpack4.h.
 _fields_=[('thread',D),('pointers',P),('clientPointers',B)]

def api(lib,name,ret,*args):
 fn=getattr(lib,name);fn.restype=ret;fn.argtypes=list(args);return fn

class Observer:
 def __init__(self,process,output):
  if os.name!='nt' or c.sizeof(P)!=8:raise ValueError('Windows x64 only')
  if process.ppid()!=os.getpid():raise ValueError('Only a direct owned child may be observed')
  self.pid=process.pid;self.created=process.create_time();self.output=Path(output).resolve();self.output.mkdir(parents=True,exist_ok=True)
  self.ready=threading.Event();self.done=threading.Event();self.cancel=threading.Event();self.events=[];self.modules={};self.report={'pid':self.pid,'createdAt':self.created,'localOnly':True,'killOnDebuggerExit':False,'diagnosticObserver':True,'events':self.events};self.thread=threading.Thread(target=self.run,daemon=True)
 def start(self):self.thread.start();return self
 def same(self):
  try:p=psutil.Process(self.pid);return abs(p.create_time()-self.created)<.01 and p.ppid()==os.getpid()
  except psutil.Error:return False
 def write(self):
  file=self.output/'native-exit.json';tmp=file.with_suffix('.tmp');tmp.write_text(json.dumps(self.report,indent=2));tmp.replace(file)
 def mapped(self,address):
  for base,row in self.modules.items():
   if base<=address<base+row['size']:return {'address':hex(address),'module':row['name'],'offset':hex(address-base)}
  return {'address':hex(address),'module':'unknown'}
 def module(self,handle,base,file):
  name='unknown';buffer=c.create_unicode_buffer(32768)
  if file and self.fileName(file,buffer,len(buffer),0):name=Path(buffer.value).name
  info=ModInfo()
  if self.modInfo(handle,P(base),c.byref(info),c.sizeof(info)):self.modules[base]={'name':name,'size':info.size}
 def capture(self,handle,event):
  record=Record.from_buffer_copy(event.data.exception.record);record.next=None # The dump uses a local record, so it must not follow a remote chain.
  row={'code':hex(record.code),'firstChance':bool(event.data.exception.first),'threadId':event.tid,'exception':self.mapped(record.address or 0),'parameters':[int(n) for n in record.info[:min(record.count,15)]]}
  thread=self.openThread(0x48,False,event.tid)
  if not thread:row['contextError']=c.get_last_error();return row
  try:
   storage=c.create_string_buffer(1232+16);context=(c.addressof(storage)+15)&~15;c.c_uint32.from_address(context+48).value=0x100003
   if not self.getContext(thread,P(context)):row['contextError']=c.get_last_error();return row
   raw=c.string_at(context,1232);rsp=struct.unpack_from('<Q',raw,152)[0];rbp=struct.unpack_from('<Q',raw,160)[0];rip=struct.unpack_from('<Q',raw,248)[0]
   with DBG_LOCK:
    dump=self.output/('exception-%x-%d.dmp'%(record.code,event.tid));file=self.createFile(str(dump),0x40000000,0,None,2,0x80,None)
    pointers=Pointers(c.addressof(record),context);exception=DumpInfo(event.tid,c.addressof(pointers),False)
    if file not in (None,c.c_void_p(-1).value):
     try:row['dumpWritten']=bool(self.dump(handle,self.pid,file,0x1000,c.byref(exception),None,None));row['dumpPath']=str(dump);row['dumpError']=None if row['dumpWritten'] else c.get_last_error()
     finally:self.close(file)
    self.symOptions(0x00080000|0x00000200|0x00000004|0x00001000)
    row['frames']=[self.mapped(rip)]
    if self.symInit(handle,str(self.output),True):
     try:
      frame=Frame();frame.pc=Address(rip,0,3);frame.frame=Address(rbp,0,3);frame.stack=Address(rsp,0,3)
      for _ in range(31):
       if not self.walk(0x8664,handle,thread,c.byref(frame),P(context),None,c.cast(self.functionTable,P),c.cast(self.moduleBase,P),None) or not frame.pc.offset:break
       current=self.mapped(frame.pc.offset)
       if current==row['frames'][-1]:continue
       row['frames'].append(current)
     finally:self.symCleanup(handle)
   return row
  finally:self.close(thread)
 def run(self):
  attached=False;exited=False;handle=None
  try:
   if not self.same():raise ValueError('Owned child identity changed before attach')
   kernel=c.WinDLL('kernel32',use_last_error=True);psapi=c.WinDLL('psapi',use_last_error=True);dbg=c.WinDLL('dbghelp',use_last_error=True)
   self.close=api(kernel,'CloseHandle',B,P);self.openThread=api(kernel,'OpenThread',P,D,B,D);self.getContext=api(kernel,'GetThreadContext',B,P,P)
   self.fileName=api(kernel,'GetFinalPathNameByHandleW',D,P,c.c_wchar_p,D,D);self.modInfo=api(psapi,'GetModuleInformation',B,P,P,c.POINTER(ModInfo),D)
   self.createFile=api(kernel,'CreateFileW',P,c.c_wchar_p,D,D,P,D,D,P);self.dump=api(dbg,'MiniDumpWriteDump',B,P,D,P,D,c.POINTER(DumpInfo),P,P)
   self.symInit=api(dbg,'SymInitializeW',B,P,c.c_wchar_p,B);self.symOptions=api(dbg,'SymSetOptions',D,D);self.symCleanup=api(dbg,'SymCleanup',B,P)
   self.functionTable=api(dbg,'SymFunctionTableAccess64',P,P,Q);self.moduleBase=api(dbg,'SymGetModuleBase64',Q,P,Q);self.walk=api(dbg,'StackWalk64',B,D,P,P,c.POINTER(Frame),P,P,P,P,P)
   attach=api(kernel,'DebugActiveProcess',B,D);detach=api(kernel,'DebugActiveProcessStop',B,D);killOnExit=api(kernel,'DebugSetProcessKillOnExit',B,B);wait=api(kernel,'WaitForDebugEvent',B,c.POINTER(Event),D);resume=api(kernel,'ContinueDebugEvent',B,D,D,D)
   openProcess=api(kernel,'OpenProcess',P,D,B,D);handle=openProcess(0x410,False,self.pid)
   if not handle:raise c.WinError(c.get_last_error())
   if not attach(self.pid):raise c.WinError(c.get_last_error())
   attached=True
   if not killOnExit(False):raise c.WinError(c.get_last_error())
   self.report['attached']=True;self.write();self.ready.set();deadline=time.monotonic()+240;initialBreakpoint=True
   while time.monotonic()<deadline and not self.cancel.is_set():
    event=Event()
    if not wait(c.byref(event),100):
     if not self.same():break
     continue
    continuation=0x00010002
    try:
     if event.pid!=self.pid:raise ValueError('Unexpected debug target')
     if event.code==3:
      self.module(handle,event.data.process.base or 0,event.data.process.file)
      if event.data.process.file:self.close(event.data.process.file)
     elif event.code==6:
      self.module(handle,event.data.dll.base or 0,event.data.dll.file)
      if event.data.dll.file:self.close(event.data.dll.file)
     elif event.code==7:self.modules.pop(c.c_void_p.from_buffer(event.data).value,None)
     elif event.code==1:
      record=event.data.exception.record
      if record.code==0x80000003 and initialBreakpoint:initialBreakpoint=False
      else:
       continuation=0x80010001 # Deliver to the target's normal exception handling; never swallow its fault.
       if not event.data.exception.first or record.code in (0xc0000409,0xc0000602):self.events.append(self.capture(handle,event));self.write()
     elif event.code==5:self.report['exitCode']=int(event.data.exitCode);exited=True
    finally:
     if not resume(event.pid,event.tid,continuation):raise c.WinError(c.get_last_error())
    if exited:break
   self.report['exited']=exited
  except BaseException as exc:self.report['observerError']=str(exc)
  finally:
   if attached and not exited:
    self.report['detached']=bool(detach(self.pid))
   if handle:self.close(handle)
   self.write();self.ready.set();self.done.set()

def observe_owned(lab,role,output):
 lab=Path(lab).resolve();marker=json.loads((lab/'local-mc-owner.json').read_text())
 if role not in ('host','guest') or Path(marker['instanceRoot']).resolve()!=lab:raise ValueError('Marked own horse client required')
 item=marker['processes'][role];process=psutil.Process(item['pid'])
 expected=str((lab/role/'launch.args').resolve()).replace('\\','/').lower()
 command=' '.join(process.cmdline()).replace('\\','/').lower()
 if abs(process.create_time()-item['processCreateTime'])>.01 or expected not in command or process.name().lower() not in ('java.exe','javaw.exe'):raise ValueError('Exact own client identity required')
 return Observer(process,output).start()
