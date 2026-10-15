/* AutoGamer console — light theme, no build step. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const isTauri = typeof window.__TAURI__ !== "undefined";
const core = isTauri ? window.__TAURI__.core : null;

const state = {
  live: null,
  plan: [],
  mulligan: null,
  replay: [],
  replayIndex: 0,
  running: false,
  paused: false,
  config: null, // { game, games: { [id]: GameConfig } }
  games: [], // registry entries from list_games
};

async function invoke(cmd, args) {
  if (!core) throw new Error("不在 Tauri 环境中");
  return core.invoke(cmd, args);
}

function logEvent(text, kind = "info") {
  const ul = $("#eventLog");
  const li = document.createElement("li");
  li.className = kind;
  li.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
  ul.prepend(li);
  while (ul.children.length > 500) ul.removeChild(ul.lastChild);
}

function setStatus() {
  const pill = $("#statusPill");
  if (state.running) {
    pill.textContent = state.paused ? "已暂停" : "运行中";
    pill.className = "pill " + (state.paused ? "paused" : "running");
  } else {
    pill.textContent = "已停止";
    pill.className = "pill stopped";
  }
  $("#btnStart").disabled = state.running;
  $("#btnPause").disabled = !state.running || state.paused;
  $("#btnResume").disabled = !state.running || !state.paused;
  $("#btnStop").disabled = !state.running;
}

// --- board rendering -------------------------------------------------
function heroCard(player, label) {
  const hero = player.hero;
  const hp = hero ? hero.health : player.hero_health;
  const div = document.createElement("div");
  div.className = "hero-card";
  div.innerHTML = `<div class="hero-name">${label}</div>
    <div class="hero-hp">${hp} ❤</div>
    <div class="hero-meta">费用 ${player.mana}/${player.max_mana} · 牌库 ${player.deck_count}</div>`;
  return div;
}

function minionCard(m) {
  const div = document.createElement("div");
  div.className = "minion" + (m.taunt ? " taunt" : "") + (m.divine_shield ? " shield" : "");
  div.title = `${m.name} (${m.card_id || ""})`;
  div.innerHTML = `<div class="m-name">${m.name || "?"}</div>
    <div class="m-stats"><span class="atk">${m.attack}</span><span class="hp">${m.health}</span></div>
    <div class="m-tags">${m.taunt ? "嘲讽 " : ""}${m.divine_shield ? "圣盾" : ""}</div>`;
  return div;
}

function handCard(c) {
  const div = document.createElement("div");
  div.className = "hand-card";
  div.title = c.name;
  div.innerHTML = `<div class="h-cost">${c.cost}</div><div class="h-name">${c.name || "?"}</div>
    <div class="h-stats">${c.attack ?? ""}/${c.health ?? ""}</div>`;
  return div;
}

function renderState(snap, mount) {
  if (!snap) return;
  mount.innerHTML = "";
  const oppRow = document.createElement("div");
  oppRow.className = "row enemy";
  oppRow.appendChild(heroCard(snap.opp, "对手"));
  const oppBoard = document.createElement("div");
  oppBoard.className = "minions";
  snap.opp.board.forEach((m) => oppBoard.appendChild(minionCard(m)));
  if (snap.secrets && snap.secrets.length) {
    const s = document.createElement("div");
    s.className = "secret";
    s.textContent = `奥秘 × ${snap.secrets.length}`;
    oppBoard.appendChild(s);
  }
  oppRow.appendChild(oppBoard);

  const myRow = document.createElement("div");
  myRow.className = "row mine";
  myRow.appendChild(heroCard(snap.me, "我"));
  const myBoard = document.createElement("div");
  myBoard.className = "minions";
  snap.me.board.forEach((m) => myBoard.appendChild(minionCard(m)));
  myRow.appendChild(myBoard);

  const handRow = document.createElement("div");
  handRow.className = "hand";
  snap.me.hand.forEach((c) => handRow.appendChild(handCard(c)));

  mount.append(oppRow, myRow, handRow);
}

// --- bot events ------------------------------------------------------
function onBotEvent(ev) {
  if (!ev || typeof ev !== "object") return;
  switch (ev.type) {
    case "state":
      state.live = ev.state;
      $("#liveTurn").textContent = ev.state.turn;
      $("#liveStep").textContent = ev.state.step;
      $("#liveCurrent").textContent = ev.state.is_my_turn ? "我方" : "对手";
      renderState(ev.state, $("#liveBoard"));
      break;
    case "decision":
      state.plan = ev.actions || [];
      $("#planList").innerHTML = state.plan.length
        ? state.plan.map((a) => `<li>${a}</li>`).join("")
        : "<li>结束回合</li>";
      logEvent(`回合 ${ev.turn} 计划：${state.plan.join("；")}`, "decision");
      break;
    case "action":
      logEvent(`执行：${ev.action}`, "action");
      break;
    case "mulligan":
      state.mulligan = ev;
      $("#mulliganInfo").textContent =
        `手牌：${ev.hand.join("、")}；换掉 ${ev.replace.length} 张（位置 ${ev.replace.join(", ") || "无"}）`;
      logEvent(`换牌：换掉 ${ev.replace.length} 张`, "mulligan");
      break;
    case "started":
      state.running = true;
      setStatus();
      logEvent(`Bot 已启动${ev.config?.game ? `（${ev.config.game}）` : ""}`, "ok");
      break;
    case "stopped":
      state.running = false;
      state.paused = false;
      setStatus();
      logEvent("Bot 已停止" + (ev.reason ? `（${ev.reason}）` : ""), "warn");
      break;
    case "paused":
      state.paused = true;
      setStatus();
      logEvent("已暂停（只解析日志，不决策）", "warn");
      break;
    case "resumed":
      state.paused = false;
      setStatus();
      logEvent("已继续", "ok");
      break;
    case "error":
      logEvent(`错误：${ev.message}`, "error");
      break;
    default:
      break;
  }
}

// --- wiring ------------------------------------------------------------
function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
}

// --- multi-game config -------------------------------------------------
const DEFAULT_GAME_CONFIG = {
  log_path: "",
  cards_json: "",
  overrides_json: "",
  delay_min: 0.4,
  delay_max: 1.2,
  beam_width: 8,
  dry_run: true,
};

function activeGameConfig() {
  return state.config?.games?.[state.config.game] || { ...DEFAULT_GAME_CONFIG };
}

function gameInfo(id) {
  return state.games.find((g) => g.id === id);
}

function populateGameSelect() {
  const sel = $("#gameSelect");
  sel.innerHTML = state.games
    .map((g) => `<option value="${g.id}">${g.name}</option>`)
    .join("");
  sel.value = state.config?.game || "";
  const info = gameInfo(state.config?.game);
  $("#gameDesc").textContent = info?.description || "";
  $("#configGameHint").textContent = `正在配置：${info?.name || state.config?.game || "—"}`;
}

function populateConfigForm() {
  const cfg = activeGameConfig();
  const form = $("#configForm");
  form.logPath.value = cfg.log_path || "";
  form.cardsJson.value = cfg.cards_json || "";
  form.overridesJson.value = cfg.overrides_json || "";
  form.delayMin.value = cfg.delay_min;
  form.delayMax.value = cfg.delay_max;
  form.beamWidth.value = cfg.beam_width;
  form.dryRun.checked = cfg.dry_run;
  $("#dryRunQuick").checked = cfg.dry_run;
}

async function loadConfig() {
  if (!isTauri) return;
  try {
    const cfg = await invoke("get_config");
    state.config = cfg;
    try {
      state.games = await invoke("list_games");
    } catch { /* keep fallback */ }
    populateGameSelect();
    populateConfigForm();
  } catch (e) {
    logEvent(`读取配置失败：${e}`, "error");
  }
}

