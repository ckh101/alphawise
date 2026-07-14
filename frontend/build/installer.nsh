; ============================================================================
; NSIS 自定义脚本 - 安装/卸载前关闭运行中的应用
;
; 作用:
;   - 安装/卸载前杀掉运行中的"灵智投研助手"，避免 exe 文件占用导致失败
;
; 范围 (职责分离，不误杀):
;   - 安装器只杀应用 GUI 进程 (灵智投研助手.exe / electron.exe)
;   - Python Worker 的清理 (端口 9999 / PID 文件) 由应用自身的 pythonWorker.js
;     _cleanupOrphanWorkers 负责 —— 应用未运行时不会有残留 worker，无需安装器代劳
;
; 注意: 不在 NSIS 里内联复杂的 PowerShell 命令 (PowerShell 的自动变量符号会被
;       NSIS 当作变量前缀解析，导致 warning 被 electron-builder 当成 error)。
; ============================================================================

!macro _CloseApp
  ; 杀应用自身进程 (electron-builder 产出的 exe 名 = productName)
  nsExec::ExecToLog 'taskkill /F /IM "灵智投研助手.exe"'
  Pop $0
  ; 开发残留的 electron.exe (构建机上可能有)
  nsExec::ExecToLog 'taskkill /F /IM "electron.exe"'
  Pop $0
  ; 给进程一点时间退出，释放文件句柄
  Sleep 1000
!macroend

; ----------------------------------------------------------------------------
; 数据库备份路径（卸载/安装两个独立进程间传递数据的固定位置）
; 用 $LOCALAPPDATA（用户级，卸载不删，且不在 $INSTDIR 内）。
; 目录名用英文（alphawise-db-bak），避免 NSIS 中文路径编码问题。
; ----------------------------------------------------------------------------
!define DB_DATA_REL "resources\backend\data"
!define DB_BACKUP_DIR "$LOCALAPPDATA\alphawise-db-bak"
; 安装日志（事后排查备份/还原是否执行）
!define DB_INSTALL_LOG "$LOCALAPPDATA\alphawise-install.log"

; 写一行日志到 DB_INSTALL_LOG（追加模式）
; 注意用 $R0 寄存器，避免和调用方（用 $0 存 xcopy 退出码）冲突
!macro _DbLog line
  FileOpen $R0 "${DB_INSTALL_LOG}" a
  ${If} $R0 != ""
    FileSeek $R0 0 END
    FileWrite $R0 "${line}$\r$\n"
    FileClose $R0
  ${EndIf}
!macroend

; ----------------------------------------------------------------------------
; 卸载器初始化（un.onInit 里，删除文件之前，最早期的钩子）
; 作用：把 data 目录备份到 $INSTDIR 外，避免 RMDir /r $INSTDIR 删掉用户数据。
; 覆盖安装和主动卸载都会经过这里；备份目录在下次安装时还原后清理。
; ----------------------------------------------------------------------------
!macro customUnInit
  !insertmacro "_DbLog" "=== customUnInit: INSTDIR=$INSTDIR ==="
  ; 备份前先杀应用 + Python Worker，释放 db 文件句柄
  ; （customUnInstall 里的 _CloseApp 在 RMDir 之后才跑，来不及，这里自己杀）
  nsExec::ExecToLog 'taskkill /F /IM "灵智投研助手.exe"'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /IM "pythonw.exe"'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /IM "python.exe"'
  Pop $0
  Sleep 1000

  ${If} ${FileExists} "$INSTDIR\${DB_DATA_REL}\*.*"
    !insertmacro "_DbLog" "backup source exists: $INSTDIR\${DB_DATA_REL}"
    DetailPrint "Backing up database to ${DB_BACKUP_DIR} ..."
    ; 先清掉可能残留的旧备份，避免上次文件混入
    RMDir /r "${DB_BACKUP_DIR}"
    nsExec::ExecToLog 'xcopy "$INSTDIR\${DB_DATA_REL}" "${DB_BACKUP_DIR}" /E /I /Y /Q'
    Pop $0
    !insertmacro "_DbLog" "xcopy backup exit code: $0"
  ${Else}
    !insertmacro "_DbLog" "no data dir to backup (fresh install)"
  ${EndIf}
!macroend

; ----------------------------------------------------------------------------
; 安装前（文件释放后执行）
; ----------------------------------------------------------------------------
!macro customInstall
  DetailPrint "正在关闭运行中的应用..."
  !insertmacro _CloseApp

  !insertmacro "_DbLog" "=== customInstall: INSTDIR=$INSTDIR ==="
  ; 若卸载阶段有备份，还原回新安装目录的 data 位置，然后清理备份
  ${If} ${FileExists} "${DB_BACKUP_DIR}\*.*"
    !insertmacro "_DbLog" "backup found, restoring to $INSTDIR\${DB_DATA_REL}"
    DetailPrint "Restoring database from backup ..."
    CreateDirectory "$INSTDIR\${DB_DATA_REL}"
    nsExec::ExecToLog 'xcopy "${DB_BACKUP_DIR}" "$INSTDIR\${DB_DATA_REL}" /E /I /Y /Q'
    Pop $0
    !insertmacro "_DbLog" "xcopy restore exit code: $0"
    RMDir /r "${DB_BACKUP_DIR}"
    !insertmacro "_DbLog" "backup cleaned"
  ${Else}
    !insertmacro "_DbLog" "no backup found, skip restore"
  ${EndIf}
!macroend

; ----------------------------------------------------------------------------
; 卸载前（关闭应用）
; ----------------------------------------------------------------------------
!macro customUnInstall
  DetailPrint "正在关闭应用以便卸载..."
  !insertmacro _CloseApp
!macroend
