// GameDock — funções novas da v0.4: status e anotações, tempo para zerar, saves, disco, melhorias de emulador,
// sortear jogo, grátis da Epic, desafios e calendário, edição de capturas, aparência, Modo Jogo, Discord,
// RetroAchievements, controle, atualização e a apresentação da primeira abertura.
// Usa o estado e as funções de app.js/pages.js (mesmo escopo global).

const STATUS = {
  playing: { label: "Jogando", icon: "▶" },
  backlog: { label: "Quero jogar", icon: "☆" },
  beaten: { label: "Zerado", icon: "✓" },
  platinum: { label: "Platinado", icon: "◆" },
  abandoned: { label: "Abandonado", icon: "✕" },
};
const fmtBytes = (n) => n == null ? "—" : n >= 1e12 ? `${(n / 1e12).toFixed(1).replace(".", ",")} TB` : n >= 1e9 ? `${(n / 1e9).toFixed(1).replace(".", ",")} GB`
  : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;
const TIER_LETTER = { platinum: "P", gold: "O", silver: "P", bronze: "B" };

// ---------------------------------------------------------------- preferências + aparência

async function loadPrefs() {
  state.prefs = await api().get_prefs();
  applyAppearance(state.prefs);
  return state.prefs;
}

async function setPref(key, value) {
  state.prefs = await api().set_prefs({ [key]: value });
  applyAppearance(state.prefs);
  return state.prefs;
}

const BASE_TIERS = { platinum: "#a9dcff", gold: "#f5c542", silver: "#c9d2de", bronze: "#d08a4c" };
const CB_TIERS = { platinum: "#56b4e9", gold: "#f0e442", silver: "#ffffff", bronze: "#e69f00" };  // paleta Okabe-Ito

function applyAppearance(p) {
  const root = document.documentElement;
  root.dataset.theme = p.theme || "dark";
  root.classList.toggle("no-anim", !p.animated_bg || p.reduce_motion);
  root.classList.toggle("reduce-motion", !!p.reduce_motion);
  root.classList.toggle("cb", !!p.colorblind);
  document.body.style.zoom = (p.text_scale || 100) / 100;
  const colors = p.colorblind ? CB_TIERS : BASE_TIERS;
  for (const [k, c] of Object.entries(colors)) {
    if (typeof TIERS !== "undefined" && TIERS[k]) TIERS[k].color = c;
    if (typeof TIER_COLORS !== "undefined") TIER_COLORS[k] = c;
  }
  if ($("textScaleOut")) $("textScaleOut").textContent = `${p.text_scale || 100}%`;
}

function paintPrefControls() {
  document.querySelectorAll("[data-pref]").forEach((el) => {
    const value = state.prefs[el.dataset.pref];
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value ?? "";
  });
  $("textScaleOut").textContent = `${state.prefs.text_scale || 100}%`;
}

function bindPrefControls() {
  document.querySelectorAll("[data-pref]").forEach((el) => {
    const read = () => el.type === "checkbox" ? el.checked : el.dataset.num !== undefined ? Number(el.value) : el.value.trim();
    el.addEventListener(el.type === "range" ? "input" : "change", async () => {
      const key = el.dataset.pref, value = read();
      await setPref(key, value);
      if (el.type !== "range") Sfx.play("move");
      if (key === "console_mode" && value && !$("autostart").checked) { $("autostart").checked = true; $("autostart").dispatchEvent(new Event("change")); }
      if (key === "discord_presence" && value && !state.prefs.discord_client_id) toast("Cole o Application ID do app do Discord logo abaixo.");
    });
    if (el.tagName === "INPUT" && el.type !== "checkbox" && el.type !== "range")
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  });
}

// ---------------------------------------------------------------- configurações extras

const _loadExtraSettings = loadExtraSettings;
loadExtraSettings = async function (settings) {
  await _loadExtraSettings(settings);
  await loadPrefs();
  paintPrefControls();
  renderGameModeApps();
  renderRA();
  renderAbout();
  refreshPad(true);
  const s = await api().get_settings();
  $("igdbId").value = s.igdbClientId || "";
  $("igdbSecret").value = s.igdbClientSecret || "";
};

async function renderGameModeApps() {
  const chosen = new Set(state.prefs.game_mode_close || []);
  const running = await api().list_running_apps();
  const names = [...new Set([...chosen, ...running.map((a) => a.name)])];
  const byName = Object.fromEntries(running.map((a) => [a.name, a]));
  $("gmApps").innerHTML = names.length ? names.map((n) => `
    <button type="button" class="app-chip ${chosen.has(n) ? "on" : ""}" data-app="${esc(n)}">
      ${esc(byName[n]?.title || n.replace(/\.exe$/, ""))}${byName[n] ? `<small>${fmtBytes(byName[n].memory)}</small>` : "<small>fechado agora</small>"}</button>`).join("")
    : `<p class="settings-hint">Abra os apps que você quer que fechem (ex: navegador) e volte aqui.</p>`;
  $("gmApps").querySelectorAll("[data-app]").forEach((b) => b.addEventListener("click", async () => {
    const set = new Set(state.prefs.game_mode_close || []);
    set.has(b.dataset.app) ? set.delete(b.dataset.app) : set.add(b.dataset.app);
    await setPref("game_mode_close", [...set]);
    b.classList.toggle("on");
    Sfx.play("move");
  }));
}

