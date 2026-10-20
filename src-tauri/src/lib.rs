//! 智企 Agent 桌面壳（Tauri 2.x）
//!
//! 职责：
//! 1. 管理 Python sidecar 生命周期（启动 / 健康探测 / 崩溃自动重启 / 退出回收）。
//! 2. 渲染 React 前端，并把 API 基址注入前端（VITE_API_BASE 运行时读取）。
//! 3. 提供应用数据目录、系统信息等 Rust 能力给前端。
//!
//! 体积与启动策略：sidecar 以 PyInstaller **onedir** 冻结（见 backend/run.py），
//! 避免 onefile 每次启动解压到临时目录带来的 2–5 秒开销（PRD Gate G0）。

use std::net::TcpStream;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

/// sidecar 端口探测范围（与后端 _pick_port 一致）
const PORT_START: u16 = 8756;
const PORT_ATTEMPTS: u16 = 10;
const HEALTH_TIMEOUT: Duration = Duration::from_millis(400);
const RESTART_BACKOFF: Duration = Duration::from_secs(2);

#[derive(Serialize, Clone)]
pub struct SidecarInfo {
    pub port: u16,
    pub base_url: String,
    pub pid: Option<u32>,
    pub restarts: u32,
}

#[derive(Serialize)]
pub struct AppDirs {
    pub data: String,
    pub config: String,
    pub logs: String,
}

pub struct SidecarState {
    pub info: Mutex<Option<SidecarInfo>>,
    pub child: Mutex<Option<tauri::plugin_shell::process::CommandChild>>,
}

/// 端口是否已被占用
fn port_busy(port: u16) -> bool {
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().unwrap(),
        HEALTH_TIMEOUT,
    )
    .is_ok()
}

/// 找到 sidecar 实际监听的端口
fn detect_port() -> Option<u16> {
    for offset in 0..PORT_ATTEMPTS {
        let port = PORT_START + offset;
        if port_busy(port) {
            return Some(port);
        }
    }
    None
}

/// sidecar 可执行文件路径：开发态走 venv，打包态走资源目录
fn sidecar_command(app: &tauri::AppHandle) -> Option<(String, Vec<String>)> {
    // 打包态：PyInstaller onedir 产物
    if let Ok(dir) = app.path().resource_dir() {
        for candidate in [
            dir.join("ai-company-agent.exe"),
            dir.join("ai-company-agent"),
            dir.join("sidecar").join("ai-company-agent.exe"),
        ] {
            if candidate.exists() {
                return Some((candidate.to_string_lossy().to_string(), vec![]));
            }
        }
    }
    // 开发态：仓库 backend/.venv
    if let Ok(cwd) = std::env::current_dir() {
        let root = cwd.join("backend");
        let exe = if cfg!(windows) {
            root.join(".venv").join("Scripts").join("python.exe")
        } else {
            root.join(".venv").join("bin").join("python")
        };
        if exe.exists() {
            return Some((
                exe.to_string_lossy().to_string(),
                vec!["-m".into(), "app.main".into()],
            ));
        }
    }
    None
}

/// 启动 sidecar 并等待就绪；崩溃时自动重启（最多 3 次）
fn spawn_sidecar(app: &tauri::AppHandle) -> Result<(), String> {
    let (program, args) = sidecar_command(app).ok_or("未找到 sidecar 可执行文件")?;
    let state = app.state::<SidecarState>();

    let (mut rx, child) = tauri::plugin_shell::process::Command::new(program)
        .args(args)
        .spawn()
        .map_err(|e| format!("sidecar 启动失败：{e}"))?;

    // 后台读取 stdout/stderr，避免管道阻塞
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let s = String::from_utf8_lossy(&line);
                    println!("[sidecar] {}", s.trim_end());
                }
                CommandEvent::Stderr(line) => {
                    let s = String::from_utf8_lossy(&line);
                    eprintln!("[sidecar] {}", s.trim_end());
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[sidecar] 退出：{:?}", payload);
                    break;
                }
                _ => {}
            }
        }
    });

    if let Ok(mut c) = state.child.lock() {
        *c = Some(child);
    }
    Ok(())
}

/// 等待 sidecar 就绪（≤ 3 秒，PRD 启动性能指标）
fn wait_ready(timeout: Duration) -> Option<u16> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Some(p) = detect_port() {
            return Some(p);
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    None
}

/// Rust 能力：返回 sidecar 信息
#[tauri::command]
fn sidecar_info(state: State<SidecarState>) -> Option<SidecarInfo> {
    state.info.lock().ok().and_then(|g| g.clone())
}

/// Rust 能力：返回应用数据目录
#[tauri::command]
fn app_dirs(app: tauri::AppHandle) -> AppDirs {
    let data = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| std::path::PathBuf::from("."));
    AppDirs {
        data: data.to_string_lossy().to_string(),
        config: data.join("config").to_string_lossy().to_string(),
        logs: data.join("logs").to_string_lossy().to_string(),
    }
}

/// Rust 能力：应用版本
#[tauri::command]
fn app_version(app: tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .manage(SidecarState {
            info: Mutex::new(None),
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![sidecar_info, app_dirs, app_version])
        .setup(|app| {
            let handle = app.handle().clone();

            // 启动 sidecar 并等待就绪
            let mut restarts: u32 = 0;
            let mut started = false;
            for attempt in 0..3 {
                match spawn_sidecar(&handle) {
                    Ok(()) => {
                        if let Some(port) = wait_ready(Duration::from_secs(10)) {
                            let info = SidecarInfo {
                                port,
                                base_url: format!("http://127.0.0.1:{port}"),
                                pid: None,
                                restarts,
                            };
                            if let Some(state) = handle.try_state::<SidecarState>() {
                                if let Ok(mut g) = state.info.lock() {
                                    *g = Some(info);
                                }
                            }
                            started = true;
                            break;
                        }
                    }
                    Err(e) => eprintln!("[tauri] {}", e),
                }
                restarts += 1;
                std::thread::sleep(RESTART_BACKOFF);
            }
            if !started {
                eprintln!("[tauri] sidecar 未能在 3 次尝试内就绪，界面将提示错误");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // 退出前回收 sidecar 进程
                if let Some(state) = app.try_state::<SidecarState>() {
                    if let Ok(mut c) = state.child.lock() {
                        if let Some(child) = c.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
