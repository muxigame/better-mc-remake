# mc.muxigame.com 部署约定

## DNS

在阿里云 DNS 添加：

| 主机记录 | 类型 | 值 |
|---|---|---|
| `mc` | A | 香港生产服务器公网 IP |

阿里云邮件推送使用根域 `muxigame.com`。根域的 SPF、DKIM、DMARC、MX 记录已按 DirectMail 返回值配置；修改时必须以 DirectMail 控制台或 API 的当前值为准。

## 服务器布局

```text
/opt/battermc/
├── .env
└── server/
    ├── app/
    ├── web/
    ├── publish/manifest.json
    ├── launcher-release.json
    ├── requirements.txt
    ├── site.json
    ├── .venv/
    └── data/battermc.db
```

FastAPI 只监听 `127.0.0.1:8099`。当前香港生产机使用宝塔自带 Nginx，配置模板为 `nginx-muxigame.conf`；`Caddyfile` 仅保留给不运行现有 Web 服务的新机器。账号数据库目录需要持久化并每天备份。

生产 `.env` 至少配置：

```dotenv
BMC_PUBLIC_URL=https://mc.muxigame.com
BMC_DATABASE_PATH=server/data/battermc.db
BMC_AUTH_DEV_VERIFY=0
BMC_SMTP_HOST=smtpdm.aliyun.com
BMC_SMTP_PORT=465
BMC_SMTP_SSL=1
BMC_SMTP_USERNAME=no-reply@muxigame.com
BMC_SMTP_PASSWORD=
BMC_SMTP_FROM="Batter MC Remake <no-reply@muxigame.com>"
```

账号功能必须在 HTTPS 生效、SMTP 测试邮件投递成功后才开放注册。