async function renderRA() {
  const ra = await api().ra_status();
  const box = $("raBox");
  if (ra.user) {
    box.innerHTML = `<div class="settings-actions" style="margin-top:0"><span class="settings-status ok" style="margin:0 !important">Conectado como <b>${esc(ra.user)}</b>${ra.apiKey ? "" : " · sem Web API Key (os troféus não aparecem no GameDock)"}</span>
      <button type="button" class="ghost-btn" id="raOut">Sair</button></div>`;
    $("raOut").addEventListener("click", async () => { await api().ra_logout(); Sfx.play("favOff"); renderRA(); });
    return;
  }
  box.innerHTML = `
    <div class="options-grid">
      <label>Usuário<input id="raUser" spellcheck="false" autocomplete="off"></label>
      <label>Senha<input id="raPass" type="password" autocomplete="off"></label>
    </div>
    <label style="margin-top:12px">Web API Key <small class="pe-count">(retroachievements.org → Settings → Keys)</small><input id="raKey" type="password" spellcheck="false" autocomplete="off"></label>
    <div class="settings-actions">
      <button type="button" class="link-btn" id="raSite">Criar conta grátis ↗</button>
      <button type="button" class="ghost-btn tall" id="raLogin">Entrar</button>
    </div>
    <p class="settings-status" id="raStatus"></p>`;
  $("raSite").addEventListener("click", () => api().open_url("https://retroachievements.org/createaccount"));
  $("raLogin").addEventListener("click", async () => {
    $("raStatus").textContent = "Entrando…";
    const r = await api().ra_login($("raUser").value, $("raPass").value, $("raKey").value);
    Sfx.play(r.ok ? "success" : "error");
    if (!r.ok) { $("raStatus").textContent = r.error; $("raStatus").className = "settings-status error"; return; }
    toast(`RetroAchievements conectado: ${r.user}`);
    renderRA();
  });
}

async function renderAbout() {
  const a = await api().get_about();
  const u = a.update || {};
  $("about").innerHTML = `
    <dl class="facts"><dt>Versão</dt><dd>${esc(a.version)}${a.frozen ? "" : " (código-fonte)"}</dd>
    <dt>Atualizações</dt><dd>${esc(a.updateRepo || "—")}</dd>
    <dt>Seus dados</dt><dd>${esc(a.dataDir)}</dd></dl>`;
  paintUpdate(u);
}

function paintUpdate(u) {
  const el = $("updateStatus");
  const btn = $("updateCheck");
  if (u.available) {
    el.innerHTML = `Versão <b>${esc(u.version)}</b> disponível.${u.notes ? `<br><small>${esc(u.notes.slice(0, 300))}</small>` : ""}`;
    el.className = "settings-status ok";
    btn.textContent = "Atualizar agora";
    btn.dataset.install = "1";
  } else {
    el.textContent = u.error && u.error !== "no_repo" ? `Não deu para procurar: ${u.error}` : u.version ? `Você está na versão mais nova (${u.current}).` : "";
    el.className = "settings-status";
    btn.textContent = "Procurar atualização";
    delete btn.dataset.install;
  }
}

async function checkOrInstallUpdate() {
  const btn = $("updateCheck");
  if (btn.dataset.install) {
    btn.disabled = true;
    $("updateStatus").textContent = "Baixando a versão nova… o GameDock vai fechar e abrir de novo sozinho.";
    const r = await api().install_update();
    if (!r.ok) { btn.disabled = false; $("updateStatus").textContent = r.error; Sfx.play("error"); }
    return;
  }
  $("updateStatus").textContent = "Procurando…";
  paintUpdate(await api().check_update());
}

async function saveIgdb() {
  const r = await api().set_igdb_keys($("igdbId").value, $("igdbSecret").value);
  $("igdbStatus").textContent = r.ok ? ($("igdbId").value.trim() ? "Chaves salvas: o tempo para zerar aparece na página de cada jogo." : "Chaves removidas.") : r.error;
  $("igdbStatus").className = "settings-status " + (r.ok ? "ok" : "error");
  Sfx.play(r.ok ? "success" : "error");
}

// ---------------------------------------------------------------- controle (bateria + luz)

async function refreshPad(inSettings = false) {
  const c = await api().get_controller();
  const b = c.battery;
  const el = $("padBattery");
  el.hidden = !b || b.level == null;
  if (b && b.level != null) {
    el.classList.toggle("low", b.level <= 20 && !b.charging);
    el.classList.toggle("charging", !!b.charging);
    el.querySelector("i").style.setProperty("--lvl", `${b.level}%`);
    el.querySelector("b").textContent = `${b.level}%`;
    el.title = `${b.model} · ${b.connection}${b.charging ? " · carregando" : ""}`;
  }
  if (inSettings) {
    $("padInfo").textContent = b ? `🎮 ${b.model} · ${b.connection}${b.level != null ? ` · bateria ${b.level}%` : ""}${b.charging ? " (carregando)" : ""}`
      : "Nenhum controle conectado.";
    $("lightbarHint").textContent = c.ds4windows ? "o DS4Windows está aberto e controla a luz: mude a cor nele" : "DualSense e DualShock 4, por USB ou Bluetooth";
  }
}

// ---------------------------------------------------------------- página do jogo: status, notas, tempo, saves, disco, melhorias

const _openGamePage = openGamePage;
openGamePage = async function () {
  const game = current();
  const done = _openGamePage();
  if (game) loadGamePageExtras(game);
  return done;
};

const _hidePage = hidePage;
hidePage = function (id, name) {
  if (id === "gamePage") stopThemeMusic();
  return _hidePage(id, name);
};

async function loadGamePageExtras(game) {
  const id = game.id;
  stopThemeMusic();
  $("gpNotes").value = "";
  $("gpNotesSaved").textContent = "";
  $("gpBeat").innerHTML = `<p class="shots-empty">…</p>`;
  $("gpEnhBlock").hidden = true;
  $("gpDisk").textContent = "";
  const details = await api().get_game_details(id);
  if (state.pageGame !== id) return;
  renderStatus(details.status);
  $("gpNotes").value = details.notes || "";
  $("gpMove").hidden = !details.movable;
  renderBeat(id, details);
  renderSaves(id);
  renderDisk(id);
  renderEnhancements(id);
  playThemeMusic(id);
}

