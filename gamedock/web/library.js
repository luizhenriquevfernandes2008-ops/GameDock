// GameDock — biblioteca (v0.7): pastas de jogos, sugestões, "Talvez seja jogo", limpar desinstalados,
// armazenamento, galeria de capas do SteamGridDB, aviso de jogo novo e "ouvir junto" na call.
// Usa o estado e as funções de app.js/pages.js/features.js (mesmo escopo global). Nomes daqui começam
// com lib/st/cg/listen para não colidir com os outros scripts (um nome repetido derruba o script inteiro).

// (o registro da galeria no controle fica no pywebviewready: padnav.js carrega depois deste arquivo)

// ---------------------------------------------------------------- Configurações → Biblioteca

async function libLoad() {
  libRender(await api().library_settings());
}

function libRender(d) {
  $("libFolders").innerHTML = d.folders.length
    ? d.folders.map((f) => `<div class="emu-folder"><code title="${esc(f)}">${esc(f)}</code>
        <button type="button" class="link-btn" data-lib-remove="${esc(f)}">remover</button></div>`).join("")
    : `<p class="settings-hint" style="margin:0">Nenhuma pasta ainda.</p>`;

  $("libSuggestions").innerHTML = d.suggestions.map((s) => `
    <div class="lib-card">
      <div><strong>${esc(s.path)}</strong><small>${s.games} jogos da sua biblioteca estão aqui. Adicione para achar também os que não têm atalho.</small></div>
      <button type="button" class="ghost-btn" data-lib-add="${esc(s.path)}">Adicionar</button>
      <button type="button" class="link-btn" data-lib-dismiss="${esc(s.path)}">agora não</button>
    </div>`).join("");

  $("libCandidates").innerHTML = d.candidates.length ? `
    <p style="margin:16px 0 8px">Talvez seja jogo <small class="settings-hint">— atalhos que quase passaram no detector</small></p>
    ${d.candidates.map((c) => `
      <div class="lib-card">
        <div><strong>${esc(c.name)}</strong><small title="${esc(c.exe)}">${esc(c.exe)}</small>
          ${c.reasons.length ? `<small>${esc(c.reasons.join(" · "))}</small>` : ""}</div>
        <button type="button" class="ghost-btn" data-lib-game="${esc(c.exe)}">É jogo</button>
        <button type="button" class="link-btn" data-lib-notgame="${esc(c.exe)}">não é</button>
      </div>`).join("")}` : "";

  $("libPurgeRow").hidden = !d.missing;
  $("libPurgeText").textContent = `${d.missing} jogo(s) desinstalado(s) guardado(s) na biblioteca (só aparecem se voltarem a ser instalados).`;

  const after = async (promise, msg) => {
    $("libStatus").textContent = "Procurando jogos…";
    const r = await promise;
    if (!r) { $("libStatus").textContent = ""; return; }
    libRender(r);
    const fresh = r.report?.new || [];
    $("libStatus").textContent = fresh.length ? `Novos: ${fresh.join(", ")}` : (msg || "");
    if (fresh.length) { Sfx.play("success"); loadGames(current()?.id); }
  };
  const on = (attr, fn) => $("settings").querySelectorAll(`[${attr}]`).forEach((b) =>
    b.addEventListener("click", () => fn(b.getAttribute(attr))));
  on("data-lib-remove", (f) => after(api().remove_scan_folder(f), "Pasta removida."));
  on("data-lib-add", (f) => after(api().add_scan_folder(f), "Pasta adicionada."));
  on("data-lib-dismiss", async (f) => libRender(await api().dismiss_folder_suggestion(f)));
  on("data-lib-game", (exe) => after(api().decide_candidate(exe, true), "Marcado como jogo."));
  on("data-lib-notgame", async (exe) => libRender(await api().decide_candidate(exe, false)));
}

// ---------------------------------------------------------------- Configurações → Armazenamento

