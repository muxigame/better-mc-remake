# Vanillin / Flywheel 嵌套依赖修复

Vanillin 1.1.3 内嵌 Flywheel 1.0.4，Create 6.0.10 内嵌 Flywheel 1.0.6。两边的
jar-in-jar Maven 标识不同，NeoForge 会发现两份同 mod id 的 Flywheel，并可能选中旧版，
导致 Sable 2.0.5 和 Create Aeronautics 1.3.2 在客户端加载阶段拒绝启动。

`patch_vanillin.py --apply` 只移除 Vanillin 的旧内嵌 JAR 及其 jarjar 元数据；Vanillin
自己的代码和签名外内容保持不变，运行时统一使用 Create 已经携带的 Flywheel 1.0.6。
脚本钉死原始 Vanillin SHA-256，并验证 Create 确实内嵌 1.0.6 后才允许修改。