function renderStatus(status) {
  $("gpStatus").innerHTML = Object.entries(STATUS).map(([k, s]) =>
    `<button class="status-chip ${status === k ? "on" : ""}" data-status="${k}"><i>${s.icon}</i>${s.label}</button>`).join("");
  $("gpStatus").querySelectorAll("[data-status]").forEach((b) => b.addEventListener("click", async () => {
    const next = b.dataset.status === status ? null : b.dataset.status;
    const r = await api().set_game_options(state.pageGame, { status: next });
    const g = state.all.find((x) => x.id === state.pageGame);
    if (g) g.status = r.status;
    renderStatus(r.status);
    if (r.status === "beaten" || r.status === "platinum") {
      Sfx.play("platinum");
      confetti();
      toast(r.status === "beaten" ? `Zerado! ${g?.name || ""} 🎉` : `Platinado! ${g?.name || ""} 🏆`);
    } else Sfx.play(next ? "favOn" : "favOff");
    renderTabs();
  }));
}

let notesTimer;
function bindNotes() {
  $("gpNotes").addEventListener("input", () => {
    clearTimeout(notesTimer);
    $("gpNotesSaved").textContent = "…";
    notesTimer = setTimeout(async () => {
      await api().set_game_options(state.pageGame, { notes: $("gpNotes").value });
      $("gpNotesSaved").textContent = "salvo";
    }, 700);
  });
  $("gpNotes").addEventListener("keydown", (e) => e.stopPropagation());   // digitar não vira atalho
}

async function renderBeat(id, details) {
  const t = await api().get_time_to_beat(id);
  if (state.pageGame !== id) return;
  const played = details.playtime / 3600;
  const rows = [];
  if (t.source === "igdb") {
    for (const [k, label] of [["hastily", "Só a história, correndo"], ["normally", "História + extras"], ["completely", "100%"]])
      if (t[k]) rows.push([label, t[k]]);
  }
  const target = t.normally || t.hastily || t.completely || t.user;
  const left = target ? Math.max(0, target - played) : null;
  $("gpBeat").innerHTML = `
    ${target ? `<div class="beat-main"><b>${left > 0 ? `faltam ~${fmtHours(left * 3600)}` : "você já passou do tempo médio!"}</b>
      <span>${fmtHours(played * 3600)} jogadas de ~${fmtHours(target * 3600)}</span>
      <div class="xp-bar beat-bar"><b style="width:${Math.min(100, (played / target) * 100)}%"></b></div></div>` : ""}
    ${rows.length ? `<dl class="facts">${rows.map(([l, h]) => `<dt>${l}</dt><dd>${fmtHours(h * 3600)}</dd>`).join("")}</dl>` : ""}
    ${t.source !== "igdb" ? `<p class="settings-hint">${t.configured ? "O IGDB não tem esse jogo. " : "Conecte o IGDB nas Configurações para ver o tempo médio, ou "}diga quantas horas o jogo dura:</p>` : ""}
    <label class="beat-user">Minha estimativa <input id="gpBeatUser" type="number" min="1" max="999" step="1" value="${t.user || ""}" placeholder="h"> horas</label>`;
  $("gpBeatUser").addEventListener("change", async (e) => {
    await api().set_game_options(id, { beat_hours: e.target.value || null });
    Sfx.play("move");
    renderBeat(id, details);
  });
  $("gpBeatUser").addEventListener("keydown", (e) => e.stopPropagation());
}

async function renderSaves(id) {
  const s = await api().get_saves(id);
  if (state.pageGame !== id) return;
  const box = $("gpSaves");
  if (!s.paths?.length && !s.registry?.length) {
    box.innerHTML = `<p class="shots-empty">${s.entry ? "O GameDock sabe onde esse jogo salva, mas ainda não achou nenhum save neste PC." :
      "Não sei onde esse jogo guarda o save (ele não está na lista pública de locais de save)."}</p>
      ${s.backups?.length ? backupList(s.backups) : ""}`;
  } else {
    box.innerHTML = `
      <div class="save-head">
        <div><b>${s.files} arquivo${s.files === 1 ? "" : "s"} · ${fmtBytes(s.size)}</b>
          <span>${s.modified ? `último save ${relativeTime(new Date(s.modified * 1000).toISOString())}` : ""}</span></div>
        <button class="ghost-btn tall" id="gpBackupNow">Fazer backup agora</button>
      </div>
      <div class="save-paths">${s.paths.map((p) => `<button class="link-btn" data-path="${esc(p)}" title="Abrir no Explorer">${esc(p)}</button>`).join("")}
        ${s.registry.map((r) => `<code>${esc(r)}</code>`).join("")}</div>
      ${backupList(s.backups)}`;
    $("gpBackupNow").addEventListener("click", async () => {
      const r = await api().backup_saves(id);
      Sfx.play(r.ok ? "success" : "error");
      toast(r.ok ? "Backup do save feito" : r.error, !r.ok);
      renderSaves(id);
    });
  }
  box.querySelectorAll("[data-path]").forEach((b) => b.addEventListener("click", () => api().open_save_location(b.dataset.path)));
  box.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Confirmar?"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Restaurar"; }, 4000); return; }
    const r = await api().restore_saves(id, b.dataset.restore);
    Sfx.play(r.ok ? "success" : "error");
    toast(r.ok ? `Save restaurado (${r.restored} arquivos). O save de antes ficou guardado.` : r.error, !r.ok);
    renderSaves(id);
  }));
}

