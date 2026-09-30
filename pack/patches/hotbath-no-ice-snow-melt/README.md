# Hot Bath 4.1.0：关闭冰雪消融扫描

适用：Minecraft 1.21.1 / NeoForge，Hot Bath `1.21.1-4.1.0`。

该版本没有独立的冰雪消融配置开关。实际 JAR 中
`IceSnowMeltHandler.onLevelTick(LevelTickEvent.Post)` 每 100 tick 执行一次玩家周围的扫描，
并且不读取 `hotbath-common.toml`。因此不能靠添加不存在的配置项关闭它。

本补丁仅把这个方法的 Code 属性替换成 `RETURN`。保留事件注解、其余方法、
全部其他类、资源、模组 ID 和版本。泡澡、脏污度、含水方块与其他联动不在修改范围内。
保留原文件名以兼容现有整合包规则；修改后的 SHA-1 会使现有 Managed 文件在发布后更新。
JAR 内新增 `META-INF/muxi-hotbath-no-ice-snow-melt.json` 标识这是 muxi 定制补丁。

上游代码及许可证：https://github.com/crabsatellite/hotBath （GPL-3.0）。
这里提供完整、可复现的修改脚本，而不是把定制 JAR 冒充为未经修改的上游版本。

## 构建与验证

```powershell
python .\pack\patches\hotbath-no-ice-snow-melt\patch_hotbath.py <原版.jar> <输出.jar>
python .\pack\patches\hotbath-no-ice-snow-melt\patch_hotbath.py <原版.jar> <输出.jar> --verify-only
```

脚本只接受 SHA-256 为
`54e87d946936af0b2da7e1e5b0d8bd775f5ea7f3e3c3680870677dadd653c74c`
的原版文件；拒绝原地覆盖、覆盖已有产物和修改签名 JAR。
构建后逐项验证 ZIP CRC、所有原有条目以及目标方法之外的类字节没有变化。

## 安装与回滚

先在运行目录之外备份原 JAR，再在服务端停止时替换其 `mods` 中的同名文件。
客户端源包同步使用同一补丁；发布与重启是独立步骤，文件修改不代表运行实例已生效。
恢复 C2ME 时使用原有 NeoForge `0.4.0-alpha.0.120`，不同时升级其他模组或改动其参数。

回滚先停服、将 C2ME 移回停用目录，再用备份的 Hot Bath JAR 替换补丁版。
不要把备份 JAR 留在 `mods` 中，也不要在运行时改写已打开的 JAR。

关闭扫描只排除了这条调用路径，不能代替 C2ME 与完整整合包的启动、进入世界和新区块生成测试。

## 正在运行的服务端

服务端运行时不替换已占用的 JAR。本次按用户要求关闭了
`config/dirchunk-common.toml` 的 `[general] enabled`，并配置下次正常启动时：

1. 将定向区块加载（dirchunk）的 JAR 移至 `mods-disabled`，保留原文件。
2. 安装上一轮已验证的 Hot Bath 消融补丁，不重新制作补丁。
3. 恢复原有服务端 C2ME `0.4.0-alpha.0.120`。

`start.bat`、`run.bat`、`start-muxi.ps1` 在启动 Java 前调用待应用安装器。
它先检查世界和相关 JAR 没有被占用，校验固定哈希；任何步骤失败都回滚文件移动和替换并阻止启动。
成功后写入 `hotbath-c2me-v1/applied.json`，以后直接跳过，不覆盖未来手动更新。
安装器不读取密钥、不停服、不启动服务、不修改世界。完整整包运行兼容性仍需重启后确认。

## 客户端 C2ME 设置还原

历史提交 `0eecddd`（2026-09-24 18:40:06 +08:00，整合包 1.3.26）已将客户端 C2ME
设为 Optional、默认关闭。原规则、说明文字均已恢复，删除了本次误加的 `defaultOn: true`。
客户端 JAR 保留可选；本次没有发布 OSS，也没有修改玩家已保存的选择。

部署脚本通过独立文件夹测试：完整安装、重复运行、占用拒绝，以及安装后故障回滚。
