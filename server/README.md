# Batter MC Remake 官网与 API

FastAPI 后端 + 无构建步骤的 HTML/CSS/JS 官网。Python 提供官网、OIDC 登录接入和管理 API；统一账户、注册、邮箱验证与密码都由独立的 muxi 账户 服务负责。客户端安装包与整合包文件直接从阿里云 OSS 下载。

```powershell
py -3 -m venv .\server\.venv
.\server\.venv\Scripts\python.exe -m pip install -r .\server\requirements.txt
.\server\run-server.ps1 -Reload
```

生成整合包清单和本地发布目录：

```powershell
Push-Location .\server
.\.venv\Scripts\python.exe -m app.cli build --spec .\packspec.json --publish .\publish
Pop-Location
```

主要接口：

- `/`：官网
- `/api/v1/site`：官网展示数据
- `/api/v1/launcher/latest`：客户端版本与 OSS 地址
- `/api/v1/manifest`：客户端清单；文件 URL 已指向 OSS
- `/account.html`：统一账户入口和当前登录状态
- `/admin.html`：嵌入官网的管理后台（仅管理员）
- `/api/v1/auth/login`：跳转到 muxi 账户 的 Authorization Code + PKCE 登录
- `/api/v1/auth/register`：跳转到 muxi 账户 注册页
- `/api/v1/auth/callback`：OIDC 回调，成功后建立本站 HttpOnly Session
- `/api/v1/auth/me`：读取当前官网会话
- `/api/v1/admin/users`：管理员读取统一账户中心的账号列表
- `/healthz`：健康检查

## 统一账户

复制根目录 `.env.example` 中的变量到根目录 `.env`。生产环境至少要配置：

- `BMC_PUBLIC_URL`：HTTPS 官网地址
- `BMC_DATABASE_PATH`：本站 OIDC 登录状态与 Web Session 的 SQLite 文件
- `BMC_AUTH_ISSUER`：muxi 账户，例如 `https://account.muxigame.com`
- `BMC_AUTH_CLIENT_ID`：`better-mc-web`
- `BMC_AUTH_CLIENT_SECRET`：与 muxi 账户 中 confidential client 完全一致的高熵 Secret
- `BMC_AUTH_REDIRECT_URI`：`https://mc.muxigame.com/api/v1/auth/callback`

Better MC 官网不接收、验证或保存用户密码；注册、邮箱验证、Argon2id 密码哈希、OAuth Token 与账号角色都在 `muxi-auth` 仓库中维护。本站只保存自己的一次性 OAuth state/PKCE verifier 和登录后的站点 Session。

管理员提升也改在统一账户服务执行：

```powershell
python -m app.cli promote-admin you@example.com
```
上面的命令需要在 `muxi-auth` 项目中运行。

## 发布版本记录

`artifacts/client/launcher-release.json` 是最新本地构建。正式客户端二进制永久存放在
`bmc/client/releases/<version>/`，`bmc/client/latest/metadata.json` 是当前正式客户端版本的唯一正常真相源；
`client/publish.ps1` 创建新历史版本并推进 latest，`client/promote.ps1 -Version x.y.z` 可把已有版本晋升/回滚为 latest。
`server/launcher-release.json` 仅作为 OSS/latest 不可访问时的镜像内 fallback。
整合包内容由 `pack/publish.ps1` 发布，OSS manifest 是唯一发布真相源；`server` 只负责下发 manifest / files 地址，不负责上传或保存整合包 manifest。
