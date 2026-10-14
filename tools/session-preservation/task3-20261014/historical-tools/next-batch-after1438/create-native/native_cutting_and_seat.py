from native_control import *
execute(D+'setblock 1 122 0 minecraft:air');execute(D+'setblock 1 123 0 minecraft:air');execute(D+'setblock 1 123 0 create:creative_motor[facing=down]{ScrollValue:-64,Speed:-64.0f}');execute(D+'setblock 1 122 0 create:cogwheel[axis=y]')
time.sleep(15)
execute(D+'data modify storage muxiqa:create cutOne set from block 0 120 0');execute('data get storage muxiqa:create cutOne')
execute(D+'data merge block 0 120 0 {InputItems:{Size:9,Items:[{Slot:0,id:"minecraft:melon",count:10}]},OutputItems:{Size:9,Items:[]}}');time.sleep(20)
execute(D+'data modify storage muxiqa:create cutTen set from block 0 120 0');execute('data get storage muxiqa:create cutTen')
execute(D+'setblock 4 120 0 minecraft:air');time.sleep(2)
execute(D+'execute store success storage muxiqa:create noSeatEntity byte 1 unless entity @e[type=interiors:big_seat]');execute('data get storage muxiqa:create noSeatEntity')
execute(D+'data get entity 10000 RootVehicle');print('cutting and seat removal snapshots complete',flush=True)
