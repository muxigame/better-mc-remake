; 安装前先卸掉改名之前的旧版本。
;
; 安装目录和卸载注册表键都是从 productName 推出来的。改名成 muxi-mc 之后，新安装包
; 会去 Uninstall\muxi-mc 找旧安装，那儿当然是空的，于是它认不出机器上已经装过：
;
;   · 装到 Program Files\muxi-mc，旧的 Program Files\BatterMC5Remake 原地不动
;   · 「程序和功能」里出现两条
;   · 旧快捷方式仍指向旧 exe，玩家点开还是旧版本 → 又提示更新 → 装到新目录
;     → 再点旧快捷方式……绕不出去
;
; 所以这里主动把旧的卸掉。调用形式照抄 Tauri 自己卸载同名旧版本那段（PageLeaveReinstall
; 里的 reinst_uninstall），不自己发明。
;
; 玩家数据不会丢，这点查过生成的卸载器：它只 Delete 自己那几个文件（主程序、sidecar、
; msquic.dll、uninstall.exe）和快捷方式，收尾用的是 RMDir 而不是 RMDir /r ——
; 目录非空就留着。便携式安装（游戏目录就在安装目录里面）因此也是安全的。
; 而删除应用数据那段由 $DeleteAppDataCheckboxState 把守，静默模式下确认页不显示、
; 该变量不会被赋值，条件不成立；何况它删的是 $LOCALAPPDATA\cn.battermc.launcher
; 这个 WebView2 缓存，不是玩家数据所在的 %LOCALAPPDATA%\BatterMC5Remake。
;
; 旧版本卸干净之后这个宏就是空转，重复执行无害。

; 注意：Tauri 是在 !define MANUFACTURER 之前 !include 本文件的，所以文件作用域这里
; 拿不到 ${MANUFACTURER}。凡是用到它的地方一律写进宏体——宏在安装段展开，那时已经
; 定义好了。写在外面不报错，只会把 ${MANUFACTURER} 当字面量，拼出一个永远匹配不上
; 的注册表路径，然后静默失效。
!define LEGACY_PRODUCT "BatterMC5Remake"
!define LEGACY_UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${LEGACY_PRODUCT}"

!macro NSIS_HOOK_PREINSTALL
  Push $R7
  Push $R8
  Push $R9

  ; 「当前用户」和「所有用户」两种安装模式写在不同的 hive，都找一遍。
  ; 装目录取自 MANUPRODUCTKEY 的默认值——卸载键里的 InstallLocation 是带引号的，
  ; 拼到 _?= 后面会坏掉。
  ReadRegStr $R7 HKCU "${LEGACY_UNINSTKEY}" "UninstallString"
  ReadRegStr $R8 HKCU "Software\${MANUFACTURER}\${LEGACY_PRODUCT}" ""
  ${If} $R7 == ""
    ReadRegStr $R7 HKLM "${LEGACY_UNINSTKEY}" "UninstallString"
    ReadRegStr $R8 HKLM "Software\${MANUFACTURER}\${LEGACY_PRODUCT}" ""
  ${EndIf}

  ${If} $R7 != ""
    DetailPrint "正在卸载旧版本 ${LEGACY_PRODUCT}"
    StrCpy $R7 "$R7 /S"
    ${If} $R8 != ""
      ; _?= 让卸载器就地同步执行。不加的话它会把自己拷到临时目录再立刻返回，
      ; ExecWait 等不到结果，新旧两次安装会抢同一批文件。
      StrCpy $R7 "$R7 _?=$R8"
    ${EndIf}
    ExecWait '$R7' $R9
    ${If} $R8 != ""
      ; 用了 _?= 之后卸载器不会自删，这里收尾。RMDir 不带 /r：
      ; 目录里还有玩家的东西就让它留着。
      Delete "$R8\uninstall.exe"
      RMDir "$R8"
    ${EndIf}
    DetailPrint "旧版本卸载返回码 $R9"
  ${EndIf}

  Pop $R9
  Pop $R8
  Pop $R7
!macroend
