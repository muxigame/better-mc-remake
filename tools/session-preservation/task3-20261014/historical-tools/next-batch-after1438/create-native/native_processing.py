from native_control import *
execute(D+'data merge block 1 123 0 {ScrollValue:64}')
execute(D+'setblock 0 120 0 create:basin destroy')
execute(D+'data merge block 0 120 0 {InputItems:{Size:9,Items:[{Slot:0,id:"minecraft:melon",count:1}]}}')
time.sleep(20)
for label,pos in [('slicer64','0 122 0'),('basin64','0 120 0')]:execute(D+'data modify storage muxiqa:create '+label+' set from block '+pos)
execute('data get storage muxiqa:create')
print('processing snapshot recorded',flush=True)
