// AutoGamer — Tauri backend (multi-game).
//
// The app knows games only through `games/registry.json`:
// each entry maps a game id -> engine dir + sidecar/replay modules.
// The backend spawns the selected game's engine as a sidecar
// (`python3 -m <sidecar module>`, cwd = the game dir) and forwards
// its JSON-lines events to the frontend as `bot-event`.
// Game ids are always resolved through the registry, never taken
// verbatim from the frontend, so a crafted id cannot escape the
// resource dir (no `..` traversal).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// Per-game settings. The fields below are the Hearthstone engine's;
/// future games may use a subset (unknown keys are ignored by the engine).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GameConfig {
    log_path: String,
    cards_json: String,
    overrides_json: String,
    dry_run: bool,
    delay_min: f64,
    delay_max: f64,
    beam_width: u32,
}

impl Default for GameConfig {
    fn default() -> Self {
        Self {
            log_path: default_log_path(),
            cards_json: String::new(),
            overrides_json: String::new(),
            dry_run: true,
            delay_min: 0.4,
            delay_max: 1.2,
            beam_width: 8,
        }
    }
}

/// Whole-app config: which game is selected + one settings block per game.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppConfig {
    game: String,
    games: HashMap<String, GameConfig>,
}

impl Default for AppConfig {
    fn default() -> Self {
        let mut games = HashMap::new();
        games.insert("hearthstone".to_string(), GameConfig::default());
        Self {
            game: "hearthstone".to_string(),
            games,
        }
    }
}

impl AppConfig {
    fn active(&self) -> GameConfig {
        self.games.get(&self.game).cloned().unwrap_or_default()
    }
}

/// One entry of `games/registry.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct GameInfo {
    id: String,
    name: String,
    dir: String,
    sidecar: String,
    replay: String,
    description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Registry {
    games: Vec<GameInfo>,
}

fn default_log_path() -> String {
    #[cfg(target_os = "windows")]
    {
        r"C:\Program Files (x86)\Hearthstone\Logs\Power.log".to_string()
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_default();
        format!("{home}/Library/Logs/Blizzard/Hearthstone/Power.log")
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        String::new()
    }
}

struct AppState {
    config: Mutex<AppConfig>,
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
}

fn config_file_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("config.json"))
}

fn load_saved_config(app: &AppHandle) -> AppConfig {
    let mut cfg = AppConfig::default();
    if let Some(path) = config_file_path(app) {
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(saved) = serde_json::from_str::<AppConfig>(&text) {
                cfg = saved;
            }
        }
    }
    cfg
}

fn resource_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .map_err(|e| format!("resource dir unavailable: {e}"))
}

/// Load the game registry. Falls back to the built-in Hearthstone entry
/// when the file is missing (dev convenience: running from source).
fn load_registry(app: &AppHandle) -> Vec<GameInfo> {
    let fallback = vec![GameInfo {
        id: "hearthstone".to_string(),
        name: "炉石传说".to_string(),
        dir: "games/hearthstone".to_string(),
        sidecar: "hsbot.sidecar".to_string(),
        replay: "hsbot.replay".to_string(),
        description: "狂野 · 圣骑士机械套牌「号角」".to_string(),
    }];
    let path = match resource_dir(app) {
        Ok(dir) => dir.join("games").join("registry.json"),
        Err(_) => return fallback,
    };
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(_) => return fallback,
    };
    serde_json::from_str::<Registry>(&text)
        .map(|r| r.games)
        .unwrap_or(fallback)
}

/// Resolve a game id through the registry. Rejects ids not in the
/// registry and dirs containing `..`.
fn game_info(app: &AppHandle, game_id: &str) -> Result<GameInfo, String> {
    let info = load_registry(app)
        .into_iter()
        .find(|g| g.id == game_id)
        .ok_or_else(|| format!("unknown game: {game_id}"))?;
    if info.dir.contains("..") {
        return Err(format!("unsafe game dir for {game_id}"));
    }
    Ok(info)
}

fn engine_cmd(app: &AppHandle, info: &GameInfo, module: &str, extra_args: &[String]) -> Result<Command, String> {
    // Sidecars run with cwd = the game dir holding the engine package,
    // e.g. <resource>/games/hearthstone for the Hearthstone engine.
    let dir = resource_dir(app)?.join(&info.dir);
    let mut cmd = Command::new("python3");
    cmd.arg("-m")
        .arg(module)
        .args(extra_args)
        .current_dir(&dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    Ok(cmd)
}

fn write_stdin(state: &State<AppState>, line: &str) -> Result<(), String> {
    let mut guard = state.stdin.lock().map_err(|e| format!("stdin lock: {e}"))?;
    let stdin = guard.as_mut().ok_or("bot is not running")?;
    stdin
        .write_all(format!("{line}\n").as_bytes())
        .map_err(|e| format!("write to sidecar: {e}"))?;
    stdin.flush().map_err(|e| format!("flush sidecar stdin: {e}"))?;
    Ok(())
}

#[tauri::command]
fn list_games(app: AppHandle) -> Vec<GameInfo> {
    load_registry(&app)
}

#[tauri::command]
fn get_config(state: State<AppState>) -> AppConfig {
    state.config.lock().map(|c| c.clone()).unwrap_or_default()
}

#[tauri::command]
fn set_config(app: AppHandle, state: State<AppState>, config: AppConfig) -> Result<(), String> {
    if let Some(path) = config_file_path(&app) {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let text = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())?;
        std::fs::write(&path, text).map_err(|e| e.to_string())?;
    }
    {
        let mut guard = state.config.lock().map_err(|e| format!("config lock: {e}"))?;
        *guard = config.clone();
    }
    // Push the ACTIVE game's settings to a running sidecar.
    let active = config.active();
    let payload = serde_json::json!({"type": "config", "config": active});
    let _ = write_stdin(&state, &payload.to_string());
    Ok(())
}

