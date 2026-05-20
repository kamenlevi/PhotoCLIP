use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use once_cell::sync::Lazy;
use tauri::{AppHandle, Emitter, Manager, RunEvent};

static SIDECAR: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));

#[derive(Clone, serde::Serialize)]
struct SidecarReady {
    port: u16,
}

struct AppState {
    port: Arc<Mutex<Option<u16>>>,
}

#[tauri::command]
fn sidecar_port(state: tauri::State<'_, AppState>) -> Option<u16> {
    *state.port.lock().unwrap()
}

/// Build the command that launches the Python sidecar.
///
/// Resolution order:
///   1. `PHOTOCLIP_SIDECAR` env var: full command, space-separated.
///   2. `PHOTOCLIP_PYTHON` env var as interpreter (default `python3`),
///      running `-m sidecar.server` from the resolved repo root.
fn build_sidecar_command(app: &AppHandle) -> Command {
    if let Ok(custom) = std::env::var("PHOTOCLIP_SIDECAR") {
        let mut parts = custom.split_whitespace();
        let prog = parts.next().expect("PHOTOCLIP_SIDECAR must not be empty");
        let mut cmd = Command::new(prog);
        cmd.args(parts);
        return cmd;
    }

    // Walk up from the resource dir (or cwd) to find a directory containing `sidecar/`.
    let start: PathBuf = app
        .path()
        .resource_dir()
        .ok()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));

    let mut repo_root = start.clone();
    for _ in 0..6 {
        if repo_root.join("sidecar").is_dir() {
            break;
        }
        if let Some(parent) = repo_root.parent() {
            repo_root = parent.to_path_buf();
        } else {
            break;
        }
    }

    let python = std::env::var("PHOTOCLIP_PYTHON").unwrap_or_else(|_| "python3".into());
    let mut cmd = Command::new(python);
    cmd.arg("-m").arg("sidecar.server");
    cmd.current_dir(repo_root);
    cmd
}

fn spawn_sidecar(app: &AppHandle, port_slot: Arc<Mutex<Option<u16>>>) -> anyhow::Result<()> {
    let mut cmd = build_sidecar_command(app);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    eprintln!("[photoclip] spawning sidecar: {:?}", cmd);
    let mut child = cmd.spawn()?;

    let stdout = child.stdout.take().expect("sidecar stdout missing");
    let stderr = child.stderr.take().expect("sidecar stderr missing");
    let app_clone = app.clone();

    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            eprintln!("[sidecar] {}", line);
            if let Some(rest) = line.strip_prefix("PHOTOCLIP_PORT=") {
                if let Ok(port) = rest.trim().parse::<u16>() {
                    *port_slot.lock().unwrap() = Some(port);
                    let _ = app_clone.emit("sidecar://ready", SidecarReady { port });
                    let script = format!("window.__PHOTOCLIP_PORT = {port};");
                    if let Some(win) = app_clone.get_webview_window("main") {
                        let _ = win.eval(&script);
                    }
                }
            }
        }
        eprintln!("[photoclip] sidecar stdout closed");
    });

    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[sidecar:err] {}", line);
        }
    });

    *SIDECAR.lock().unwrap() = Some(child);
    Ok(())
}

fn kill_sidecar() {
    if let Some(mut child) = SIDECAR.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = Arc::new(Mutex::new(None));
    let port_for_state = Arc::clone(&port);

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(AppState { port: port_for_state })
        .invoke_handler(tauri::generate_handler![sidecar_port])
        .setup(move |app| {
            let handle = app.handle().clone();
            if let Err(e) = spawn_sidecar(&handle, Arc::clone(&port)) {
                eprintln!("[photoclip] failed to spawn sidecar: {e:?}");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Tauri application")
        .run(|_app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                kill_sidecar();
            }
        });
}
