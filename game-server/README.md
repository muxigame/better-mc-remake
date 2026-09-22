# 服务端功能已独立为 muxi Game Core

源码、构建和安装脚本的唯一位置是同级 `../muxi-game-core/` 仓库：

https://github.com/muxigame/muxi-game-core

真实 Minecraft 服务端位于同级 `../bmc5server/`，不再嵌套在 Better MC 仓库中。

```text
muxigame/
├─ better-mc-remake/      # 启动器、官网/API、客户端整合包发布
├─ muxi-game-core/        # 通用 NeoForge 服务端功能模组（源码）
├─ bmc5server/            # 真正的 MC 服务端包及世界（非源码）
└─ muxi-auth/             # 统一账户
```

昵称同步已由旧 `muxi-identity` 迁移到 Game Core 的可配置 `identity` 功能。
不要同时安装新旧两个 JAR。安装器会保留原凭据、备份并退役旧文件，不修改客户端或世界。

本次独立仓库不配置 CI；在 `muxi-game-core` 本地运行 `build.ps1 -Test` 和 `python install.py`。
