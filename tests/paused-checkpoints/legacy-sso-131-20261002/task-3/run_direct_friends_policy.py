from pathlib import Path
import subprocess,tempfile,os
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent/'task/dev-sync-20261002/muxi-terminal'
JDK=Path(r'C:\Users\ranzh\Documents\Codex\terminal-integration-task6-20261001\tools\jdk\jdk-21.0.12.1+1')
GSON=Path(r'C:\Users\ranzh\workspace\dev\muxigame\bmc5server\libraries\com\google\code\gson\gson\2.10.1\gson-2.10.1.jar')
with tempfile.TemporaryDirectory(prefix='131-native-friends-policy-') as raw:
 tmp=Path(raw);stub=tmp/'Minecraft.java';out=tmp/'classes';out.mkdir()
 stub.write_text('''package net.minecraft.client;
 public class Minecraft {public Object player,screen;public static Minecraft getInstance(){return new Minecraft();}public Object getConnection(){return null;}public void execute(Runnable task){task.run();}}
 ''',encoding='utf-8')
 sources=[ROOT/'src/main/java/net/muxigame/terminal/client'/n for n in ('TerminalFriendsPolicy.java','TerminalFriendsTransport.java')]
 sources.extend([ROOT/'tests/friends/FriendsPolicyTest.java',stub])
 args=['--release','21','-encoding','UTF-8','-proc:none','-cp',str(GSON),'-d',str(out),*map(str,sources)]
 file=tmp/'args.txt';file.write_text('\n'.join('"'+a.replace('\\','/')+'"' for a in args),encoding='utf-8')
 subprocess.run([str(JDK/'bin/javac.exe'),'@'+str(file)],check=True)
 subprocess.run([str(JDK/'bin/java.exe'),'-cp',str(out)+os.pathsep+str(GSON),'net.muxigame.terminal.client.FriendsPolicyTest'],check=True)