function backupList(backups) {
  if (!backups?.length) return `<p class="settings-hint" style="margin-top:10px">Nenhum backup ainda.</p>`;
  return `<div class="backups">${backups.slice(0, 8).map((b) => `
    <div class="backup"><span><b>${b.at ? fmtDate(b.at) + " " + new Date(b.at * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : esc(b.name)}</b>
      ${b.reason === "auto" ? "automático" : b.reason === "restore" ? "antes de restaurar" : b.reason === "uninstall" ? "antes de desinstalar" : "manual"} · ${b.files} arq. · ${fmtBytes(b.size)}</span>
      <button class="ghost-btn" data-restore="${esc(b.name)}">Restaurar</button></div>`).join("")}</div>`;
}

async function renderDisk(id, again = 0) {
  const d = await api().get_disk(id);
  if (state.pageGame !== id) return;
  const el = $("gpDisk");
  if (!d.path) { el.textContent = ""; return; }
  const drive = d.drives?.find((x) => x.drive === d.drive);
  const t = d.task;
  el.innerHTML = t?.running ? `Movendo… ${t.total ? Math.round((t.done / t.total) * 100) : 0}%`
    : `<span>Ocupa <b>${d.size == null ? "calculando…" : fmtBytes(d.size)}</b> em ${esc(d.drive || "")}</span>
       ${drive ? `<span class="disk-bar"><i style="width:${(1 - drive.free / drive.total) * 100}%"></i></span><span>${fmtBytes(drive.free)} livres</span>` : ""}
       ${t?.error ? `<span class="error">Não deu para mover: ${esc(t.error)}</span>` : ""}`;
  if ((d.size == null || t?.running) && again < 120) setTimeout(() => renderDisk(id, again + 1), t?.running ? 800 : 1500);
}

// desinstalar (tela inicial, botão da lixeira): 1º clique mostra o que vai acontecer,
// 2º clique (em até 8 s, no mesmo jogo) confirma. Trocar de jogo desarma.
let uninstallTimer = null;
let uninstallArmedFor = null;
function resetUninstallButton() {
  const b = $("uninstallBtn");
  clearTimeout(uninstallTimer);
  uninstallArmedFor = null;
  if (b.dataset.busy === "1") return;
  b.dataset.armed = "";
  b.querySelector("span").textContent = "Confirmar desinstalação";
}

const _showHeroUninstall = showHero;
showHero = function (game) {
  if (uninstallArmedFor && uninstallArmedFor !== game?.id) resetUninstallButton();
  return _showHeroUninstall(game);
};

async function uninstallGame() {
  const b = $("uninstallBtn");
  const game = current();
  if (!game || b.dataset.busy === "1") return;
  const { id, name } = game;
  if (b.dataset.armed !== "1" || uninstallArmedFor !== id) {
    const plan = await api().uninstall_plan(id);
    if (plan.mode === "none") { toast(plan.text, true, 6000); Sfx.play("error"); return; }
    toast(`${name}: ${plan.text}`, false, 8000);
    b.dataset.armed = "1";
    uninstallArmedFor = id;
    Sfx.play("move");
    clearTimeout(uninstallTimer);
    uninstallTimer = setTimeout(resetUninstallButton, 8000);
    return;
  }
  clearTimeout(uninstallTimer);
  b.dataset.armed = "";
  b.dataset.busy = "1";
  b.querySelector("span").textContent = "Desinstalando…";
  toast(`Desinstalando ${name}… os saves foram copiados antes.`, false, 6000);
  let r;
  try {
    r = await api().uninstall_game(id);
  } finally {
    b.dataset.busy = "";
    resetUninstallButton();
  }
  if (!r.ok) { toast(r.error, true, 6000); Sfx.play("error"); return; }
  if (!r.removed) { toast(`${name} continua instalado (o desinstalador foi cancelado ou ainda não terminou).`, false, 6000); return; }
  Sfx.play("success");
  toast(`${name} foi desinstalado.`);
  await loadGames();
}

async function moveGame() {
  const r = await api().move_game(state.pageGame);
  if (r.cancelled) return;
  if (!r.ok) { toast(r.error, true); Sfx.play("error"); return; }
  toast("Movendo o jogo… pode continuar usando o GameDock.");
  renderDisk(state.pageGame);
}

async function renderEnhancements(id) {
  const e = await api().get_game_enhancements(id);
  if (state.pageGame !== id || !e.options?.length) return;
  $("gpEnhBlock").hidden = false;
  $("gpEnhEmu").textContent = `vale só para este jogo · ${e.emulator}`;
  $("gpEnh").innerHTML = e.options.map((o) => o.type === "bool"
    ? `<label class="switch-row"><span>${esc(o.label)}${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</span>
        <input type="checkbox" data-enh="${o.key}" ${e.values[o.key] ? "checked" : ""}><i class="switch"></i></label>`
    : `<label class="volume-row"><span style="width:auto;flex:1">${esc(o.label)}</span><select class="select" data-enh="${o.key}">
        ${o.options.map(([v, l]) => `<option value="${v}" ${String(v) === String(e.values[o.key]) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`).join("");
  $("gpEnh").querySelectorAll("[data-enh]").forEach((el) => el.addEventListener("change", async () => {
    await api().set_game_enhancements(id, { [el.dataset.enh]: el.type === "checkbox" ? el.checked : Number(el.value) });
    Sfx.play("move");
    toast("Vale a partir da próxima vez que o jogo abrir");
  }));
}

// música do jogo (PS3: SND0 do disco) e trailer com som baixo
let themeAudio = null;
async function playThemeMusic(id) {
  if (!state.prefs?.theme_music) return;
  const url = await api().get_theme_music(id);
  if (!url || state.pageGame !== id || !$("gamePage").classList.contains("show")) return;
  themeAudio = new Audio(url);
  themeAudio.loop = true;
  themeAudio.volume = 0;
  themeAudio.play().catch(() => {});
  const target = (state.sound?.volume ?? 0.6) * 0.35;
  const fade = setInterval(() => { if (!themeAudio) return clearInterval(fade); themeAudio.volume = Math.min(target, themeAudio.volume + 0.02); if (themeAudio.volume >= target) clearInterval(fade); }, 80);
}
function stopThemeMusic() {
  if (themeAudio) { themeAudio.pause(); themeAudio = null; }
}

const _playTrailer = playTrailer;
playTrailer = function (trailer, withSound) {
  if (withSound) stopThemeMusic();
  const soft = !withSound && state.prefs?.trailer_music && !themeAudio;
  _playTrailer(trailer, withSound || soft);
  if (soft) { const v = $("gpVideo"); v.volume = (state.sound?.volume ?? 0.6) * 0.25; }
};

// ---------------------------------------------------------------- abas por status

const _tabList = tabList;
tabList = function () {
  const tabs = _tabList();
  const extra = [["status:playing", "Jogando"], ["status:backlog", "Quero jogar"], ["status:done", "Zerados"]];
  const at = tabs.findIndex((t) => t.id.startsWith("src:"));
  const add = extra.filter(([id]) => filterGames(state.all, id, "").length).map(([id, label]) => ({ id, label }));
  tabs.splice(at < 0 ? tabs.length : at, 0, ...add);
  return tabs;
};
const _filterGames = filterGames;
filterGames = function (all, tab, query) {
  if (!tab.startsWith("status:")) return _filterGames(all, tab, query);
  const wanted = tab.slice(7);
  let list = all.filter((g) => wanted === "done" ? ["beaten", "platinum"].includes(g.status) : g.status === wanted);
  if (query) list = list.filter((g) => fold(g.name).includes(fold(query)));
  return list;
};

// ---------------------------------------------------------------- sortear

async function openRandom() {
  const pick = await api().random_game(true);
  if (!pick) return toast("Nenhum jogo para sortear.");
  const pool = pick.pool.map((id) => state.all.find((g) => g.id === id)).filter(Boolean);
  const reel = $("randomReel");
  $("randomOverlay").classList.add("show");
  $("randomName").textContent = "";
  pushLayer("random", closeRandom, { Enter: playRandom, s: openRandom });
  const spins = 14;
  for (let i = 0; i < spins; i++) {
    const g = i === spins - 1 ? state.all.find((x) => x.id === pick.id) : pool[Math.floor(Math.random() * pool.length)];
    if (!g) continue;
    reel.replaceChildren(coverElement(g));
    Sfx.play("move");
    await new Promise((r) => setTimeout(r, 60 + i * i * 2.4));
  }
  state.randomPick = pick.id;
  $("randomName").textContent = pick.name;
  reel.classList.remove("land"); void reel.offsetWidth; reel.classList.add("land");
  Sfx.play("success");
}
function closeRandom() {
  $("randomOverlay").classList.remove("show");
  popLayer("random");
}
function playRandom() {
  const i = state.games.findIndex((g) => g.id === state.randomPick);
  closeRandom();
  if (i < 0) { state.tab = "all"; applyView(state.randomPick); } else select(i, { force: true });
  play();
}

// ---------------------------------------------------------------- grátis da Epic

async function openFreeGames() {
  $("freeDot").hidden = true;
  showPage("freePage", "free", {});
  $("freeNow").innerHTML = `<p class="shots-empty">Buscando…</p>`;
  const f = await api().get_free_games();
  const card = (g, next) => `
    <button class="free-card" data-url="${esc(g.url)}">
      <div class="free-img">${g.image ? `<img src="${esc(g.image)}" alt="" loading="lazy">` : ""}<span>${next ? "Em breve" : "Grátis agora"}</span></div>
      <strong>${esc(g.title)}</strong>
      <small>${next ? `de ${fmtDate(g.from)} até ${fmtDate(g.until)}` : `até ${fmtDate(g.until)}`}${g.price && g.price !== "0" ? ` · custa ${esc(g.price)}` : ""}</small>
    </button>`;
  $("freeNow").innerHTML = f.now?.length ? f.now.map((g) => card(g, false)).join("") : `<p class="shots-empty">Nenhum jogo grátis agora.</p>`;
  $("freeNext").innerHTML = (f.next || []).map((g) => card(g, true)).join("");
  $("freeNextTitle").hidden = !f.next?.length;
  document.querySelectorAll("#freePage [data-url]").forEach((b) => b.addEventListener("click", () => api().open_url(b.dataset.url)));
}

// ---------------------------------------------------------------- perfil: desafios, sequência, calendário

const _renderProfile = renderProfile;
renderProfile = function (p) {
  _renderProfile(p);
  renderChallenges();
  renderCalendar();
};

async function renderChallenges() {
  const c = await api().get_challenges();
  $("pfGoalsEnd").textContent = `renova em ${new Date(c.weekEnds + "T00:00").toLocaleDateString("pt-BR", { weekday: "long" })}`;
  $("pfGoals").innerHTML = c.goals.map((g) => `
    <div class="goal ${g.done ? "done" : ""}"><span class="goal-check">${g.done ? "✓" : ""}</span>
      <div><strong>${esc(g.text)}</strong><div class="xp-bar"><b style="width:${g.progress * 100}%"></b></div></div>
      <small>${g.done ? "+50 XP" : `${String(g.value).replace(".", ",")}/${g.target}`}</small></div>`).join("");
  $("pfStreak").innerHTML = `<div class="streak-num"><b>${c.streak}</b><span>${c.streak === 1 ? "dia seguido" : "dias seguidos"}</span></div>
    <p>Melhor sequência: <b>${c.bestStreak} ${c.bestStreak === 1 ? "dia" : "dias"}</b>. Jogue pelo menos 5 minutos por dia para manter.</p>`;
  const done = c.meta.filter((m) => m.done).length;
  $("pfMetaCount").textContent = `${done} de ${c.meta.length}`;
  $("pfMeta").innerHTML = c.meta.map((m) => `
    <div class="meta-item ${m.done ? "done" : ""}" style="--tier:${TIER_COLORS[m.tier]}">
      <i class="meta-badge-icon">${m.done ? "★" : Math.round(m.progress * 100) + "%"}</i>
      <div><strong>${esc(m.name)}</strong><span>${esc(m.desc)}</span>
      ${m.done ? `<small>${fmtDate(m.at)} · +${m.xp} XP</small>` : `<div class="xp-bar"><b style="width:${m.progress * 100}%"></b></div>`}</div></div>`).join("");
}

async function renderCalendar() {
  const cal = await api().get_calendar();
  $("pfCalTotal").textContent = `${fmtHours(cal.total)} no último ano`;
  const max = Math.max(1, ...cal.days.map((d) => d.seconds));
  const level = (s) => s <= 0 ? 0 : s < max * 0.15 ? 1 : s < max * 0.4 ? 2 : s < max * 0.7 ? 3 : 4;
  const weeks = [];
  for (let i = 0; i < cal.days.length; i += 7) weeks.push(cal.days.slice(i, i + 7));
  const months = weeks.map((w, i) => { const d = new Date(w[0].date + "T00:00"); return d.getDate() <= 7 || i === 0 ? d.toLocaleDateString("pt-BR", { month: "short" }).replace(".", "") : ""; });
  $("pfCalendar").innerHTML = `<div class="cal-months">${months.map((m) => `<span>${m}</span>`).join("")}</div>
    <div class="cal-grid">${weeks.map((w) => `<div class="cal-week">${w.map((d) =>
      `<i class="l${level(d.seconds)}" title="${new Date(d.date + "T00:00").toLocaleDateString("pt-BR")}: ${d.seconds ? fmtHours(d.seconds) : "nada"}"></i>`).join("")}</div>`).join("")}</div>
    <div class="cal-legend">menos <i class="l0"></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><i class="l4"></i> mais</div>`;
  const hmax = Math.max(1, ...cal.hours.flat());
  const days = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
  $("pfHours").innerHTML = `<div class="hm-grid">${cal.hours.map((row, d) => `<span>${days[d]}</span>${row.map((s, h) =>
    `<i style="--a:${s ? 0.15 + 0.85 * (s / hmax) : 0}" title="${days[d]} ${h}h: ${s ? fmtHours(s) : "nada"}"></i>`).join("")}`).join("")}
    <span></span>${Array.from({ length: 24 }, (_, h) => `<em>${h % 6 === 0 ? h + "h" : ""}</em>`).join("")}</div>`;
}

// ---------------------------------------------------------------- galeria: copiar, recortar, cortar clipe, vertical, Discord

const _paintLightbox = paintLightbox;
paintLightbox = function () {
  _paintLightbox();
  endEdit();
  const item = state.lightbox.items[state.lightbox.index];
  const isClip = item.kind === "clip";
  const tools = $("lbTools");
  tools.hidden = !item.id;
  tools.querySelector('[data-tool="copy"]').hidden = isClip;
  tools.querySelector('[data-tool="crop"]').hidden = isClip;
  tools.querySelector('[data-tool="trim"]').hidden = !isClip;
  tools.querySelector('[data-tool="vertical"]').hidden = !isClip;
};

const edit = { mode: null };
function endEdit() {
  edit.mode = null;
  $("cropBox").hidden = true;
  $("lbTrim").hidden = true;
  $("lbTools").classList.remove("editing");
}

async function lightboxTool(tool) {
  const item = state.lightbox.items[state.lightbox.index];
  if (tool === "copy") {
    const ok = await api().copy_image(item.id);
    toast(ok ? "Imagem copiada: cole com Ctrl+V" : "Não deu para copiar", !ok);
  } else if (tool === "discord") {
    toast("Enviando para o Discord…");
    const r = await api().share_discord(item.id, `${item.trophy ? "🏆 " + item.trophy + " · " : ""}${item.game || ""}`);
    toast(r.ok ? "Enviado para o Discord!" : r.error, !r.ok);
    Sfx.play(r.ok ? "success" : "error");
  } else if (tool === "crop") startCrop();
  else if (tool === "trim" || tool === "vertical") startTrim(tool === "vertical");
  else if (tool === "cancel") endEdit();
  else if (tool === "apply") applyEdit(item);
}

function startCrop() {
  edit.mode = "crop";
  const img = $("lightboxImg");
  const r = img.getBoundingClientRect(), stage = $("lbStage").getBoundingClientRect();
  edit.box = { x: r.left - stage.left + r.width * 0.1, y: r.top - stage.top + r.height * 0.1, w: r.width * 0.8, h: r.height * 0.8 };
  edit.bounds = { x: r.left - stage.left, y: r.top - stage.top, w: r.width, h: r.height };
  paintCrop();
  $("cropBox").hidden = false;
  $("lbTools").classList.add("editing");
}

function paintCrop() {
  const b = edit.box, el = $("cropBox");
  Object.assign(el.style, { left: b.x + "px", top: b.y + "px", width: b.w + "px", height: b.h + "px" });
}

function bindCrop() {
  const el = $("cropBox");
  el.addEventListener("mousedown", (e) => {
    e.preventDefault(); e.stopPropagation();
    const handle = e.target.dataset.h, mouse = { x: e.clientX, y: e.clientY }, B = edit.bounds;
    const startBox = { ...edit.box };
    const move = (ev) => {
      const dx = ev.clientX - mouse.x, dy = ev.clientY - mouse.y, b = edit.box;
      if (!handle) {
        b.x = clamp(startBox.x + dx, B.x, B.x + B.w - b.w);
        b.y = clamp(startBox.y + dy, B.y, B.y + B.h - b.h);
      } else {
        if (handle.includes("w")) { const x = clamp(startBox.x + dx, B.x, startBox.x + startBox.w - 30); b.w = startBox.x + startBox.w - x; b.x = x; }
        if (handle.includes("e")) b.w = clamp(startBox.w + dx, 30, B.x + B.w - startBox.x);
        if (handle.includes("n")) { const y = clamp(startBox.y + dy, B.y, startBox.y + startBox.h - 30); b.h = startBox.y + startBox.h - y; b.y = y; }
        if (handle.includes("s")) b.h = clamp(startBox.h + dy, 30, B.y + B.h - startBox.y);
      }
      paintCrop();
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function startTrim(vertical) {
  const video = $("lightboxVideo");
  edit.mode = vertical ? "vertical" : "trim";
  const go = () => {
    edit.dur = video.duration || 30;
    edit.start = 0;
    edit.end = edit.dur;
    paintTrim();
    $("lbTrim").hidden = false;
    $("lbTools").classList.add("editing");
  };
  if (video.readyState >= 1) go(); else video.addEventListener("loadedmetadata", go, { once: true });
}

function paintTrim() {
  const pct = (t) => (t / edit.dur) * 100;
  $("trimStart").style.left = pct(edit.start) + "%";
  $("trimEnd").style.left = pct(edit.end) + "%";
  Object.assign($("trimSel").style, { left: pct(edit.start) + "%", width: pct(edit.end - edit.start) + "%" });
  $("trimLabel").textContent = `${edit.start.toFixed(1).replace(".", ",")} s → ${edit.end.toFixed(1).replace(".", ",")} s · ${(edit.end - edit.start).toFixed(1).replace(".", ",")} s${edit.mode === "vertical" ? " · vertical 1080×1920" : ""}`;
}

function bindTrim() {
  for (const which of ["trimStart", "trimEnd"]) {
    $(which).addEventListener("mousedown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const track = $("trimTrack").getBoundingClientRect();
      const move = (ev) => {
        const t = clamp((ev.clientX - track.left) / track.width, 0, 1) * edit.dur;
        if (which === "trimStart") edit.start = Math.min(t, edit.end - 0.5); else edit.end = Math.max(t, edit.start + 0.5);
        $("lightboxVideo").currentTime = which === "trimStart" ? edit.start : edit.end;
        paintTrim();
      };
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }
  $("lightboxVideo").addEventListener("timeupdate", () => {
    const v = $("lightboxVideo");
    if (edit.mode && (edit.mode === "trim" || edit.mode === "vertical") && v.currentTime > edit.end) v.currentTime = edit.start;
  });
}

async function applyEdit(item) {
  if (edit.mode === "crop") {
    const img = $("lightboxImg"), B = edit.bounds, b = edit.box;
    const sx = img.naturalWidth / B.w, sy = img.naturalHeight / B.h;
    const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(b.w * sx), height: Math.round(b.h * sy) });
    const source = new Image();
    source.crossOrigin = "anonymous";
    source.src = img.src;
    await source.decode();
    canvas.getContext("2d").drawImage(source, (b.x - B.x) * sx, (b.y - B.y) * sy, b.w * sx, b.h * sy, 0, 0, canvas.width, canvas.height);
    const r = await api().save_edited_image(item.id, canvas.toDataURL("image/jpeg", 0.92));
    toast(r.ok ? "Recorte salvo como uma captura nova" : r.error, !r.ok);
  } else {
    toast(edit.mode === "vertical" ? "Gerando o vídeo vertical…" : "Cortando o clipe…");
    const r = await api().trim_clip(item.id, edit.start, edit.end, edit.mode === "vertical");
    toast(r.ok ? "Clipe novo salvo na galeria" : r.error, !r.ok);
    Sfx.play(r.ok ? "success" : "error");
  }
  endEdit();
  if ($("galleryPage").classList.contains("show")) renderGallery();
}

// ---------------------------------------------------------------- confete

function confetti() {
  if (state.prefs?.reduce_motion) return;
  const canvas = $("confetti"), ctx = canvas.getContext("2d");
  canvas.width = innerWidth; canvas.height = innerHeight;
  const colors = ["#f5c542", "#ff3d81", "#3dd6ff", "#2fd18b", "#a9dcff", getComputedStyle(document.documentElement).getPropertyValue("--accent")];
  const parts = Array.from({ length: 160 }, () => ({ x: innerWidth / 2 + (Math.random() - 0.5) * 200, y: innerHeight * 0.45,
    vx: (Math.random() - 0.5) * 16, vy: -Math.random() * 16 - 6, r: Math.random() * 6 + 3, c: colors[Math.floor(Math.random() * colors.length)], a: Math.random() * 6 }));
  canvas.classList.add("show");
  let frames = 0;
  const step = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy; p.vy += 0.45; p.vx *= 0.99; p.a += 0.2;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.a); ctx.fillStyle = p.c; ctx.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); ctx.restore();
    }
    if (++frames < 160) requestAnimationFrame(step); else { canvas.classList.remove("show"); ctx.clearRect(0, 0, canvas.width, canvas.height); }
  };
  step();
}

// ---------------------------------------------------------------- primeira abertura

const OB_STEPS = [
  { title: "Bem-vindo ao GameDock", render: (p) => `<p>Todos os seus jogos num lugar só — Steam, Epic, Riot, jogos soltos e emuladores — com troféus, capturas e tempo de jogo.</p>
      <label>Como você quer ser chamado?<input id="obName" maxlength="32" value="${esc(p.name || "")}" spellcheck="false"></label>` },
  { title: "Seus jogos", render: () => `<p>O GameDock já procurou jogos neste PC:</p><div class="ob-covers" id="obCovers"></div>
      <p class="settings-hint">Faltou algum? Use o botão ⟳ em cima ou adicione uma pasta em Configurações.</p>` },
  { title: "Emuladores", render: () => `<p>Quer jogar seus discos de <b>PS1, PS2, PS3</b> ou cartuchos de <b>NES</b>? Em Configurações → Emuladores o GameDock baixa
      o emulador oficial, configura o controle e abre o jogo direto. Use cópias dos seus próprios discos.</p>
      <button type="button" class="ghost-btn tall" id="obEmus">Abrir Emuladores</button>` },
  { title: "Controle", render: () => `<p>Dá para usar o GameDock inteiro pelo controle: <b>A</b> confirma, <b>B</b> volta, o direcional navega, <b>LB/RB</b> trocam de aba.
      Nos campos de texto aparece um teclado na tela.</p><div class="ob-pad" id="obPad">Aperte qualquer botão do controle…</div>` },
  { title: "Tudo pronto!", render: () => `<p>Dicas rápidas:</p><ul class="ob-tips">
      <li><b>Alt+G</b> durante o jogo abre o painel (capturas, clipes, volume, save state).</li>
      <li><b>${esc(state.captureKey || "Print Screen")}</b> captura a tela; os troféus aparecem por cima do jogo.</li>
      <li>O <b>dado</b> em cima sorteia um jogo do seu "Quero jogar".</li>
      <li>Em Configurações tem Modo Jogo, Discord, backup de saves e muito mais.</li></ul>` },
];

async function maybeOnboarding(force = false) {
  const prefs = state.prefs || await loadPrefs();
  if (prefs.onboarding_done && !force) return;
  state.ob = { step: 0, profile: await api().get_profile() };
  state.captureKey = (await api().get_settings()).captureHotkey;
  $("onboarding").classList.add("show");
  pushLayer("onboarding", finishOnboarding, { Enter: nextOnboarding });
  paintOnboarding();
}

function paintOnboarding() {
  const s = OB_STEPS[state.ob.step];
  $("obDots").innerHTML = OB_STEPS.map((_, i) => `<i class="${i === state.ob.step ? "on" : i < state.ob.step ? "done" : ""}"></i>`).join("");
  $("obBody").innerHTML = `<h2>${s.title}</h2>${s.render(state.ob.profile)}`;
  $("obNext").querySelector("span").textContent = state.ob.step === OB_STEPS.length - 1 ? "Começar" : "Continuar";
  $("obSkip").hidden = state.ob.step === OB_STEPS.length - 1;
  if ($("obCovers")) $("obCovers").replaceChildren(...state.all.slice(0, 12).map((g) => { const d = document.createElement("div"); d.className = "ob-cover"; d.append(coverElement(g)); return d; }));
  $("obEmus")?.addEventListener("click", () => { finishOnboarding(); openSettings(); setTimeout(() => $("emuList").scrollIntoView({ behavior: "smooth" }), 300); });
  $("obName")?.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") nextOnboarding(); });
  if ($("obPad")) {
    const wait = () => {
      if (!$("obPad")) return;
      const gp = [...navigator.getGamepads()].find(Boolean);
      if (gp && gp.buttons.some((b) => b.pressed)) { $("obPad").textContent = `🎮 ${gp.id.replace(/\(.*?\)/g, "").trim() || "Controle"} funcionando!`; $("obPad").classList.add("ok"); Sfx.play("success"); return; }
      requestAnimationFrame(wait);
    };
    wait();
  }
  Sfx.play("move");
}

async function nextOnboarding() {
  if (state.ob.step === 0 && $("obName")?.value.trim()) {
    await api().set_profile({ name: $("obName").value.trim() });
    refreshProfileButton();
  }
  if (state.ob.step >= OB_STEPS.length - 1) return finishOnboarding();
  state.ob.step++;
  paintOnboarding();
}

async function finishOnboarding() {
  $("onboarding").classList.remove("show");
  popLayer("onboarding");
  await setPref("onboarding_done", true);
}

// ---------------------------------------------------------------- eventos ao vivo

window.onFreeGames = (games) => {
  $("freeDot").hidden = false;
  toast(`Grátis na Epic: ${games.map((g) => g.title).join(", ")}`);
};
window.onChallenge = (item) => { refreshProfileButton(); toast(`${item.name} · +${item.xp} XP`); };
window.onSaveBackup = ({ name }) => toast(`Backup do save de ${name} feito`);

// ---------------------------------------------------------------- ligações

function bindFeatures() {
  bindPrefControls();
  bindNotes();
  bindCrop();
  bindTrim();
  $("randomBtn").addEventListener("click", openRandom);
  $("randomClose").addEventListener("click", closeRandom);
  $("randomAgain").addEventListener("click", () => { closeRandom(); openRandom(); });
  $("randomPlay").addEventListener("click", playRandom);
  $("freeBtn").addEventListener("click", openFreeGames);
  $("gpMove").addEventListener("click", moveGame);
  $("uninstallBtn").addEventListener("click", uninstallGame);
  $("gpSavesFolder").addEventListener("click", () => api().open_saves_folder(state.pageGame));
  $("savesOpen").addEventListener("click", () => api().open_saves_folder(null));
  $("igdbSave").addEventListener("click", saveIgdb);
  $("igdbPage").addEventListener("click", () => api().open_url("https://dev.twitch.tv/console/apps/create"));
  $("discordDev").addEventListener("click", () => api().open_url("https://discord.com/developers/applications"));
  $("padRefresh").addEventListener("click", () => refreshPad(true));
  $("logsOpen").addEventListener("click", () => api().open_logs());
  $("updateCheck").addEventListener("click", checkOrInstallUpdate);
  $("obAgain").addEventListener("click", () => { closeSettings(); maybeOnboarding(true); });
  $("obNext").addEventListener("click", nextOnboarding);
  $("obSkip").addEventListener("click", finishOnboarding);
  $("lbTools").addEventListener("click", (e) => { const b = e.target.closest("[data-tool]"); if (b) { e.stopPropagation(); lightboxTool(b.dataset.tool); } });
  for (const id of ["igdbId", "igdbSecret"]) $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveIgdb(); } });
  document.addEventListener("keydown", (e) => {
    if (topLayer() || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.ctrlKey || e.altKey) return;
    if (e.key === "s" || e.key === "S") { e.preventDefault(); openRandom(); }
  });
}

window.addEventListener("pywebviewready", async () => {
  bindFeatures();
  await loadPrefs();
  setTimeout(() => maybeOnboarding(), 1200);
  refreshPad();
  setInterval(refreshPad, 60000);
  if (state.prefs.update_auto) {
    setTimeout(async () => {
      const u = await api().check_update();
      if (u.available) toast(`GameDock ${u.version} disponível — Configurações → Sobre → Atualizar agora`);
    }, 8000);
  }
});
