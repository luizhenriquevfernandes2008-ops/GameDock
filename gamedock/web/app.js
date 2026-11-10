// GameDock — interface. Os dados vêm do Python via window.pywebview.api.

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const SOURCES = {
  steam: "Steam", epic: "Epic Games", gog: "GOG", ubisoft: "Ubisoft Connect", riot: "Riot Games",
  google: "Google Play Games", battlenet: "Battle.net", ea: "EA app", folder: "Pasta local", local: "Instalado no PC", manual: "Manual",
  ps3: "PlayStation 3", ps2: "PlayStation 2", ps1: "PlayStation", nes: "NES",
};
const STAR = '<svg viewBox="0 0 24 24"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z"/></svg>';

const state = {
  all: [],        // todos os jogos
  games: [],      // os que aparecem na prateleira (aba + busca)
  tab: "all",
  query: "",
  layers: [],     // painéis abertos, o último fica por cima (Esc/B fecha ele)
  index: 0,
  art: {},        // id -> {cover, hero, logo, icon}
  accent: {},     // id -> [h, s, l]
  heroFront: "heroA",
  launching: false,
};

window.addEventListener("pywebviewready", init);

async function init() {
  tickClock();
  setInterval(tickClock, 15000);
  bindControls();
  bindPages();
  renderHints(false);
  const settings = await api().get_settings();
  state.sound = settings.sound;
  Sfx.configure(state.sound);
  if (state.sound.boot && !location.search.includes("wake=1")) Sfx.play("boot");
  await loadGames();
  refreshProfileButton();
  requestAnimationFrame(gamepadLoop);
  // procura jogos novos a cada abertura, em segundo plano (avisa só se achar algo),
  // mas só depois que as capas apareceram, para não disputar CPU com a primeira tela
  await state.artReady;
  scan({ silent: true });
}

// ---------------------------------------------------------------- dados

async function loadGames(keepId) {
  state.all = await api().list_games();
  $("empty").classList.toggle("show", state.all.length === 0);
  document.querySelector(".stage").style.visibility = state.all.length ? "" : "hidden";
  applyView(keepId);
  state.artReady = loadAllArt();
}

// aplica aba + busca à lista completa e redesenha a prateleira
function applyView(keepId = current()?.id) {
  state.games = filterGames(state.all, state.tab, state.query);
  renderTabs();
  renderShelf();
  $("shelfEmpty").classList.toggle("show", !state.games.length && state.all.length > 0);
  $("shelfEmpty").textContent = state.query ? `Nada encontrado para "${state.query}".` : "Nenhum jogo nesta aba.";
  document.querySelector(".stage").style.visibility = state.games.length ? "" : "hidden";
  if (!state.games.length) return;
  const i = keepId ? state.games.findIndex((g) => g.id === keepId) : 0;
  select(Math.max(0, i), { force: true });
}

async function loadAllArt() {
  // o jogo em destaque primeiro; depois os outros, alguns de cada vez (arte nova pode precisar ser baixada)
  const queue = state.all.filter((g) => !state.art[g.id]);
  queue.sort((a, b) => (b.id === current()?.id) - (a.id === current()?.id));
  const worker = async () => {
    while (queue.length) {
      const game = queue.shift();
      state.art[game.id] = await api().get_art(game.id);
      paintTile(game);
      if (current()?.id === game.id) showHero(game);
    }
  };
  await Promise.all(Array.from({ length: 4 }, worker));
}

const current = () => state.games[state.index];

// ---------------------------------------------------------------- prateleira

function renderShelf() {
  const shelf = $("shelf");
  shelf.innerHTML = "";
  state.games.forEach((game, i) => {
    const tile = document.createElement("button");
    tile.className = "tile" + (game.favorite ? " fav" : "");
    tile.style.setProperty("--i", Math.min(i, 14));
    tile.dataset.index = i;
    tile.innerHTML = `<div class="tile-art"></div><div class="tile-fav">${STAR}</div><div class="tile-name"></div>`;
    tile.querySelector(".tile-name").textContent = game.name;
    tile.addEventListener("click", () => select(i));
    tile.addEventListener("dblclick", () => play());
    shelf.appendChild(tile);
    paintTile(game);
  });
}

