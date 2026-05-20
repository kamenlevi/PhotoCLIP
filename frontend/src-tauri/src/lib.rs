use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use once_cell::sync::Lazy;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, RunEvent, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

static SIDECAR: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));

#[derive(Clone, serde::Serialize)]
struct SidecarReady {
    port: u16,
}

struct AppState {
    port: Arc<Mutex<Option<u16>>>,
}

// ---------- commands ----------

#[tauri::command]
fn sidecar_port(state: tauri::State<'_, AppState>) -> Option<u16> {
    *state.port.lock().unwrap()
}

#[tauri::command]
fn show_main(app: AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

#[tauri::command]
fn hide_spotlight(app: AppHandle) {
    if let Some(win) = app.get_webview_window("spotlight") {
        let _ = win.hide();
    }
}

#[tauri::command]
fn resize_spotlight(app: AppHandle, height: f64) {
    if let Some(win) = app.get_webview_window("spotlight") {
        let h = height.clamp(80.0, 720.0);
        let _ = win.set_size(LogicalSize::new(640.0, h));
    }
}

#[tauri::command]
fn frontmost_folder() -> Option<String> {
    frontmost_folder_impl()
}

// ---------- frontmost folder ----------

#[cfg(target_os = "macos")]
fn frontmost_folder_impl() -> Option<String> {
    // Ask Finder for the front window's folder. Returns empty on failure.
    let script = r#"
        try
            tell application "Finder"
                if (count of windows) is 0 then return ""
                return POSIX path of (target of front window as alias)
            end tell
        on error
            return ""
        end try
    "#;
    let out = Command::new("osascript").args(["-e", script]).output().ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

#[cfg(target_os = "linux")]
fn frontmost_folder_impl() -> Option<String> {
    // Wayland: querying the active window's PID needs compositor-specific
    // protocols that aren't exposed to regular apps. Return None gracefully.
    if std::env::var("WAYLAND_DISPLAY").is_ok()
        && std::env::var("XDG_SESSION_TYPE")
            .map(|s| s == "wayland")
            .unwrap_or(false)
    {
        return None;
    }

    // X11: use xdotool to find the focused window's PID, then read its cwd
    // via /proc. Only treat the result as a "folder" if the process is a
    // known file manager.
    let pid_out = Command::new("xdotool")
        .args(["getactivewindow", "getwindowpid"])
        .output()
        .ok()?;
    let pid: u32 = String::from_utf8_lossy(&pid_out.stdout)
        .trim()
        .parse()
        .ok()?;

    let comm = std::fs::read_to_string(format!("/proc/{pid}/comm"))
        .ok()?
        .trim()
        .to_string();

    const FILE_MANAGERS: &[&str] = &[
        "nautilus",
        "nemo",
        "caja",
        "dolphin",
        "thunar",
        "pcmanfm",
        "krusader",
        "files",
    ];
    if !FILE_MANAGERS.iter().any(|fm| comm.eq_ignore_ascii_case(fm)) {
        return None;
    }

    // /proc/<pid>/cwd is good enough for terminals; many file managers
    // chdir into the displayed folder too. When they don't, this still
    // returns *a* sensible folder.
    let cwd = std::fs::read_link(format!("/proc/{pid}/cwd")).ok()?;
    Some(cwd.to_string_lossy().to_string())
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn frontmost_folder_impl() -> Option<String> {
    None
}

// ---------- sidecar lifecycle ----------

fn build_sidecar_command(app: &AppHandle) -> Command {
    if let Ok(custom) = std::env::var("PHOTOCLIP_SIDECAR") {
        let mut parts = custom.split_whitespace();
        let prog = parts.next().expect("PHOTOCLIP_SIDECAR must not be empty");
        let mut cmd = Command::new(prog);
        cmd.args(parts);
        return cmd;
    }

    let start: PathBuf = app
        .path()
        .resource_dir()
        .ok()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));

    let mut repo_root = start.clone();
    for _ in 0..8 {
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
                    for label in ["main", "spotlight"] {
                        if let Some(win) = app_clone.get_webview_window(label) {
                            let _ = win.eval(&script);
                        }
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

// ---------- windows ----------

fn create_spotlight(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, "spotlight", WebviewUrl::App("spotlight/".into()))
        .title("PhotoCLIP")
        .inner_size(640.0, 80.0)
        .resizable(false)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible(false)
        .center()
        .build()
}

fn toggle_spotlight(app: &AppHandle) {
    let win = match app.get_webview_window("spotlight") {
        Some(w) => w,
        None => match create_spotlight(app) {
            Ok(w) => w,
            Err(e) => {
                eprintln!("[photoclip] failed to create spotlight: {e:?}");
                return;
            }
        },
    };
    let visible = win.is_visible().unwrap_or(false);
    if visible {
        let _ = win.hide();
    } else {
        let _ = win.emit("spotlight://show", ());
        let _ = win.center();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

// ---------- tray ----------

fn build_tray(app: &AppHandle) -> anyhow::Result<()> {
    let show_item = MenuItem::with_id(app, "show", "Open PhotoCLIP", true, None::<&str>)?;
    let search_item = MenuItem::with_id(
        app,
        "search",
        "Quick search…",
        true,
        Some("CmdOrCtrl+Space"),
    )?;
    let settings_item = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let library_item = MenuItem::with_id(app, "library", "Library…", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit PhotoCLIP", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;

    let menu = Menu::with_items(
        app,
        &[
            &search_item,
            &show_item,
            &sep1,
            &library_item,
            &settings_item,
            &sep2,
            &quit_item,
        ],
    )?;

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("default window icon not configured"))?;

    TrayIconBuilder::with_id("main")
        .icon(icon)
        .icon_as_template(true) // macOS: lets the OS recolor for light/dark
        .tooltip("PhotoCLIP")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(handle_menu_event)
        .build(app)?;
    Ok(())
}

fn handle_menu_event(app: &AppHandle, event: MenuEvent) {
    match event.id().as_ref() {
        "show" => show_main_window(app),
        "search" => toggle_spotlight(app),
        "settings" => {
            show_main_window(app);
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval("window.location.hash = '#nav:settings';");
                let _ = app.emit("nav", "settings");
            }
        }
        "library" => {
            show_main_window(app);
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval("window.location.hash = '#nav:library';");
                let _ = app.emit("nav", "library");
            }
        }
        "quit" => {
            kill_sidecar();
            app.exit(0);
        }
        _ => {}
    }
}

// ---------- entry point ----------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = Arc::new(Mutex::new(None));
    let port_for_state = Arc::clone(&port);

    // CmdOrCtrl+Space — Cmd on macOS (Modifiers::META), Ctrl elsewhere.
    // macOS users will want to free this from system Spotlight in System
    // Settings → Keyboard → Shortcuts → Spotlight.
    #[cfg(target_os = "macos")]
    let primary_mod = Modifiers::META;
    #[cfg(not(target_os = "macos"))]
    let primary_mod = Modifiers::CONTROL;
    let shortcut = Shortcut::new(Some(primary_mod), Code::Space);
    let trigger = shortcut.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, sc, event| {
                    if event.state() == ShortcutState::Pressed && sc == &trigger {
                        toggle_spotlight(app);
                    }
                })
                .build(),
        )
        .manage(AppState { port: port_for_state })
        .invoke_handler(tauri::generate_handler![
            sidecar_port,
            show_main,
            hide_spotlight,
            resize_spotlight,
            frontmost_folder
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            if let Err(e) = spawn_sidecar(&handle, Arc::clone(&port)) {
                eprintln!("[photoclip] failed to spawn sidecar: {e:?}");
            }

            // Tray icon — lets the app live without a window.
            if let Err(e) = build_tray(&handle) {
                eprintln!("[photoclip] failed to build tray: {e:?}");
            }

            // Pre-create the spotlight window so the first Ctrl+Space is
            // instant; it stays hidden until the user triggers it.
            if let Err(e) = create_spotlight(&handle) {
                eprintln!("[photoclip] failed to create spotlight: {e:?}");
            }

            // Try registering the global hotkey. Some Linux compositors
            // refuse — fail soft so the rest of the app keeps working.
            if let Err(e) = handle.global_shortcut().register(shortcut.clone()) {
                eprintln!(
                    "[photoclip] global shortcut registration failed: {e:?} \
                     (use the tray icon to launch the spotlight)"
                );
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide the spotlight on focus loss or ESC — feels native.
            if window.label() == "spotlight" {
                if let tauri::WindowEvent::Focused(false) = event {
                    let _ = window.hide();
                }
            }
            // Hitting the close button on the main window just hides it
            // — the tray icon keeps the app alive.
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Tauri application")
        .run(|_app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                kill_sidecar();
            }
        });
}
