# Batter MC Remake 官网与 API

FastAPI 后端 + 无构建步骤的 HTML/CSS/JS 官网。Python 提供官网、玩家账号和管理 API；客户端安装包与整合包文件直接从阿里云 OSS 下载。

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
- `/account.html`：注册、登录和账号中心
- `/admin.html`：嵌入官网的管理后台（仅管理员）
- `/api/v1/auth/register`：邮箱注册
- `/api/v1/auth/verify`：邮箱验证
- `/api/v1/auth/login`：官网登录，使用 HttpOnly 会话 Cookie
- `/api/v1/auth/launcher-login`：客户端登录，返回独立会话令牌
- `/api/v1/auth/me`：读取当前账号；客户端在下载和启动前都会校验
- `/api/v1/admin/users`：管理员读取账号列表
- `/healthz`：健康检查

## 账号与邮件

复制根目录 `.env.example` 中的变量到根目录 `.env`。生产环境至少要配置：

- `BMC_PUBLIC_URL`：HTTPS 官网地址
- `BMC_DATABASE_PATH`：SQLite 数据文件；生产部署必须持久化和备份
- `BMC_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM`：事务邮件 SMTP

密码使用 Argon2id 哈希；验证令牌和登录令牌在数据库里只保存 SHA-256 摘要。公网 HTTP 下客户端会拒绝提交账号密码，因此账号功能上线前必须先配置域名、证书和 HTTPS 反向代理。

注册并验证第一个账号后，在服务器项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.cli promote-admin you@example.com
```

推荐使用阿里云邮件推送作为验证码邮件出口，不在游戏/官网机器上自建完整邮箱。若以后确实需要收发企业邮箱，再为独立机器部署 Mailcow；它要求正确 PTR、开放 25/465/587 等端口，并建议至少 6 GiB RAM，和游戏服混部维护成本较高。

## 发布版本记录

`artifacts/client/launcher-release.json` 是最新本地构建；`server/launcher-release.json` 是已上传 OSS 的线上版本。只有 `scripts/publish-oss.ps1` 完整成功后才会推进线上记录，避免官网宣布一个尚未发布的安装包。