async function renderStorage() {
  $("stGames").innerHTML = `<p class="settings-hint">Calculando o tamanho dos jogos…</p>`;
  const d = await api().storage_overview();
  $("stDrives").innerHTML = d.drives.map((dr) => {
    const used = dr.total - dr.free;
    const pct = dr.total ? Math.round((used / dr.total) * 100) : 0;
    const games = d.games.filter((g) => g.drive === dr.drive).reduce((a, g) => a + g.size, 0);
    return `<div class="st-drive">
      <div class="st-drive-head"><strong>${esc(dr.drive)}</strong><span>${fmtBytes(dr.free)} livres de ${fmtBytes(dr.total)}</span></div>
      <i class="st-bar"><b style="width:${pct}%"></b><em style="width:${dr.total ? (games / dr.total) * 100 : 0}%"></em></i>
      <small>${fmtBytes(games)} em jogos</small></div>`;
  }).join("");
  $("stGames").innerHTML = d.games.length ? d.games.map((g) => `
    <div class="st-row">
      <span class="st-name">${esc(g.name)}<small>${esc(g.path)}</small></span>
      <span class="st-size">${fmtBytes(g.size)}</span>
      <button type="button" class="round-btn uninstall-btn small" data-st-uninst="${esc(g.id)}" data-name="${esc(g.name)}" title="Segure para desinstalar">
        <svg viewBox="0 0 24 24"><path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span>Desinstalando…</span>
      </button>
    </div>`).join("") : `<p class="settings-hint">Nenhum jogo instalado.</p>`;
  $("stGames").querySelectorAll("[data-st-uninst]").forEach((b) =>
    bindUninstallHold(b, () => ({ id: b.dataset.stUninst, name: b.dataset.name })));
}

// Delete / LT segurado em cima do botão de desinstalar da lista (Configurações abertas)
function libSettingsKeys() {
  const layer = state.layers.find((l) => l.name === "settings");
  if (!layer) return;
  layer.keys = { ...(layer.keys || {}), Delete: () => {
    const el = document.activeElement;
    if (el?.dataset?.stUninst) startUninstallHold(keysDown.has("Delete") ? "key" : "pad", el.dataset.stUninst, el.dataset.name, el);
  } };
}

const _loadExtraSettingsLib = loadExtraSettings;
loadExtraSettings = async function (settings) {
  await _loadExtraSettingsLib(settings);
  libSettingsKeys();
  libLoad();
  renderStorage();
};

// ---------------------------------------------------------------- galeria de capas

let cgGame = null;
async function openCoverGallery() {
  const id = state.pageGame;
  cgGame = state.all.find((g) => g.id === id);
  if (!cgGame) return;
  $("cgTitle").textContent = cgGame.name;
  $("cgGrid").innerHTML = `<p class="settings-hint">Buscando capas no SteamGridDB…</p>`;
  $("coverGallery").classList.add("show");
  pushLayer("coverGallery", closeCoverGallery);
  Sfx.play("open");
  const options = await api().cover_options(id);
  if (cgGame?.id !== id) return;
  $("cgGrid").innerHTML = options.length
    ? options.map((o, i) => `<button type="button" class="cg-item" data-cg="${i}" title="${o.author ? "por " + esc(o.author) : ""}">
        <img src="${esc(o.thumb)}" alt="" loading="lazy"></button>`).join("")
    : `<p class="settings-hint">Nenhuma capa encontrada. Configure a chave do SteamGridDB nas Configurações ou escolha um arquivo do PC.</p>`;
  $("cgGrid").querySelectorAll("[data-cg]").forEach((b) => b.addEventListener("click", async () => {
    b.classList.add("loading");
    const art = await api().set_cover_url(id, options[Number(b.dataset.cg)].url);
    if (!art) { toast("Não consegui baixar essa capa.", true); b.classList.remove("loading"); return; }
    cgApply(id, art);
    closeCoverGallery();
    toast("Capa trocada.");
  }));
}

