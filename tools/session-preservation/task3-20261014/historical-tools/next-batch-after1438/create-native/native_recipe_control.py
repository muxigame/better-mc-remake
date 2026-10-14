from native_control import *
for count in [1,10]:
 execute(D+'setblock 0 120 0 minecraft:air')
 execute(D+'execute positioned 0 120 0 run kill @e[type=minecraft:item,distance=..5]')
 execute(D+'setblock 0 120 0 create:basin')
 execute(D+'data merge block 0 120 0 {Filter:{id:"minecraft:melon_slice",count:1},InputItems:{Size:9,Items:[{Slot:0,id:"minecraft:melon",count:'+str(count)+'}]}}')
 execute(D+'data modify storage muxiqa:create before'+str(count)+' set from block 0 120 0')
 execute('data get storage muxiqa:create before'+str(count));time.sleep(20)
 execute(D+'data modify storage muxiqa:create after'+str(count)+' set from block 0 120 0')
 execute('data get storage muxiqa:create after'+str(count))
print('controlled FarmersDelight cutting counts captured',flush=True)
