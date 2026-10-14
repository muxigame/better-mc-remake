from native_control import *
execute(D+'setblock 1 122 0 minecraft:air');execute(D+'setblock 1 123 0 minecraft:air');execute(D+'setblock 1 123 0 create:creative_motor[facing=down]{ScrollValue:64,Speed:64.0f}')
execute(D+'setblock 1 122 0 create:cogwheel[axis=y]')
execute('item replace entity 10000 hotbar.8 with minecraft:air')
cmd('host','select',slot=8);cmd('host','block-interact',x=4,y=120,z=0);time.sleep(4)
execute(D+'data get entity 10000 RootVehicle');execute(D+'execute as @e[type=interiors:big_seat] run data get entity @s Passengers')
for label,pos in [('slicerFollowup','0 122 0'),('basinFollowup','0 120 0'),('motorFollowup','1 123 0')]:execute(D+'data modify storage muxiqa:create '+label+' set from block '+pos)
execute('data get storage muxiqa:create');print('followup complete',flush=True)
