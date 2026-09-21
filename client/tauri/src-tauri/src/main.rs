#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{atomic::{AtomicBool, Ordering}, Mutex};
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use tauri_plugin_updater::UpdaterExt;

struct BackendState(Mutex<Option<CommandChild>>);

static CLIENT_UPDATE_ACTIVE: AtomicBool = AtomicBool::new(false);
struct UpdateGuard;
impl Drop for UpdateGuard {
    fn drop(&mut self) { CLIENT_UPDATE_ACTIVE.store(false, Ordering::SeqCst); }
}

#[tauri::command]
fn send_message(state: tauri::State<'_, BackendState>, json: String) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|_| "sidecar 状态锁已损坏".to_string())?;
    let child = guard.as_mut().ok_or_else(|| "sidecar 没有运行".to_string())?;
    let mut bytes = json.into_bytes();
    bytes.push(b'\n');
    child.write(&bytes).map_err(|e| format!("写入 sidecar 失败：{e}"))
}

#[tauri::command]
fn minimize(window: tauri::WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

#[tauri::command]
fn close_app(window: tauri::WebviewWindow) -> Result<(), String> {
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
fn begin_drag(window: tauri::WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|e| e.to_string())
}

#[tauri::command]
fn restore_window(window: tauri::WebviewWindow) -> Result<(), String> {
    window.unminimize().map_err(|e| e.to_string())?;
    window.show().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}

#[tauri::command]
fn pick_java() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("选择 java.exe")
        .add_filter("Java 可执行文件", &["exe"])
        .pick_file()
        .map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
async fn check_client_update(app: tauri::AppHandle) -> Result<Option<serde_json::Value>, String> {
    let update = app.updater_builder().timeout(Duration::from_secs(15))
        .build().map_err(|e| format!("初始化更新器失败：{e}"))?
        .check().await.map_err(|e| format!("检查客户端更新失败：{e}"))?;
    Ok(update.map(|u| serde_json::json!({
        "version": u.version,
        "currentVersion": u.current_version,
        "notes": u.body,
        "size": u.raw_json.get("size").and_then(|v| v.as_u64()).unwrap_or(0),
        "mandatory": u.raw_json.get("mandatory").and_then(|v| v.as_bool()).unwrap_or(false),
        "minSupportedVersion": u.raw_json.get("minSupportedVersion"),
        "updateReason": u.raw_json.get("updateReason"),
    })))
}

#[tauri::command]
async fn install_client_update(app: tauri::AppHandle) -> Result<(), String> {
    if CLIENT_UPDATE_ACTIVE.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
        return Err("客户端更新已在进行中".into());
    }
    let _guard = UpdateGuard;
    let update = app
        .updater_builder().timeout(Duration::from_secs(300)).build()
        .map_err(|e| format!("初始化客户端更新器失败：{e}"))?
        .check()
        .await
        .map_err(|e| format!("检查客户端更新失败：{e}"))?
        .ok_or_else(|| "当前已经是最新客户端".to_string())?;

    let version = update.version.clone();
    let progress_app = app.clone();
    let finished_app = app.clone();
    let mut downloaded: u64 = 0;
    let mut last_progress = Instant::now() - Duration::from_secs(1);

    let _ = app.emit(
        "client-update-progress",
        serde_json::json!({"phase":"started","version":version}),
    );

    let bytes = update
        .download(
            move |chunk_length, content_length| {
                downloaded = downloaded.saturating_add(chunk_length as u64);
                if last_progress.elapsed() >= Duration::from_millis(100) || content_length == Some(downloaded) {
                    last_progress = Instant::now();
                    let _ = progress_app.emit(
                        "client-update-progress",
                        serde_json::json!({
                            "phase":"downloading",
                            "downloaded":downloaded,
                            "total":content_length,
                        }),
                    );
                }
            },
            move || {
                let _ = finished_app.emit(
                    "client-update-progress",
                    serde_json::json!({"phase":"verifying"}),
                );
            },
        )
        .await
        .map_err(|e| format!("更新包下载或签名校验失败：{e}"))?;

    // download() verifies the minisign signature before returning these bytes.
    // Never launch NSIS on a partial/unverified download.
    let _ = app.emit("client-update-progress", serde_json::json!({"phase":"installing"}));
    tauri::async_runtime::spawn_blocking(move || update.install(bytes))
        .await.map_err(|e| format!("无法启动客户端安装器：{e}"))?
        .map_err(|e| format!("无法启动客户端安装器：{e}"))?;

    // Windows 的 updater 在成功启动 NSIS 安装器后会自动退出当前应用。
    // 其它平台未来接入时再在这里显式 restart。
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let sidecar = app.shell().sidecar("battermc-backend")?;
            let (mut events, child) = sidecar.spawn()?;
            app.manage(BackendState(Mutex::new(Some(child))));

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            let text = String::from_utf8_lossy(&bytes);
                            for line in text.lines().filter(|line| !line.trim().is_empty()) {
                                let _ = handle.emit("backend-message", line.to_string());
                            }
                        }
                        CommandEvent::Stderr(bytes) => {
                            let text = String::from_utf8_lossy(&bytes).trim().to_string();
                            if !text.is_empty() {
                                let _ = handle.emit("backend-error", text);
                            }
                        }
                        CommandEvent::Terminated(payload) => {
                            let _ = handle.emit(
                                "backend-error",
                                format!("sidecar 已退出：{:?}", payload.code),
                            );
                        }
                        _ => {}
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            send_message,
            minimize,
            close_app,
            begin_drag,
            restore_window,
            pick_java,
            check_client_update,
            install_client_update
        ])
        .run(tauri::generate_context!())
        .expect("启动 BatterMC5Remake 失败");
}
