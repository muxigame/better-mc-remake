; 卸载钩子（installer-hooks.nsh 里的 NSIS_HOOK_POSTUNINSTALL）的单独测试。
;
; 不用真的安装、卸载：真卸载会碰到机器上已经装着的启动器和玩家数据。这里把钩子原样
; !include 进来，旧版本注册表键、兜底数据目录、安装目录都换成测试专用的，按命令行
; 给的场景跑一遍宏。编译、布置目录、检查结果都在 installer-hooks-test.ps1 里。
Unicode true
!include LogicLib.nsh
!include FileFunc.nsh

!include "${HOOKS}"

Name "bmc-installer-hooks-test"
OutFile "${OUTFILE}"
RequestExecutionLevel user
SilentInstall silent

; 这两个变量在 Tauri 的卸载器里由确认页和 /UPDATE 参数赋值，钩子直接读它们
Var DeleteAppDataCheckboxState
Var UpdateMode

Section
  ${GetOptions} $CMDLINE "/DEL=" $DeleteAppDataCheckboxState
  ${GetOptions} $CMDLINE "/UPD=" $UpdateMode
  ${GetOptions} $CMDLINE "/INST=" $INSTDIR
  !insertmacro NSIS_HOOK_POSTUNINSTALL
SectionEnd