function paintTile(game) {
  const i = state.games.findIndex((g) => g.id === game.id);
  const tile = $("shelf").children[i];
  if (!tile) return;
  tile.querySelector(".tile-art").replaceChildren(coverElement(game));
}

function coverElement(game) {
  const art = state.art[game.id];
  if (art?.cover) {
    const img = new Image();
    img.className = "cover";
    img.src = art.cover;
    img.style.objectPosition = art.coverFocus || "center";
    if (!art.coverLogo) return img;
    // arte horizontal (ex: Riot): recorte nos personagens + logo oficial por cima
    const composed = document.createElement("div");
    composed.className = "composed-cover";
    const logo = new Image();
    logo.className = "cover-logo";
    logo.src = art.logo;
    composed.append(img, logo);
    return composed;
  }
  // capa tipográfica gerada
  const gen = document.createElement("div");
  gen.className = "gen-cover";
  gen.style.setProperty("--h", hashHue(game.name));
  // o tamanho da fonte se ajusta à maior palavra, para nunca quebrar no meio ("CASTLEVA-NIA")
  gen.style.setProperty("--longest", Math.max(6, ...game.name.split(/\s+/).map((w) => w.length)));
  gen.innerHTML = `<small></small>${art?.icon ? `<img class="icon" src="${art.icon}">` : ""}<strong></strong>`;
  gen.querySelector("small").textContent = SOURCES[game.source] || game.source;
  gen.querySelector("strong").textContent = game.name;
  return gen;
}

function select(i, { force = false } = {}) {
  if (!state.games.length) return;
  const clamped = Math.max(0, Math.min(state.games.length - 1, i));
  if (clamped === state.index && !force) {
    if (clamped !== i) Sfx.play("edge");  // tentou passar do começo/fim da lista
    return;
  }
  if (!force) Sfx.play("move");
  i = clamped;
  state.index = i;
  const tiles = $("shelf").children;
  [...tiles].forEach((t, n) => t.classList.toggle("selected", n === i));
  // estilo PS5: o selecionado fica ancorado à esquerda
  const shelf = $("shelf");
  shelf.scrollTo({ left: Math.max(0, tiles[i].offsetLeft - tiles[0].offsetLeft) });
  showHero(current());
}

// ---------------------------------------------------------------- destaque

async function showHero(game) {
  const art = state.art[game.id] || {};

  const info = $("heroInfo");
  info.classList.remove("enter");
  void info.offsetWidth;           // reinicia a animação de entrada
  info.classList.add("enter");

  const logo = $("gameLogo");
  logo.classList.toggle("show", !!art.logo);
  if (art.logo) logo.src = art.logo;
  $("gameTitle").textContent = game.name;
  $("gameTitle").classList.toggle("hidden", !!art.logo);

  $("gameSource").textContent = SOURCES[game.source] || game.source;
  $("gameStats").textContent = statsText(game);
  $("gameLive").classList.toggle("show", !!game.running);
  renderTrophyChip(game);
  $("favBtn").classList.toggle("on", game.favorite);

  setBackground(art.hero || art.cover);
  setAccent(await accentFor(game));
}

function setBackground(src) {
  state.wantedHero = src;  // navegando rápido, só a última imagem pedida pode aparecer
  const front = $(state.heroFront);
  const backId = state.heroFront === "heroA" ? "heroB" : "heroA";
  const back = $(backId);
  $("heroGen").classList.toggle("show", !src);
  if (!src) {
    front.classList.remove("show");
    back.classList.remove("show");
    return;
  }
  if (front.dataset.src === src && front.classList.contains("show")) return;
  back.onload = () => {
    if (state.wantedHero !== src) return;
    back.classList.add("show");
    front.classList.remove("show");
    state.heroFront = backId;
  };
  back.dataset.src = src;  // .src vira URL absoluta; guardamos a original para comparar
  back.src = src;
}

function setAccent([h, s, l]) {
  const root = document.documentElement.style;
  root.setProperty("--ah", h);
  root.setProperty("--as", s + "%");
  root.setProperty("--al", l + "%");
}

