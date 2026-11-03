// GameDock — Downloads: busca nas fontes que o usuário adicionou (formato do Hydra), fila de downloads e fontes.
// O Python faz o trabalho (sources.py / downloads.py); aqui é só a tela.

const store = { tab: "search", results: [], art: {}, dir: "", group: null, folder: null, open: false, lastJobs: "" };

const fmtEta = (s) => s == null || !isFinite(s) ? "" : s >= 86400 ? `${Math.floor(s / 86400)} d ${Math.floor(s % 86400 / 3600)} h`
  : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min` : s >= 60 ? `${Math.floor(s / 60)} min` : `${Math.max(1, Math.round(s))} s`;
const fmtUploadDate = (d) => d ? new Date(d + "T12:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" }) : "";
const hostOf = (uri) => { try { return new URL(uri).hostname.replace(/^www\./, ""); } catch { return ""; } };
const STATUS_TEXT = { queued: "Na fila", metadata: "Procurando pares…", downloading: "Baixando", paused: "Pausado",
                      error: "Erro", done: "Concluído", extracting: "Extraindo…" };

async function openStore(tab) {
  store.open = true;
  showPage("storePage", "store", {});
  const page = $("storePage");
  if (!page.dataset.bound) bindStore();
  await refreshStoreMeta();
  switchStoreTab(tab || (store.tab === "search" && !(await api().sources_list()).sources.length ? "sources" : store.tab));
  if (store.tab === "search") setTimeout(() => $("stQuery").focus(), 60);
}
window.openStore = openStore;

function bindStore() {
  $("storePage").dataset.bound = "1";
  $("stTabs").addEventListener("click", (e) => { const b = e.target.closest("[data-st]"); if (b) { Sfx.play("move"); switchStoreTab(b.dataset.st); } });
  $("stGo").addEventListener("click", runSearch);
  $("stQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runSearch(); } });
  $("stSourceAdd").addEventListener("click", addSource);
  $("stSourceUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addSource(); } });
  $("stSourcesRefresh").addEventListener("click", async () => {
    $("stSourcesRefresh").textContent = "Atualizando…";
    renderSources(await api().sources_refresh());
    $("stSourcesRefresh").textContent = "Atualizar todas";
  });
  $("stDirPick").addEventListener("click", async () => {
    const dir = await api().download_choose_folder();
    if (dir) renderStoreSettings(await api().downloads_settings({ dir }));
  });
  $("stSeed").addEventListener("change", async (e) => renderStoreSettings(await api().downloads_settings({ seed: e.target.checked })));
  $("stLimit").addEventListener("change", async (e) => renderStoreSettings(await api().downloads_settings({ limit: +e.target.value })));
  $("stModalClose").addEventListener("click", closeStoreModal);
  $("stModal").addEventListener("click", (e) => { if (e.target.id === "stModal") closeStoreModal(); });
  $("stModalDirPick").addEventListener("click", async () => {
    const dir = await api().download_choose_folder();
    if (dir) { store.folder = dir; renderModalDest(); }
  });
  $("stJobs").addEventListener("click", onJobClick);
  $("stResults").addEventListener("click", (e) => { const c = e.target.closest("[data-group]"); if (c) openStoreModal(store.results[+c.dataset.group]); });
  $("stOptions").addEventListener("click", onOptionClick);
}

function switchStoreTab(tab) {
  store.tab = tab;
  document.querySelectorAll("#stTabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.st === tab));
  document.querySelectorAll("#storePage .st-pane").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  $("stTitle").textContent = { search: "Buscar jogos", queue: "Seus downloads", sources: "Fontes" }[tab];
  if (tab === "queue") refreshJobs(true);
  if (tab === "sources") api().sources_list().then(renderSources);
}

async function refreshStoreMeta() {
  const [src, dl] = await Promise.all([api().sources_list(), api().downloads_list()]);
  renderSources(src);
  renderStoreSettings(dl);
  renderJobs(dl.jobs);
}

// ---------------------------------------------------------------- busca

async function runSearch() {
  const q = $("stQuery").value.trim();
  if (!q) return;
  $("stSearchEmpty").textContent = "Buscando…";
  $("stResults").innerHTML = "";
  store.results = await api().store_search(q);
  const total = (await api().sources_list()).sources.length;
  $("stSearchEmpty").textContent = !total ? "Nenhuma fonte adicionada. Vá na aba Fontes."
    : store.results.length ? `${store.results.length} jogo${store.results.length > 1 ? "s" : ""} encontrado${store.results.length > 1 ? "s" : ""}` : `Nada encontrado para "${q}".`;
  $("stResults").innerHTML = store.results.map((g, i) => `
    <button class="st-card" data-group="${i}" data-key="${esc(g.key)}">
      <div class="st-cover" style="--h:${hashHue(g.name)}"><span>${esc(g.name)}</span></div>
      <strong>${esc(g.name)}</strong>
      <small>${g.options.length} opç${g.options.length > 1 ? "ões" : "ão"} · ${esc(g.sources.join(", "))}</small>
      ${g.owned ? `<em class="st-owned">Na biblioteca</em>` : ""}
    </button>`).join("");
  store.results.forEach((g) => loadStoreArt(g));
}

async function loadStoreArt(group) {
  if (!(group.key in store.art)) store.art[group.key] = await api().store_art(group.name);
  const art = store.art[group.key];
  const card = document.querySelector(`.st-card[data-key="${CSS.escape(group.key)}"] .st-cover`);
  if (!art?.cover || !card) return;
  const img = new Image();
  img.onload = () => { card.replaceChildren(img); card.classList.add("art"); };
  img.onerror = () => { if (art.header && img.src !== art.header) img.src = art.header; };   // jogo sem capa vertical
  img.src = art.cover;
  img.alt = "";
}

// ---------------------------------------------------------------- escolher a versão

async function openStoreModal(group) {
  store.group = group;
  store.folder = null;
  const art = store.art[group.key] || {};
  $("stModalHero").style.backgroundImage = art.hero ? `url("${art.hero}"), url("${art.header || ""}")` : "";
  $("stModalHero").style.setProperty("--h", hashHue(group.name));
  $("stModalName").textContent = group.name;
  $("stModalOwned").textContent = group.owned ? "Já está na sua biblioteca" : `${group.options.length} opç${group.options.length > 1 ? "ões" : "ão"} encontrada${group.options.length > 1 ? "s" : ""}`;
  $("stOptions").innerHTML = group.options.map((o, i) => `
    <div class="st-option">
      <div class="st-opt-info">
        <b title="${esc(o.title)}">${esc(o.title)}</b>
        <span>${esc(o.source)}${o.size ? ` · ${esc(o.size)}` : ""}${o.date ? ` · ${fmtUploadDate(o.date)}` : ""}</span>
      </div>
      <div class="st-opt-links">${o.uris.map((u, j) => {
        const torrent = /^magnet:/i.test(u) || /\.torrent($|\?)/i.test(u);
        return `<button class="ghost-btn ${torrent ? "torrent" : ""}" data-opt="${i}" data-uri="${j}" title="${esc(torrent ? "Torrent / magnet" : u)}">
          ${torrent ? "Torrent" : esc(hostOf(u) || "Direto")}</button>`;
      }).join("")}</div>
    </div>`).join("");
  renderModalDest();
  $("stModal").classList.add("show");
  pushLayer("storeModal", closeStoreModal, {});
}

async function renderModalDest() {
  const dir = store.folder || store.dir;
  $("stModalDir").textContent = dir;
  const d = await api().disk_free(dir);
  $("stModalDisk").textContent = `${fmtBytes(d.free)} livres em ${d.drive}`;
}

function closeStoreModal() {
  $("stModal").classList.remove("show");
  popLayer("storeModal");
}

async function onOptionClick(e) {
  const b = e.target.closest("[data-opt]");
  if (!b) return;
  const option = store.group.options[+b.dataset.opt];
  const uri = option.uris[+b.dataset.uri];
  b.disabled = true;
  const r = await api().download_start({ ...option, name: store.group.name }, uri, store.folder);
  b.disabled = false;
  if (!r.ok) return toast(r.error, true);
  Sfx.play("success");
  toast(`${store.group.name}: download iniciado`);
  closeStoreModal();
  switchStoreTab("queue");
}

// ---------------------------------------------------------------- fila

async function refreshJobs(force = false) {
  const dl = await api().downloads_list();
  renderJobs(dl.jobs, force);
}

function jobButtons(j) {
  const btn = (action, text, extra = "") => `<button class="ghost-btn ${extra}" data-job="${j.id}" data-action="${action}">${text}</button>`;
  if (["queued", "metadata", "downloading"].includes(j.status)) return btn("pause", "Pausar") + btn("cancel", "Cancelar", "danger");
  if (["paused", "error"].includes(j.status)) return btn("resume", j.status === "error" ? "Tentar de novo" : "Continuar") + btn("cancel", "Cancelar", "danger");
  if (j.status === "extracting") return "";
  let html = "";
  for (const s of j.setups || []) html += `<button class="ghost-btn accent" data-job="${j.id}" data-action="setup" data-arg="${esc(s)}" title="${esc(s)}">Instalar</button>`;
  if (j.canExtract && !j.extracted) html += btn("extract", "Extrair", (j.setups || []).length ? "" : "accent");
  if (j.extracted && j.canExtract) html += btn("clean", "Apagar compactados");
  html += btn("library", "Adicionar à biblioteca") + btn("open", "Abrir pasta") + btn("remove", "Tirar da lista");
  return html;
}

function renderJobs(jobs, force = false) {
  const active = jobs.filter((j) => ["queued", "metadata", "downloading", "extracting"].includes(j.status)).length;
  $("stQueueCount").textContent = jobs.length ? (active ? `${active} ativo${active > 1 ? "s" : ""}` : jobs.length) : "";
  if (!store.open || store.tab !== "queue") return;
  const sig = JSON.stringify(jobs);
  if (!force && sig === store.lastJobs) return;
  store.lastJobs = sig;
  if (!jobs.length) {
    $("stJobs").innerHTML = `<p class="shots-empty">Nenhum download ainda. Use a aba Buscar.</p>`;
    return;
  }
  const keepConfirm = document.querySelector("#stJobs [data-confirm]")?.dataset.job;
  $("stJobs").innerHTML = jobs.map((j) => {
    const pct = j.status === "extracting" ? (j.extract || 0) : j.total ? j.done / j.total : 0;
    const bits = [];
    if (j.status === "extracting") bits.push(`${Math.round(pct * 100)}%`);
    else if (j.total) bits.push(`${fmtBytes(j.done)} de ${fmtBytes(j.total)}`);
    else if (j.done) bits.push(fmtBytes(j.done));
    if (j.status === "downloading" && j.speed) bits.push(`${fmtBytes(j.speed)}/s`);
    if (j.status === "downloading" && j.eta) bits.push(`faltam ${fmtEta(j.eta)}`);
    if (j.kind === "torrent" && j.peers != null && j.status !== "done") bits.push(`${j.peers} par${j.peers === 1 ? "" : "es"}`);
    if (j.checking) bits.push("verificando arquivos");
    if (j.status === "done" && j.extracted) bits.push("extraído");
    return `
    <article class="st-job ${j.status}">
      <div class="st-job-cover" style="--h:${hashHue(j.name)}">${store.art[sourcesKey(j.name)]?.header ? `<img src="${esc(store.art[sourcesKey(j.name)].header)}" alt="">` : ""}</div>
      <div class="st-job-main">
        <header><b>${esc(j.name)}</b><span class="st-badge">${j.kind === "torrent" ? "Torrent" : "Direto"}</span>
          <span class="st-status">${STATUS_TEXT[j.status] || j.status}</span></header>
        <small class="st-job-title" title="${esc(j.title)}">${esc(j.source ? `${j.source} · ` : "")}${esc(j.title)}</small>
        <div class="st-bar ${j.status}"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>
        <div class="st-job-stats">${bits.map(esc).join(" · ")}</div>
        ${j.error ? `<p class="st-error">${esc(j.error)}</p>` : ""}
        <div class="st-job-actions">${jobButtons(j)}</div>
      </div>
    </article>`;
  }).join("");
  if (keepConfirm) armConfirm(document.querySelector(`#stJobs [data-job="${keepConfirm}"][data-action="cancel"]`));
}

