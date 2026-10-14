from native_control import *
execute(D+'sable name get @e');execute(D+'sable name set @e create_compat_fixture');execute(D+'sable info @e');execute(D+'sable teleport @e 6 125 0');time.sleep(3);execute(D+'sable info @e');execute('save-all flush');print('native sublevel motion and saved state captured',flush=True)
