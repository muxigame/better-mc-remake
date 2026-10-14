from native_control import *
execute(D+'gamerule randomTickSpeed 256');time.sleep(15);execute(D+'execute store success storage muxiqa:create hydratedAccelerated byte 1 if block 6 119 0 minecraft:farmland[moisture=7]');execute('data get storage muxiqa:create hydratedAccelerated');execute(D+'gamerule randomTickSpeed 3')
execute(D+'sable assemble area 6 121 0 9 123 1');time.sleep(4);execute(D+'sable name get @a');execute(D+'sable name set @a create_compat_fixture')
execute(D+'execute as @e[type=create:stationary_contraption] run data get entity @s');execute('save-all flush');print('native Sable assembly attempted; inspect actual receipts before claiming',flush=True)