async function accentFor(game) {
  if (state.accent[game.id]) return state.accent[game.id];
  const cover = state.art[game.id]?.cover;
  const color = cover ? await accentFromImage(cover) : [hashHue(game.name), 80, 64];
  if (state.art[game.id]) state.accent[game.id] = color;  // só guarda depois que a arte chegou
  return color;
}

// Escolhe a cor "marcante" da capa: agrupa pixels por matiz e pesa por saturação,
// ignorando quase-preto e quase-branco. Depois normaliza para ficar legível no escuro.
function accentFromImage(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const w = 40, h = 60;
      const ctx = Object.assign(document.createElement("canvas"), { width: w, height: h })
        .getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, w, h);
      const data = ctx.getImageData(0, 0, w, h).data;
      const buckets = Array.from({ length: 18 }, () => ({ weight: 0, h: 0, s: 0 }));
      for (let p = 0; p < data.length; p += 4) {
        const [hue, sat, lig] = rgbToHsl(data[p], data[p + 1], data[p + 2]);
        const weight = sat * sat * (1 - Math.abs(lig - 0.5) * 2);
        if (weight < 0.04) continue;
        const b = buckets[Math.floor(hue / 20) % 18];
        b.weight += weight; b.h += hue * weight; b.s += sat * weight;
      }
      const best = buckets.reduce((a, b) => (b.weight > a.weight ? b : a));
      if (best.weight < 3) return resolve([0, 0, 88]);  // capa sem cor: destaque branco
      const sat = Math.round(Math.min(90, Math.max(60, (best.s / best.weight) * 100)));
      resolve([Math.round(best.h / best.weight), sat, 64]);
    };
    img.onerror = () => resolve([265, 85, 66]);
    img.src = src;
  });
}

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}

function hashHue(text) {
  let hash = 0;
  for (const ch of text) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % 360;
}

function formatPlaytime(seconds) {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "";
  if (minutes < 60) return `${minutes} min jogados`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (hours >= 100 || !rest) return `${hours.toLocaleString("pt-BR")} h jogadas`;
  return `${hours} h ${rest} min jogadas`;
}

function statsText(game) {
  const played = formatPlaytime(game.playtime || 0);
  if (!played && !game.lastPlayed) return "Pronto para jogar";
  if (game.running) return played || "Sessão começando";
  return [played, game.lastPlayed && relativeTime(game.lastPlayed)].filter(Boolean).join(" · ");
}

function renderTrophyChip(game) {
  const chip = $("gameTrophies");
  const t = game.trophies || { total: 0 };
  chip.classList.toggle("hidden", !t.total);
  chip.classList.toggle("platinum", !!t.platinum);
  if (!t.total) return;
  $("gameTrophiesText").textContent = `${t.unlocked}/${t.total}`;
  $("gameTrophiesBar").style.width = `${(t.unlocked / t.total) * 100}%`;
}

function relativeTime(iso) {
  const rtf = new Intl.RelativeTimeFormat("pt-BR", { numeric: "auto" });
  const diff = (new Date(iso) - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, secs] of units) {
    if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
  }
  return "agora mesmo";
}

// ---------------------------------------------------------------- ações

async function play() {
  const game = current();
  if (!game || state.launching) return;
  state.launching = true;

  $("launchCover").replaceChildren(coverElement(game));
  $("launchName").textContent = game.name;
  $("launchOverlay").classList.add("show");
  Sfx.play("play");

  const result = await api().play(game.id);
  if (!result.ok) {
    $("launchOverlay").classList.remove("show");
    Sfx.play("error");
    toast(result.error, true);
    state.launching = false;
    return;
  }
  setTimeout(async () => {
    $("launchOverlay").classList.remove("show");
    state.launching = false;
    api().after_launch(game.id);
    const fresh = await api().list_games();      // atualiza contador de sessões
    const updated = fresh.find((g) => g.id === game.id);
    if (updated) Object.assign(game, updated);
    if (current()?.id === game.id) $("gameStats").textContent = statsText(game);
  }, 2600);
}

async function toggleFavorite() {
  const game = current();
  if (!game) return;
  game.favorite = await api().toggle_favorite(game.id);
  Sfx.play(game.favorite ? "favOn" : "favOff");
  if (state.tab === "fav") return applyView(game.favorite ? game.id : undefined);
  $("favBtn").classList.toggle("on", game.favorite);
  $("shelf").children[state.index].classList.toggle("fav", game.favorite);
  toast(game.favorite ? `${game.name} nos favoritos` : `${game.name} removido dos favoritos`);
}

