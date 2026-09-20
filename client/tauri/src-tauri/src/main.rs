#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

struct BackendState(Mutex<Option<CommandChild>>);

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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
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
            pick_java
        ])
        .run(tauri::generate_context!())
        .expect("启动 BatterMC5Remake 失败");
}
