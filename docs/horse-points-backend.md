# 赛马赛事积分接线（2026-10-02）

审合赛马 owner 的 `horse-points-backend-review.patch`，沿用
`POST /api/internal/game/results`、统一 `player_profiles.points` 和现有原子账本。
本次不是马匹租售交易：金额余额、资产和交易接口均未改。

比赛服务端 `HorseScores` 按排名与用时计算成绩：冠军 10 分，其他完赛
名次 1–9 分。后端接收 `horse_racing`，要求 score 为整数 1–10、difficulty
为整数 1–5，win 必须恰好对应 score=10；按该受信服务端成绩入账，
不要求非冠军 win=true。浏览器不能提交 points、subject、rank 或权限字段。

接口继续要求现有服务端凭据与显式平台开关。UID 必须为合法范围内的
整数且唯一匹配已有网站资料；浏览器登录 cookie 本身不能写成绩。
比赛端既有 GameRuntime/GamePlatform 先验证当前连接绑定的 TrustedAccounts
UID，再发送结果。后端的服务端密钥授权不是逐请求玩家身份断言；
身份绑定依赖该既有受信发送链，未新增浏览器指定 UID 的写入口。

账本主键仍为 `(uid,game,session)`，session 必须为规范 UUID4。
同一结果重复/并发提交只入账一次；内容冲突返回 400，未知 UID 返回 409。
账本写入和积分增加处于同一 SQLite 事务，失败时共同回滚。
已有僵尸/爆发奖励策略保留，赛马不接受策略覆盖；飞行零积分规则原样保留。

验证：当前工作区实际 loopback HTTP API 与 SQLite 15 项测试、142 次请求
通过；不含飞行 dirty 的精确待提交源码也通过同一 15 项测试。
另有积分账本 7、飞行 3、平台接口/权限 4 项回归通过。合成数据库均已
删除，自有 HTTP 服务退出；未启动 MC，未接触生产积分、密钥或配置。
详见 `horse-points-backend-qa-20261002.json`。

本次采用部分暂存：提交仅包含赛马校验/入账、自己的测试及此文档，
飞行 owner 的共享文件改动留在工作区 dirty，未卷入提交。
本地接口通过不代表生产启用。部署仍需批准目标分支、匹配赛马/平台
产物、既有安全服务端凭据安装和平台开关，并验证真实目标。
不因赛事积分开启 LoginGate、OP trust 或马匹租售。未 push/tag/deploy。