async function scan({ silent = false } = {}) {
  const btn = $("scanBtn");
  if (btn.classList.contains("spin")) return;
  btn.classList.add("spin");
  try {
    const report = await api().scan();
    const before = state.all.map((g) => g.id).join();
    const fresh = await api().list_games();
    // só redesenha se a lista mudou, para não "piscar" a prateleira ao abrir
    if (fresh.map((g) => g.id).join() !== before) await loadGames(current()?.id);
    if (!silent || report.new.length) {
      Sfx.play("success");
      const parts = [`${report.total} jogos`];
      if (report.new.length) parts.push(`${report.new.length} novos`);
      if (report.missing.length) parts.push(`${report.missing.length} não encontrados`);
      toast(parts.join(" · "));
    }
  } finally {
    btn.classList.remove("spin");
  }
}

// ---------------------------------------------------------------- troféus

const TIERS = {
  platinum: { color: "#a9dcff", label: "Platina" },
  gold: { color: "#f5c542", label: "Ouro" },
  silver: { color: "#c9d2de", label: "Prata" },
  bronze: { color: "#d08a4c", label: "Bronze" },
};

async function openTrophies() {
  const game = current();
  if (!game || !game.trophies?.total) return;
  state.trophiesOpen = true;
  pushLayer("trophies", closeTrophies, { t: closeTrophies, ArrowDown: () => scrollLayer("tpList", 1), ArrowUp: () => scrollLayer("tpList", -1) });
  $("tpGame").textContent = game.name;
  $("tpList").innerHTML = '<p class="tp-loading">Buscando troféus...</p>';
  $("trophies").classList.add("show");
  Sfx.play("open");
  const data = await api().get_trophies(game.id);
  if (!state.trophiesOpen || current()?.id !== game.id) return;
  renderTrophies(game, data);
}

function closeTrophies() {
  popLayer("trophies");
  state.trophiesOpen = false;
  $("trophies").classList.remove("show");
  Sfx.play("close");
}

function renderTrophies(game, data) {
  const ratio = data.total ? data.unlocked / data.total : 0;
  $("tpPercent").textContent = `${Math.round(ratio * 100)}%`;
  $("tpRingFill").style.strokeDashoffset = 97.4 * (1 - ratio);
  const counts = { platinum: 0, gold: 0, silver: 0, bronze: 0 };
  data.trophies.forEach((t) => { if (t.unlocked) counts[t.tier]++; });
  $("tpTiers").innerHTML =
    `<span>${data.unlocked} de ${data.total}</span>` +
    ["gold", "silver", "bronze"].map((k) =>
      `<span style="--tier:${TIERS[k].color}"><i class="tier-dot"></i>${counts[k]} ${TIERS[k].label.toLowerCase()}</span>`).join("") +
    (data.platinum ? `<span style="--tier:${TIERS.platinum.color}"><i class="tier-dot"></i>Platina!</span>` : "");

  const list = $("tpList");
  list.innerHTML = "";
  if (!data.trophies.length) {
    list.innerHTML = '<p class="tp-empty">Esse jogo não tem troféus.</p>';
    return;
  }
  data.trophies.forEach((t, i) => {
    const secret = t.hidden && !t.unlocked;
    const item = document.createElement("div");
    item.className = "tp-item" + (t.unlocked ? "" : " locked");
    item.style.setProperty("--i", Math.min(i, 30));
    item.style.setProperty("--tier", TIERS[t.tier].color);
    const icon = t.unlocked ? t.icon : t.icon_gray || t.icon;
    item.innerHTML = `
      <div class="tp-icon">${secret ? '<span class="secret">?</span>' : icon ? `<img src="${icon}" alt="" loading="lazy">` : ""}</div>
      <div class="tp-info"><strong></strong><p></p></div>
      <div class="tp-side">
        <div><span class="rarity"><i class="tier-dot"></i>${t.percent != null ? t.percent.toFixed(1).replace(".", ",") + "%" : TIERS[t.tier].label}</span>
        ${t.unlocked && t.unlocked_at ? `<time>${new Date(t.unlocked_at * 1000).toLocaleDateString("pt-BR")}</time>` : ""}</div>
        ${t.screenshot ? `<div class="tp-shot" title="Ver captura"><img src="${t.screenshot}" alt="" loading="lazy"></div>` : ""}
      </div>`;
    item.querySelector("strong").textContent = secret ? "Troféu secreto" : t.name;
    item.querySelector("p").textContent = secret ? "Continue jogando para descobrir." : t.desc;
    item.querySelector(".tp-shot")?.addEventListener("click", () =>
      openLightbox([{ url: t.screenshot, caption: `${t.name} · ${game.name}` }], 0));
    list.appendChild(item);
  });
}