async function saveConfig(quiet = false) {
  const form = $("#configForm");
  const gameCfg = {
    log_path: form.logPath.value.trim(),
    cards_json: form.cardsJson.value.trim(),
    overrides_json: form.overridesJson.value.trim(),
    delay_min: parseFloat(form.delayMin.value) || 0.4,
    delay_max: parseFloat(form.delayMax.value) || 1.2,
    beam_width: parseInt(form.beamWidth.value, 10) || 8,
    dry_run: form.dryRun.checked,
  };
  if (!state.config) {
    state.config = { game: "hearthstone", games: {} };
  }
  state.config.games[state.config.game] = gameCfg;
  if (!isTauri) {
    if (!quiet) $("#configSaved").textContent = "演示模式：配置仅保存在页面（未写入磁盘）";
    return;
  }
  await invoke("set_config", { config: state.config });
  $("#dryRunQuick").checked = gameCfg.dry_run;
  if (!quiet) {
    $("#configSaved").textContent = "已保存";
    setTimeout(() => ($("#configSaved").textContent = ""), 2000);
  }
}

async function switchGame(newGame) {
  if (state.running) {
    logEvent("请先停止当前 Bot 再切换游戏", "warn");
    $("#gameSelect").value = state.config.game;
    return;
  }
  // Save the outgoing game's form values first, then switch + populate.
  try { await saveConfig(true); } catch { /* ignore */ }
  state.config.game = newGame;
  populateGameSelect();
  populateConfigForm();
  try { await saveConfig(true); } catch { /* ignore */ }
  logEvent(`已切换游戏：${gameInfo(newGame)?.name || newGame}`, "ok");
}

