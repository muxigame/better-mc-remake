import os,sys,json,time
os.environ['MUXI_NATIVE_LAB']=r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\create-native-reload-14-045505"
sys.path.insert(0,r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\tests\native-window")
from control import w,s,click
q=s('observe');print(json.dumps({k:q.get(k) for k in ['screen','widgets']},ensure_ascii=False),flush=True)
b=next(x for x in q['widgets'] if x['text']=='重生');click(b['x']+b['width']/2,b['y']+b['height']/2);time.sleep(2);print('actual measured respawn button clicked through native callback',flush=True)