// ---------------------------------------------------------------- eventos ao vivo (vindos do Python)

async function refreshGameData() {
  const fresh = await api().list_games();
  const byId = Object.fromEntries(fresh.map((g) => [g.id, g]));
  state.all.forEach((g) => byId[g.id] && Object.assign(g, byId[g.id]));
  const game = current();
  if (game) {
    $("gameStats").textContent = statsText(game);
    $("gameLive").classList.toggle("show", !!game.running);
    renderTrophyChip(game);
  }
}

window.onRunning = (ids) => {
  state.running = ids;
  refreshGameData();
};
window.onLibraryChanged = () => refreshGameData();
window.onTrophy = ({ gameId, names, platinum }) => {
  refreshGameData();
  refreshProfileButton();
  if (state.trophiesOpen && current()?.id === gameId) openTrophies();
  if (names.length) toast(platinum ? `Platina! ${names.join(", ")}` : `Troféu: ${names.join(", ")}`);
};
// enquanto um jogo roda, o tempo jogado na tela vai subindo
setInterval(() => { if (state.running?.length) refreshGameData(); }, 30000);

// ---------------------------------------------------------------- configurações

async function openSettings() {
  const settings = await api().get_settings();
  state.keyPage = settings.steamgriddbKeyPage;
  $("sgdbKey").value = settings.steamgriddbKey || "";
  setStatus(settings.steamgriddbKey ? "SteamGridDB conectado." : "", "ok");
  $("trophyPopup").checked = settings.trophyPopup;
  $("trophyShot").checked = settings.trophyScreenshot;
  $("soundEnabled").checked = state.sound.enabled;
  $("soundBoot").checked = state.sound.boot;
  $("soundVolume").value = Math.round(state.sound.volume * 100);
  $("soundVolumeOut").textContent = $("soundVolume").value + "%";
  $("settings").classList.add("show");
  state.settingsOpen = true;
  pushLayer("settings", closeSettings, null, { form: true });
  loadExtraSettings(settings);
  Sfx.play("open");
}

function closeSettings() {
  popLayer("settings");
  $("settings").classList.remove("show");
  state.settingsOpen = false;
  Sfx.play("close");
}

function applySound(preview) {
  state.sound = {
    enabled: $("soundEnabled").checked,
    boot: $("soundBoot").checked,
    volume: $("soundVolume").value / 100,
  };
  $("soundVolumeOut").textContent = $("soundVolume").value + "%";
  Sfx.configure(state.sound);
  api().set_sound(state.sound);
  if (preview) Sfx.play(preview);
}

function toggleMute() {
  $("soundEnabled").checked = !state.sound.enabled;
  applySound();
  if (state.sound.enabled) Sfx.play("success");
  toast(state.sound.enabled ? "Sons ligados" : "Sons desligados (M para religar)");
}

function setStatus(text, kind = "") {
  const el = $("sgdbStatus");
  el.textContent = text;
  el.className = "settings-status " + kind;
}

async function saveKey(e) {
  e.preventDefault();
  const btn = $("sgdbSave");
  btn.disabled = true;
  setStatus("Verificando a chave...");
  const result = await api().set_steamgriddb_key($("sgdbKey").value);
  btn.disabled = false;
  if (!result.ok) {
    Sfx.play("error");
    return setStatus(result.error, "error");
  }
  Sfx.play("success");
  setStatus($("sgdbKey").value.trim() ? "Chave salva! Buscando capas em alta..." : "Chave removida.", "ok");
  // recarrega toda a arte, agora passando pelo SteamGridDB
  state.art = {};
  state.accent = {};
  await loadAllArt();
  setStatus($("sgdbKey").value.trim() ? "SteamGridDB conectado. Capas atualizadas." : "Chave removida.", "ok");
}

