import os,sys,json,time
from pathlib import Path
os.environ['MUXI_NATIVE_LAB']=r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\jui-layout2-14-030112"
sys.path.insert(0,r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\tests\native-window")
from control import w,ev,chrome,shot
w('app',name='tasks');time.sleep(1);w('app',name='games');time.sleep(3);ev("(()=>{document.querySelector('[data-mg-page=shop]').click();const s=document.querySelector('#mg-shop-game');s.value='horse_racing';s.dispatchEvent(new Event('change',{bubbles:true}));return {restored:true}})()");time.sleep(2)
w('scale',value=2)
if not w('observe')['fullscreen']:chrome('windowMode')
time.sleep(1)
ev("(()=>{const g=[...document.querySelectorAll('.mg-product-card img')].find(x=>x.src.startsWith('data:image/gif'));g.closest('.mg-product-card').scrollIntoView({block:'start'});return {found:!!g}})()")
time.sleep(1)
q=ev("(()=>{const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};return {images:[...document.querySelectorAll('.mg-product-card img')].filter(x=>x.src.startsWith('data:image/gif')).map(x=>({id:x.dataset.terminalIconId,rect:rect(x),complete:x.complete,naturalWidth:x.naturalWidth})),innerHeight,innerWidth}})()")
state=w('observe');q['windowState']={k:state[k] for k in ['guiScale','layout','framebufferWidth','framebufferHeight']}
shot('layout2-sixgif-frame-a.png');time.sleep(.71);shot('layout2-sixgif-frame-b.png')
ev("(()=>{const x=[...document.querySelectorAll('.mg-product-card img')].find(x=>x.src.startsWith('data:image/gif'));window.qaFallbackTarget=x.closest('.mg-preview-media');x.dispatchEvent(new Event('error'));return {syntheticErrorDispatched:true}})()")
time.sleep(1)
q['fallback']=ev("(()=>{const x=window.qaFallbackTarget,b=x.querySelector('.mg-preview-backup'),i=b.querySelector('img');return {mediaClass:x.className,backupDisplay:getComputedStyle(b).display,backupRect:(()=>{const r=b.getBoundingClientRect();return {width:r.width,height:r.height}})(),pngDecoded:i.complete&&i.naturalWidth>0,primaryHidden:x.querySelector('.mg-preview-primary img').hidden}})()")
assert q['fallback']['pngDecoded'] and q['fallback']['primaryHidden'],q['fallback']
shot('layout2-sixgif-png-fallback.png');Path(__file__).with_name('media-native.json').write_text(json.dumps(q,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(q['fallback']),flush=True)
