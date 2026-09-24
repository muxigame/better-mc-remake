; 安装器简体中文文案。取自 Tauri CLI 2.11.5 自带的 SimpChinese.nsh，只改了 deleteAppData 一条。
;
; customLanguageFiles 是整份替换，不是只覆盖其中几条，所以 27 条都得在；升级 Tauri CLI
; 之后要和它新带的英文文件（target/release/nsis/x64/English.nsh）对一遍键，缺了编译会报错。
;
; 存成不带 BOM 的 UTF-8：Tauri 拷进构建目录时自己会加一个，这里再带一个就成了双 BOM，
; makensis 在第 1 行报错。
;
; deleteAppData：Tauri 原文「删除应用程序数据」只删 WebView2 缓存；我们在卸载钩子里
; 让这个勾把游戏数据也删掉（见 installer-hooks.nsh），文字就得说清楚会删什么——存档也在里面。

LangString addOrReinstall ${LANG_SIMPCHINESE} "添加/重新安装组件"
LangString alreadyInstalled ${LANG_SIMPCHINESE} "已安装"
LangString alreadyInstalledLong ${LANG_SIMPCHINESE} "${PRODUCTNAME} ${VERSION} 已经安装了。选择你想要执行的操作后点击下一步以继续。"
LangString appRunning ${LANG_SIMPCHINESE} "{{product_name}} 正在运行！请关闭后再试。"
LangString appRunningOkKill ${LANG_SIMPCHINESE} "{{product_name}} 正在运行！$\n点击确定以终止运行。"
LangString chooseMaintenanceOption ${LANG_SIMPCHINESE} "选择要执行的维护操作。"
LangString choowHowToInstall ${LANG_SIMPCHINESE} "选择你想要安装 ${PRODUCTNAME} 的方式。"
LangString createDesktop ${LANG_SIMPCHINESE} "创建桌面快捷方式"
LangString dontUninstall ${LANG_SIMPCHINESE} "请勿卸载"
LangString dontUninstallDowngrade ${LANG_SIMPCHINESE} "请勿卸载（此安装程序禁止未卸载就进行版本降级的操作）"
LangString failedToKillApp ${LANG_SIMPCHINESE} "无法终止 {{product_name}}。请关闭后再试。"
LangString installingWebview2 ${LANG_SIMPCHINESE} "正在安装 WebView2..."
LangString newerVersionInstalled ${LANG_SIMPCHINESE} "有一个更新版本的 ${PRODUCTNAME} 已经安装了！不推荐你安装旧的版本。如果你真的想要安装这个旧的版本，推荐先卸载当前版本。选择你想要执行的操作后点击下一步以继续。"
LangString older ${LANG_SIMPCHINESE} "旧的"
LangString olderOrUnknownVersionInstalled ${LANG_SIMPCHINESE} "系统中已存在版本为 $R4 的 ${PRODUCTNAME}。推荐先卸载当前版本后再进行安装。选择你想要执行的操作后点击下一步以继续。"
LangString silentDowngrades ${LANG_SIMPCHINESE} "降级操作在此安装程序上已禁用，无法进行安静安装，请使用图形操作界面。$\n"
LangString unableToUninstall ${LANG_SIMPCHINESE} "无法卸载！"
LangString uninstallApp ${LANG_SIMPCHINESE} "卸载 ${PRODUCTNAME}"
LangString uninstallBeforeInstalling ${LANG_SIMPCHINESE} "安装前卸载"
LangString unknown ${LANG_SIMPCHINESE} "未知"
LangString webview2AbortError ${LANG_SIMPCHINESE} "无法安装 WebView2！没有它，此应用就无法运行。尝试重启安装程序。"
LangString webview2DownloadError ${LANG_SIMPCHINESE} "错误：无法下载 WebView2 - $0"
LangString webview2DownloadSuccess ${LANG_SIMPCHINESE} "WebView2 引导程序下载成功"
LangString webview2Downloading ${LANG_SIMPCHINESE} "正在下载 WebView2 引导程序..."
LangString webview2InstallError ${LANG_SIMPCHINESE} "错误：安装 WebView2 时失败，错误代码：$1"
LangString webview2InstallSuccess ${LANG_SIMPCHINESE} "成功安装 WebView2"
LangString deleteAppData ${LANG_SIMPCHINESE} "同时删除游戏数据（整合包、存档、设置）"