let toastTimer;
function toast(message, error = false, ms = 3200) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

function tickClock() {
  $("clock").textContent = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------- controles

function bindControls() {
  $("playBtn").addEventListener("click", play);
  $("favBtn").addEventListener("click", toggleFavorite);
  $("scanBtn").addEventListener("click", () => scan());
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsClose").addEventListener("click", closeSettings);
  $("settings").addEventListener("click", (e) => { if (e.target.id === "settings") closeSettings(); });
  $("settingsForm").addEventListener("submit", saveKey);
  $("sgdbGetKey").addEventListener("click", () => api().open_url(state.keyPage));
  const saveTrophyOptions = () => api().set_trophy_options($("trophyPopup").checked, $("trophyShot").checked);
  $("trophyPopup").addEventListener("change", saveTrophyOptions);
  $("trophyShot").addEventListener("change", saveTrophyOptions);
  $("trophyTest").addEventListener("click", () => api().test_trophy_popup());
  $("gameTrophies").addEventListener("click", openTrophies);
  $("trophiesClose").addEventListener("click", closeTrophies);
  $("trophies").addEventListener("click", (e) => { if (e.target.id === "trophies") closeTrophies(); });
  $("soundEnabled").addEventListener("change", () => applySound("success"));
  $("soundBoot").addEventListener("change", () => applySound($("soundBoot").checked ? "boot" : null));
  $("soundVolume").addEventListener("input", () => applySound());
  $("soundVolume").addEventListener("change", () => Sfx.play("move"));  // prévia ao soltar o controle
  $("sgdbShow").addEventListener("click", () => {
    const input = $("sgdbKey");
    input.type = input.type === "password" ? "text" : "password";
    $("sgdbShow").textContent = input.type === "password" ? "Mostrar" : "Ocultar";
  });
  $("emptyScan").addEventListener("click", () => scan());
  $("winMin").addEventListener("click", () => api().minimize());
  $("winMax").addEventListener("click", () => api().toggle_maximize());
  $("winClose").addEventListener("click", () => api().close());
  document.querySelectorAll(".resize-edges i").forEach((edge) => edge.addEventListener("mousedown", (e) => {
    if (e.button === 0) { e.preventDefault(); api().begin_resize(edge.dataset.edge); }
  }));
  document.querySelector(".brand").addEventListener("dblclick", () => api().toggle_maximize());
  document.querySelector(".drag-spacer").addEventListener("dblclick", () => api().toggle_maximize());

  document.addEventListener("keydown", (e) => {
    if (e.target.id === "searchInput") return;  // a busca cuida das próprias teclas
    handleInput(e.key, e);
  });

  let wheelLock = 0;
  $("shelf").addEventListener("wheel", (e) => {
    e.preventDefault();
    if (Date.now() < wheelLock) return;
    wheelLock = Date.now() + 120;
    select(state.index + (e.deltaY + e.deltaX > 0 ? 1 : -1));
  }, { passive: false });

  window.addEventListener("gamepadconnected", () => { state.gamepad = true; renderHints(true); });
  window.addEventListener("gamepaddisconnected", () => { state.gamepad = false; renderHints(false); });
}

// ---------------------------------------------------------------- camadas e entrada

function pushLayer(name, close, keys = null, opts = {}) {
  popLayer(name);
  state.layers.push({ name, close, keys, opts });
  renderHints(state.gamepad);
}

function popLayer(name) {
  state.layers = state.layers.filter((l) => l.name !== name);
  renderHints(state.gamepad);
}

const topLayer = () => state.layers[state.layers.length - 1];

function scrollLayer(id, dir) {
  $(id)?.scrollBy({ top: dir * 160, behavior: "smooth" });
}

// teclado e controle chegam aqui com o mesmo "nome de tecla"
function handleInput(key, e) {
  const lower = key.length === 1 ? key.toLowerCase() : key;
  const typing = e && ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName);
  const top = topLayer();
  if (top) {
    if (key === "Escape") {
      e?.preventDefault();
      if (typing) e.target.blur();
      return top.close();
    }
    if (typing) return;
    const fn = top.keys?.[key] || top.keys?.[lower];
    if (fn) { e?.preventDefault(); fn(); }
    return;
  }
  if (state.launching || typing) return;
  if ((e?.ctrlKey && lower === "f") || key === "/") { e?.preventDefault(); return openSearch(); }
  if (e?.ctrlKey || e?.altKey) return;
  const actions = {
    ArrowRight: () => select(state.index + 1),
    ArrowLeft: () => select(state.index - 1),
    ArrowDown: openGamePage,
    Home: () => select(0),
    End: () => select(state.games.length - 1),
    Enter: play,
    Escape: () => state.query && clearSearch(),
    f: toggleFavorite,
    r: () => scan(),
    m: toggleMute,
    t: openTrophies,
    g: openGallery,
    p: openProfile,
    w: () => openRecap("week", 0),
    q: () => switchTab(-1),
    e: () => switchTab(1),
    Menu: openSettings,
    F11: () => api().toggle_fullscreen(),
    Delete: () => !e?.repeat && window.startUninstallHold?.(e ? "key" : "pad"),
  };
  const action = actions[key] || actions[lower];
  if (action) { e?.preventDefault(); action(); }
}