function renderReplay() {
  const snap = state.replay[state.replayIndex];
  $("#replayPos").textContent = `${state.replay.length ? state.replayIndex + 1 : 0} / ${state.replay.length}`;
  $("#replaySlider").max = Math.max(0, state.replay.length - 1);
  $("#replaySlider").value = state.replayIndex;
  if (!snap) return;
  $("#replayTurn").textContent = snap.turn;
  $("#replayStep").textContent = snap.step;
  $("#replayPlan").textContent = snap.plan ? snap.plan.join("；") : "—";
  renderState(snap, $("#replayBoard"));
}

function demoSnapshot() {
  return {
    turn: 3,
    step: "MAIN_ACTION",
    current_player: 1,
    is_my_turn: true,
    me: {
      hero_health: 28, mana: 3, max_mana: 3, deck_count: 22,
      hero: { id: 1, name: "乌瑟尔", health: 28, attack: 0 },
      hand: [
        { id: 10, name: "青铜门卫", cost: 3, attack: 3, health: 4 },
        { id: 11, name: "雷达探测", cost: 2, attack: 0, health: 0 },
      ],
      board: [{ id: 20, name: "安保自动机", attack: 2, health: 3, taunt: false, divine_shield: false }],
    },
    opp: {
      hero_health: 26, mana: 3, max_mana: 3, deck_count: 21,
      hero: { id: 3, name: "对手", health: 26, attack: 0 },
      hand: [],
      board: [{ id: 30, name: "随从", attack: 2, health: 2, taunt: true, divine_shield: false }],
    },
    secrets: [],
  };
}

async function init() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  $("#btnStart").addEventListener("click", async () => {
    if (!isTauri) { logEvent("演示模式：无法启动 sidecar，请在 Tauri 中打开", "warn"); return; }
    try {
      await invoke("start_bot");
    } catch (e) { logEvent(`启动失败：${e}`, "error"); }
  });
  $("#btnStop").addEventListener("click", async () => {
    if (!isTauri) return;
    try { await invoke("stop_bot"); } catch (e) { logEvent(`停止失败：${e}`, "error"); }
  });
  $("#btnPause").addEventListener("click", async () => {
    if (!isTauri) return;
    await invoke("send_bot_command", { command: { type: "pause" } });
  });
  $("#btnResume").addEventListener("click", async () => {
    if (!isTauri) return;
    await invoke("send_bot_command", { command: { type: "resume" } });
  });
  $("#gameSelect").addEventListener("change", async (e) => {
    try { await switchGame(e.target.value); } catch (err) { logEvent(`切换游戏失败：${err}`, "error"); }
  });
  $("#dryRunQuick").addEventListener("change", async (e) => {
    const form = $("#configForm");
    form.dryRun.checked = e.target.checked;
    await saveConfig();
  });
  $("#configForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await saveConfig(); } catch (err) { logEvent(`保存失败：${err}`, "error"); }
  });
  $("#btnClearLog").addEventListener("click", () => ($("#eventLog").innerHTML = ""));

  $("#btnReplayLoad").addEventListener("click", async () => {
    const path = $("#replayPath").value.trim();
    if (!path) { logEvent("请先填 Power.log 路径", "warn"); return; }
    if (!isTauri) { logEvent("演示模式：无法读取日志文件", "warn"); return; }
    try {
      const snaps = await invoke("replay_log", { path, game: state.config?.game });
      state.replay = snaps;
      state.replayIndex = 0;
      renderReplay();
      logEvent(`回放已载入：${snaps.length} 个快照`, "ok");
    } catch (e) { logEvent(`回放失败：${e}`, "error"); }
  });
  $("#btnReplayPrev").addEventListener("click", () => {
    state.replayIndex = Math.max(0, state.replayIndex - 1);
    renderReplay();
  });
  $("#btnReplayNext").addEventListener("click", () => {
    state.replayIndex = Math.min(state.replay.length - 1, state.replayIndex + 1);
    renderReplay();
  });
  $("#replaySlider").addEventListener("input", (e) => {
    state.replayIndex = parseInt(e.target.value, 10) || 0;
    renderReplay();
  });

  if (isTauri) {
    await window.__TAURI__.event.listen("bot-event", (ev) => onBotEvent(ev.payload));
    await loadConfig();
    try {
      state.running = await invoke("get_status");
      setStatus();
    } catch { /* ignore */ }
    logEvent("已连接 Tauri 后端", "ok");
  } else {
    state.games = [{ id: "hearthstone", name: "炉石传说", description: "狂野 · 圣骑士机械套牌「号角」" }];
    state.config = { game: "hearthstone", games: { hearthstone: { ...DEFAULT_GAME_CONFIG } } };
    populateGameSelect();
    populateConfigForm();
    $("#liveHint").textContent = "演示模式：在 Tauri 中打开可接真实对局";
    renderState(demoSnapshot(), $("#liveBoard"));
    $("#liveTurn").textContent = "3";
    $("#liveStep").textContent = "MAIN_ACTION";
    $("#liveCurrent").textContent = "我方";
    logEvent("演示模式：页面在浏览器中打开，未连接引擎", "warn");
  }
  setStatus();
}

init();
