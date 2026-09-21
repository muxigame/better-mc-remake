# Better MC 客户端整合包发布域

这个目录只负责 **Minecraft 客户端内容**，不负责启动器程序，也不负责官网/API。

目录约定：

```text
pack/
├─ packspec.json                      # 版本化的发布规则，进入 Git
├─ source/                            # 本地干净整合包源，Git 忽略
│  └─ Better MC Remake [FORGE]/
├─ archive/                           # 原始 ZIP 备份，Git 忽略
└─ staging/                           # 构建输出，Git 忽略
   ├─ manifest.json
   └─ files/
```

生成发布清单与 staging：

```powershell
$env:PYTHONPATH='server'
..\.ops-venv\Scripts\python.exe -m app.cli build --link
```

发布到 OSS：

```powershell
.\pack\publish.ps1
```

发布脚本先请求 `mc.muxigame.com/api/v1/manifest` 获取当前真实 `manifestUrl`，再读取该 manifest 作为基线，只上传新增或 SHA-1 变化的文件，最后才更新 OSS manifest。OSS manifest 是唯一发布真相源；服务器只负责向客户端下发 `manifestUrl` 与 `filesBaseUrl`。

`Seed` 文件的语义是“每个服务端内容修订强制同步一次”：首次安装下载；玩家之后可以修改；只有当 manifest 中该文件 SHA-1 变化时才再次强制同步一次。
