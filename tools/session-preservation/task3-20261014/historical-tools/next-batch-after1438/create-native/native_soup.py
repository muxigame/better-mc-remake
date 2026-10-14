from native_control import *
execute(D+'forceload add 16 0');execute(D+'setblock 20 119 0 create:blaze_burner[blaze=kindled]');execute(D+'setblock 20 122 0 create:mechanical_mixer');execute(D+'setblock 21 122 0 create:cogwheel[axis=y]');execute(D+'setblock 21 123 0 create:creative_motor[facing=down]{ScrollValue:64,Speed:64.0f}')
for count in [1,10]:
 execute(D+'setblock 20 120 0 minecraft:air');execute(D+'execute positioned 20 120 0 run kill @e[type=minecraft:item,distance=..4]');execute(D+'setblock 20 120 0 create:basin')
 items=[{'Slot':i,'id':v,'count':count} for i,v in enumerate(['minecraft:carrot','minecraft:potato','minecraft:beetroot','farmersdelight:cabbage','minecraft:bowl'])]
 tag='{Filter:{id:"farmersdelight:vegetable_soup",count:1},InputItems:{Size:9,Items:['+','.join('{Slot:'+str(v['Slot'])+',id:"'+v['id']+'",count:'+str(v['count'])+'}' for v in items)+']}}'
 execute(D+'data merge block 20 120 0 '+tag);execute(D+'data modify storage muxiqa:create soupBefore'+str(count)+' set from block 20 120 0');execute('data get storage muxiqa:create soupBefore'+str(count));time.sleep(25);execute(D+'data modify storage muxiqa:create soupAfter'+str(count)+' set from block 20 120 0');execute('data get storage muxiqa:create soupAfter'+str(count))
print('native soup bowl batch counts captured',flush=True)