function cgApply(id, art) {
  state.art[id] = art;
  delete state.accent[id];
  const game = state.all.find((g) => g.id === id);
  if (!game) return;
  $("gpCover").replaceChildren(coverElement(game));
  paintTile(game);
  if (current()?.id === id) showHero(current());
  $("gpResetCover").hidden = false;
  Sfx.play("success");
}

function closeCoverGallery() {
  popLayer("coverGallery");
  $("coverGallery").classList.remove("show");
  cgGame = null;
  Sfx.play("close");
}

// ---------------------------------------------------------------- jogo novo detectado sozinho

window.onLibraryChanged = async (report) => {
  await loadGames(current()?.id);
  if (report.new?.length) {
    toast(`Novo na biblioteca: ${report.new.join(", ")}`, false, 6000);
    Sfx.play("success");
  } else if (report.missing?.length) {
    toast(`Saiu da biblioteca (desinstalado): ${report.missing.join(", ")}`, false, 6000);
  }
  if (state.settingsOpen) libLoad();
};

// ---------------------------------------------------------------- ouvir junto na call

function listenRender(st) {
  $("listenBlock").hidden = !st.inCall;
  if (!st.inCall) return;
  $("listenNote").textContent = st.hosting ? "transmitindo" : st.following ? "ouvindo junto" : "";
  $("listenHosts").innerHTML = st.hosts.map((h) => `
    <div class="lib-card">
      ${h.art ? `<img class="listen-art" src="${esc(h.art)}" alt="">` : ""}
      <div><strong>${esc(h.host.name || "Alguém")} está ouvindo</strong><small>${esc(h.title)}${h.artist ? " · " + esc(h.artist) : ""}</small></div>
      ${st.following === h.host.id
        ? `<button type="button" class="ghost-btn" data-listen-stop>Parar de ouvir</button>`
        : `<button type="button" class="ghost-btn" data-listen-follow="${esc(h.host.id)}">Ouvir junto</button>`}
    </div>`).join("") || `<p class="settings-hint" style="margin:0">Ninguém da call está transmitindo agora.</p>`;
  $("listenListeners").textContent = st.hosting
    ? (st.listeners.length ? `Ouvindo com você: ${st.listeners.map((u) => u.name).join(", ")}` : "Ninguém ouvindo ainda")
    : "";
  $("listenHostBtn").textContent = st.hosting ? "Parar transmissão" : "Transmitir minha música";
  $("listenHosts").querySelectorAll("[data-listen-follow]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api().listen_follow(b.dataset.listenFollow);
    if (!r.ok) toast(r.error, true);
  }));
  $("listenHosts").querySelectorAll("[data-listen-stop]").forEach((b) => b.addEventListener("click", async () =>
    listenRender(await api().listen_unfollow())));
}
window.onListen = listenRender;

window.addEventListener("pywebviewready", () => {
  if (typeof PAD_ROOTS !== "undefined") PAD_ROOTS.coverGallery = "coverGallery";
  $("libAddFolder").addEventListener("click", async () => {
    $("libStatus").textContent = "";
    const r = await api().pick_scan_folder();
    if (r) { libRender(r); const n = r.report?.new || []; $("libStatus").textContent = n.length ? `Novos: ${n.join(", ")}` : "Pasta adicionada."; if (n.length) loadGames(current()?.id); }
  });
  $("libPurge").addEventListener("click", async () => {
    const r = await api().purge_missing();
    libRender(r);
    toast(`${r.removed} jogo(s) desinstalado(s) tirado(s) da biblioteca.`);
  });
  $("cgFile").addEventListener("click", async () => {
    const id = cgGame?.id;
    closeCoverGallery();
    if (id) changeCover(false);
  });
  $("cgClose").addEventListener("click", closeCoverGallery);
  $("listenHostBtn").addEventListener("click", async () => {
    const st = await api().listen_state();
    if (st.hosting) return listenRender(await api().listen_stop());
    const r = await api().listen_host();
    if (!r.ok) toast(r.error, true);
  });
  setTimeout(async () => { try { listenRender(await api().listen_state()); } catch { /* sem Discord */ } }, 1500);
});
