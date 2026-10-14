import os,sys,json,time,hashlib
from pathlib import Path
LAB=Path(r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\build\local-mc-debug\jui-layout2-14-030112")
os.environ['MUXI_NATIVE_LAB']=str(LAB)
sys.path.insert(0,r"C:\Users\ranzh\workspace\dev\muxigame\muxi-terminal\tests\native-window")
from control import w,ev,wait,chrome,shot
OUT=Path(__file__).parent;checks=[]
def save(): (OUT/'native-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
def measure(label):
 q=ev("(()=>{const f=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,bottom:r.bottom}};const c=document.querySelector('.mg-product-card');return {height:innerHeight,width:innerWidth,content:f(document.querySelector('#mg-content')),card:f(c),image:f(c?.querySelector('.mg-card-preview')),price:f(c?.querySelector('.mg-product-price')),cta:f(c?.querySelector('.detail-actions')),images:[...document.querySelectorAll('.mg-product-card img')].map(x=>({src:x.src,complete:x.complete,naturalWidth:x.naturalWidth,naturalHeight:x.naturalHeight}))}})()")
 assert q.get('card') and q.get('cta'),q
 assert q['cta']['bottom']<=q['content']['bottom']+1,q
 assert q['card']['y']>=q['content']['y']-1,q
 checks.append({'case':label,'passed':True,'measurement':q});save();shot(label+'.png');return q
wait(lambda q:not q.get('reloading'),60);w('open');wait(lambda q:q.get('mcef') and q.get('shellId'),60);w('app',name='games');time.sleep(4)
ev("(()=>{const b=document.querySelector('[data-mg-page=shop]');b.click();return {clicked:true}})()");time.sleep(2)
ev("(()=>{const s=document.querySelector('#mg-shop-game');const o=[...s.options].find(x=>/horse|racing/.test(x.value));if(o){s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}));}return {options:[...s.options].map(x=>({value:x.value,text:x.text})),selected:s.value}})()");time.sleep(2)
for scale in [2,4]:
 w('scale',value=scale);time.sleep(2);measure('layout2-scale'+str(scale)+'-normal');chrome('windowMode');time.sleep(2);measure('layout2-scale'+str(scale)+'-fullscreen');chrome('windowMode');time.sleep(1)
w('scale',value=2);time.sleep(2)
q=ev("(()=>{const s=document.querySelector('#mg-shop-game');return {options:[...s.options].map(x=>({value:x.value,text:x.text}))}})()");checks.append({'case':'actual-shop-categories','data':q});save()
media=ev("(()=>({images:[...document.querySelectorAll('.mg-product-card img')].map(x=>({src:x.src,complete:x.complete,naturalWidth:x.naturalWidth,naturalHeight:x.naturalHeight}))}))()")
checks.append({'case':'actual-media-decoded','data':media});save()
shot('layout2-animation-frame-a.png');time.sleep(.65);shot('layout2-animation-frame-b.png')
fallback=ev("(()=>{const x=document.querySelector('.mg-preview-primary img');if(!x)return {triggered:false};x.dispatchEvent(new Event('error'));return {triggered:true}})()")
time.sleep(1);fallback.update(ev("(()=>{const x=document.querySelector('.mg-preview-media');return {className:x?.className,backupVisible:!!x?.querySelector('.mg-preview-backup')&&getComputedStyle(x.querySelector('.mg-preview-backup')).display!=='none',images:[...x?.querySelectorAll('img')||[]].map(x=>({src:x.src,complete:x.complete,naturalWidth:x.naturalWidth}))}})()"))
checks.append({'case':'synthetic-image-error-real-page-png-fallback','data':fallback});save();shot('layout2-png-fallback.png')
print(json.dumps(checks,ensure_ascii=False),flush=True)