const sourcesKey = (name) => name.toLowerCase().replace(/&/g, " and ").replace(/['’`]/g, "").replace(/[^a-z0-9]+/g, "");

function armConfirm(b) {
  if (!b) return;
  b.dataset.confirm = "1";
  b.textContent = "Apagar o que já baixou?";
  clearTimeout(store.confirmTimer);
  store.confirmTimer = setTimeout(() => { store.lastJobs = ""; refreshJobs(true); }, 4000);
}

async function onJobClick(e) {
  const b = e.target.closest("[data-action]");
  if (!b) return;
  const { job, action, arg } = b.dataset;
  if (action === "cancel" && !b.dataset.confirm) return armConfirm(b);   // dois cliques para cancelar
  b.disabled = true;
  const r = await api().download_action(job, action, arg || null);
  if (r && r.ok === false) toast(r.error, true);
  else if (action === "library") {
    toast(r.new?.length ? `Na biblioteca: ${r.new.join(", ")}` : "Pasta adicionada à biblioteca (nenhum jogo novo encontrado ainda)");
    window.loadGames && loadGames();
  } else if (action === "clean") toast(`${fmtBytes(r.freed)} liberados`);
  else if (action === "setup") toast("Instalador aberto. Depois de instalar, clique em Procurar jogos.");
  refreshJobs(true);
}

// ---------------------------------------------------------------- fontes e opções

function renderSources(data) {
  const list = data.sources || [];
  $("stSourceCount").textContent = list.length || "";
  $("stHint").textContent = list.length ? `${list.length} fonte${list.length > 1 ? "s" : ""} · ${data.total.toLocaleString("pt-BR")} downloads` : "";
  $("stSources").innerHTML = list.length ? list.map((s) => `
    <div class="st-source">
      <div><b>${esc(s.name)}</b><small>${s.count.toLocaleString("pt-BR")} downloads · atualizada ${relativeTime(new Date(s.updated * 1000).toISOString())}</small>
        <code title="${esc(s.url)}">${esc(s.url)}</code>
        ${s.error ? `<p class="st-error">Última atualização falhou: ${esc(s.error)}</p>` : ""}</div>
      <button class="ghost-btn danger" data-remove-source="${esc(s.id)}">Remover</button>
    </div>`).join("") : `<p class="shots-empty">Nenhuma fonte ainda.</p>`;
  $("stSources").querySelectorAll("[data-remove-source]").forEach((b) => b.addEventListener("click", async () => {
    if (!b.dataset.confirm) { b.dataset.confirm = "1"; b.textContent = "Confirmar"; return; }
    renderSources(await api().sources_remove(b.dataset.removeSource));
  }));
}

async function addSource() {
  const url = $("stSourceUrl").value.trim();
  if (!url) return;
  $("stSourceAdd").disabled = true;
  $("stSourceMsg").textContent = "Lendo a fonte…";
  $("stSourceMsg").className = "st-msg";
  const r = await api().sources_add(url);
  $("stSourceAdd").disabled = false;
  if (!r.ok) {
    $("stSourceMsg").textContent = r.error;
    $("stSourceMsg").className = "st-msg st-error";
    return;
  }
  $("stSourceUrl").value = "";
  $("stSourceMsg").textContent = `"${r.source.name}" adicionada: ${r.source.count.toLocaleString("pt-BR")} downloads.`;
  Sfx.play("success");
  renderSources(await api().sources_list());
}

async function renderStoreSettings(dl) {
  store.dir = dl.dir;
  $("stDir").value = dl.dir;
  $("stSeed").checked = dl.seed;
  $("stLimit").value = String(dl.limit);
  if ($("stLimit").value !== String(dl.limit)) $("stLimit").value = "0";
  const d = await api().disk_free(dl.dir);
  $("stDisk").textContent = `${fmtBytes(d.free)} livres em ${d.drive}`;
  $("stTools").innerHTML = [
    dl.torrent ? "Torrent: pronto." : `<span class="st-error">Torrent indisponível (libtorrent não instalado).</span>`,
    dl.sevenZip ? "7-Zip encontrado: extrai .rar e .7z." : "Sem 7-Zip: só .zip é extraído aqui (instale o 7-Zip para .rar/.7z).",
  ].join(" ");
}

// ---------------------------------------------------------------- barra de título

window.onDownloads = (summary) => {
  const ring = document.querySelector("#dlBtn .dl-ring");
  ring.toggleAttribute("hidden", !summary.active);   // SVG não tem a propriedade .hidden
  if (summary.active) ring.style.setProperty("--p", Math.round((summary.progress || 0) * 100));
  $("dlBtn").title = summary.active ? `Downloads: ${summary.active} ativo${summary.active > 1 ? "s" : ""} · ${fmtBytes(summary.speed)}/s (D)` : "Downloads (D)";
  if (store.open && store.tab === "queue" && $("storePage").classList.contains("show")) refreshJobs();
  else if (!$("storePage").classList.contains("show")) store.open = false;
};

window.addEventListener("pywebviewready", () => {
  $("dlBtn").addEventListener("click", () => openStore());
});