#[tauri::command]
fn get_status(state: State<AppState>) -> bool {
    state.child.lock().map(|c| c.is_some()).unwrap_or(false)
}

#[tauri::command]
fn start_bot(app: AppHandle, state: State<AppState>) -> Result<String, String> {
    {
        let guard = state.child.lock().map_err(|e| format!("child lock: {e}"))?;
        if guard.is_some() {
            return Err("bot is already running".to_string());
        }
    }
    let config = state.config.lock().map(|c| c.clone()).unwrap_or_default();
    let info = game_info(&app, &config.game)?;
    let mut cmd = engine_cmd(&app, &info, &info.sidecar, &[])?;
    let mut child = cmd.spawn().map_err(|e| format!("spawn sidecar: {e}"))?;
    let stdout = child.stdout.take().ok_or("sidecar has no stdout")?;
    let stdin = child.stdin.take().ok_or("sidecar has no stdin")?;
    {
        let mut guard = state.stdin.lock().map_err(|e| format!("stdin lock: {e}"))?;
        *guard = Some(stdin);
    }
    // Forward sidecar stdout lines to the frontend as bot-event.
    let app_handle = app.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    let text = text.trim().to_string();
                    if text.is_empty() {
                        continue;
                    }
                    let payload: serde_json::Value =
                        serde_json::from_str(&text).unwrap_or_else(|_| {
                            serde_json::json!({"type": "error", "message": text})
                        });
                    let _ = app_handle.emit("bot-event", payload);
                }
                Err(_) => break,
            }
        }
        let _ = app_handle.emit(
            "bot-event",
            serde_json::json!({"type": "stopped", "reason": "sidecar exited"}),
        );
    });
    {
        let mut guard = state.child.lock().map_err(|e| format!("child lock: {e}"))?;
        *guard = Some(child);
    }
    let active = config.active();
    let start_payload = serde_json::json!({
        "type": "start",
        "config": {
            "game": config.game,
            "log_path": active.log_path,
            "dry_run": active.dry_run,
            "delay_min": active.delay_min,
            "delay_max": active.delay_max,
            "cards_json": active.cards_json,
            "overrides_json": active.overrides_json,
            "beam_width": active.beam_width,
        }
    });
    write_stdin(&state, &start_payload.to_string())?;
    Ok(format!("bot started ({})", config.game))
}

#[tauri::command]
fn stop_bot(state: State<AppState>) -> Result<(), String> {
    let _ = write_stdin(&state, r#"{"type":"stop"}"#);
    let mut child_opt = {
        let mut guard = state.child.lock().map_err(|e| format!("child lock: {e}"))?;
        guard.take()
    };
    if let Some(mut child) = child_opt.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    let mut stdin_guard = state.stdin.lock().map_err(|e| format!("stdin lock: {e}"))?;
    *stdin_guard = None;
    Ok(())
}

#[tauri::command]
fn send_bot_command(state: State<AppState>, command: serde_json::Value) -> Result<(), String> {
    write_stdin(&state, &command.to_string())
}

#[tauri::command]
fn replay_log(app: AppHandle, state: State<AppState>, path: String, game: Option<String>) -> Result<serde_json::Value, String> {
    let config = state.config.lock().map(|c| c.clone()).unwrap_or_default();
    let game_id = game.unwrap_or(config.game);
    let info = game_info(&app, &game_id)?;
    let active = config.games.get(&game_id).cloned().unwrap_or_default();
    let output = engine_cmd(&app, &info, &info.replay, &[
        "--log".to_string(),
        path,
        "--beam-width".to_string(),
        active.beam_width.to_string(),
    ])?
    .output()
    .map_err(|e| format!("spawn replay: {e}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(format!("replay failed: {err}"));
    }
    let text = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(&text).map_err(|e| format!("parse replay output: {e}"))
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let cfg = load_saved_config(&app.handle());
            app.manage(AppState {
                config: Mutex::new(cfg),
                child: Mutex::new(None),
                stdin: Mutex::new(None),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            list_games,
            get_config,
            set_config,
            get_status,
            start_bot,
            stop_bot,
            send_bot_command,
            replay_log
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