function renderHints(gamepad) {
  const layer = topLayer()?.name;
  const k = (key, label, cls = "") => `<span><kbd class="${cls}">${key}</kbd> ${label}</span>`;
  let html;
  if (layer) {
    const extra = layer === "lightbox" ? (gamepad ? k("◀▶", "Navegar") : k("←→", "Navegar")) :
                  ["gamePage", "profile", "gallery", "trophies"].includes(layer) ? (gamepad ? k("▲▼", "Rolar") : k("↑↓", "Rolar")) : "";
    html = extra + (gamepad ? k("B", "Voltar", "pad b") : k("Esc", "Voltar"));
  } else if (gamepad) {
    html = k("◀▶", "Navegar") + k("A", "Jogar", "pad a") + k("▼", "Detalhes") + k("X", "Troféus", "pad x") +
           k("Y", "Favoritar", "pad y") + k("LB RB", "Abas") + k("View", "Perfil") + k("LT", "Desinstalar (segure)");
  } else {
    html = k("←→", "Navegar") + k("Enter", "Jogar") + k("↓", "Detalhes") + k("T", "Troféus") + k("G", "Capturas") +
           k("P", "Perfil") + k("Q E", "Abas") + k("Ctrl F", "Buscar") + k("M", "Sons") + k("Del", "Desinstalar (segure)");
  }
  $("hints").innerHTML = html;
}

// Controle (Xbox/PlayStation via Gamepad API): cada botão vira uma "tecla" e passa pelo handleInput.
const PAD_BUTTONS = { 0: "Enter", 1: "Escape", 2: "t", 3: "f", 4: "q", 5: "e", 6: "Delete", 8: "p", 9: "Menu",
                      12: "ArrowUp", 13: "ArrowDown", 14: "ArrowLeft", 15: "ArrowRight" };
const REPEATS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
const pad = { held: new Set(), nextRepeat: 0 };

function gamepadLoop(now) {
  const gp = [...navigator.getGamepads()].find(Boolean);
  if (gp && document.hasFocus()) {
    const pressed = new Set();
    for (const [i, key] of Object.entries(PAD_BUTTONS)) if (gp.buttons[i]?.pressed) pressed.add(key);
    const [x = 0, y = 0] = gp.axes;  // analógico esquerdo vale como direcional
    if (x > 0.5) pressed.add("ArrowRight"); else if (x < -0.5) pressed.add("ArrowLeft");
    if (y > 0.5) pressed.add("ArrowDown"); else if (y < -0.5) pressed.add("ArrowUp");
    for (const key of pressed) {
      const fresh = !pad.held.has(key);
      if (fresh || (REPEATS.has(key) && now >= pad.nextRepeat)) {
        handleInput(key);
        if (REPEATS.has(key)) pad.nextRepeat = now + (fresh ? 380 : 110);
      }
    }
    pad.held = pressed;
  }
  requestAnimationFrame(gamepadLoop);
}
