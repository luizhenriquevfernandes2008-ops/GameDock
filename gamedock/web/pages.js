// GameDock — abas, busca, página do jogo, perfil, galeria de capturas e configurações extras.
// Usa o estado e as funções de app.js (os dois scripts compartilham o escopo global).

const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const fmtHours = (sec) => {
  const h = sec / 3600;
  return h >= 10 ? `${Math.round(h).toLocaleString("pt-BR")} h` : h >= 1 ? `${h.toFixed(1).replace(".", ",")} h` : `${Math.round(sec / 60)} min`;
};
const fmtDate = (unix) => new Date(unix * 1000).toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- abas e busca

function tabList() {
  const tabs = [{ id: "all", label: "Todos" }];
  if (state.all.some((g) => g.lastPlayed)) tabs.push({ id: "recent", label: "Recentes" });
  if (state.all.some((g) => g.favorite)) tabs.push({ id: "fav", label: "Favoritos" });
  for (const source of [...new Set(state.all.map((g) => g.source))]) tabs.push({ id: "src:" + source, label: SOURCES[source] || source });
  return tabs;
}

function filterGames(all, tab, query) {
  let list = all;
  if (tab === "recent") list = all.filter((g) => g.lastPlayed).sort((a, b) => b.lastPlayed.localeCompare(a.lastPlayed));
  else if (tab === "fav") list = all.filter((g) => g.favorite);
  else if (tab.startsWith("src:")) list = all.filter((g) => g.source === tab.slice(4));
  if (query) list = list.filter((g) => fold(g.name).includes(fold(query)));
  return list;
}

function renderTabs() {
  const tabs = tabList();
  if (!tabs.some((t) => t.id === state.tab)) state.tab = "all";
  $("tabs").innerHTML = tabs.map((t) => {
    const n = filterGames(state.all, t.id, "").length;
    return `<button class="tab ${t.id === state.tab ? "on" : ""}" data-tab="${t.id}">${esc(t.label)} <small>${n}</small></button>`;
  }).join("");
  $("tabs").querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
}

function setTab(id) {
  if (id === state.tab) return;
  state.tab = id;
  Sfx.play("move");
  applyView();
}

function switchTab(dir) {
  const tabs = tabList();
  const i = tabs.findIndex((t) => t.id === state.tab);
  const next = tabs[(i + dir + tabs.length) % tabs.length];
  setTab(next.id);
}

function openSearch() {
  $("search").classList.add("show");
  $("searchInput").focus();
  $("searchInput").select();
}

function clearSearch() {
  state.query = "";
  $("searchInput").value = "";
  $("search").classList.remove("show");
  applyView();
}

function bindSearch() {
  const input = $("searchInput");
  input.addEventListener("input", () => { state.query = input.value.trim(); applyView(state.games[0]?.id); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); clearSearch(); input.blur(); }
    if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); input.blur(); }  // mantém o filtro e volta a navegar
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); select(state.index + (e.key === "ArrowRight" ? 1 : -1)); }
  });
  input.addEventListener("blur", () => { if (!state.query) $("search").classList.remove("show"); });
  $("searchBtn").addEventListener("click", openSearch);
}

// ---------------------------------------------------------------- camada genérica de página

function showPage(id, name, keys) {
  $(id).classList.add("show");
  $(id).scrollTop = 0;
  pushLayer(name, () => hidePage(id, name), {
    ArrowDown: () => $(id).scrollBy({ top: 160, behavior: "smooth" }),
    ArrowUp: () => $(id).scrollBy({ top: -160, behavior: "smooth" }),
    ...keys,
  });
  Sfx.play("open");
}

function hidePage(id, name) {
  if (id === "gamePage") stopTrailer();
  $(id).classList.remove("show");
  popLayer(name);
  Sfx.play("close");
}

// ---------------------------------------------------------------- página do jogo

const STAR_PATH = "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z";
const fmtCount = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} mi` : n >= 1e3 ? `${Math.round(n / 1e3)} mil` : `${n}`;

async function openGamePage() {
  const game = current();
  if (!game) return;
  state.pageGame = game.id;
  const art = state.art[game.id] || {};
  stopTrailer();
  $("gpHeroImg").src = art.hero || art.cover || "";
  $("gpCover").replaceChildren(coverElement(game));
  $("gpLogo").classList.toggle("show", !!art.logo);
  if (art.logo) $("gpLogo").src = art.logo;
  $("gpName").textContent = game.name;
  $("gpName").classList.toggle("hidden", !!art.logo);
  $("gpFav").classList.toggle("on", game.favorite);
  $("gpTags").innerHTML = `<span>${esc(SOURCES[game.source] || game.source)}</span>`;
  for (const id of ["gpScores", "gpMedia", "gpFacts", "gpAbout", "gpTrophySummary"]) $(id).innerHTML = "";
  $("gpShort").textContent = "Carregando informações do jogo...";
  $("gpMore").hidden = true;
  $("gpAbout").classList.remove("open");
  $("gpTrophyBlock").hidden = !game.trophies?.total;
  $("gpSound").hidden = true;
  showPage("gamePage", "gamePage", {
    Enter: play,
    t: () => game.trophies?.total && openTrophies(),
    f: () => toggleFavorite().then(() => $("gpFav").classList.toggle("on", game.favorite)),
  });

  const [details, shots, info, trophies] = await Promise.all([
    api().get_game_details(game.id), api().list_captures(game.id), api().get_game_info(game.id),
    game.trophies?.total ? api().get_trophies(game.id) : Promise.resolve(null),
  ]);
  if (state.pageGame !== game.id) return;
  state.pageDetails = details;
  state.pageInfo = info;
  renderGameInfo(game, info, details);
  renderGameTiles(game, details);
  renderChart($("gpChart"), details.days);
  renderStars(details.rating);
  if (trophies) renderTrophySummary(trophies);
  renderShots($("gpShots"), shots.slice(0, 6), "Nenhuma captura ainda. Aperte a tecla de captura durante o jogo.");
  $("gpNameInput").value = details.customName || game.name;
  $("gpArgsRow").hidden = !details.canArgs;
  $("gpArgs").value = details.args || "";
  $("gpFolder").hidden = !details.installDir;
  $("gpResetCover").hidden = !details.customCover;
  $("gpStore").hidden = !info.storeUrl;
  $("gpNotGame").checked = !details.track;
  renderNews(game.id);
  // como no PS5: depois de um instante, a arte do topo vira o trailer (sem som)
  if (info.trailers?.length) state.trailerTimer = setTimeout(() => playTrailer(info.trailers[0], false), 1800);
}

function renderGameInfo(game, info, details) {
  const year = (info.released || "").match(/\d{4}/)?.[0];
  const tags = [SOURCES[game.source] || game.source, ...(info.genres || []).slice(0, 4), year].filter(Boolean);
  $("gpTags").innerHTML = tags.map((x) => `<span>${esc(x)}</span>`).join("") +
    (info.age >= 12 ? `<span class="age">${info.age}+</span>` : "");

  const scores = [];
  if (info.metacritic) {
    const kind = info.metacritic >= 75 ? "good" : info.metacritic >= 50 ? "mixed" : "bad";
    scores.push(`<div class="score"><small>Metacritic</small><b><i class="meta-badge ${kind}">${info.metacritic}</i></b><span>nota da crítica</span></div>`);
  }
  if (info.reviews) {
    scores.push(`<div class="score"><small>Jogadores</small><b>${info.reviews.percent}%</b>
      <span>${esc(info.reviews.label)} · ${fmtCount(info.reviews.count)} análises</span></div>`);
  }
  $("gpScores").innerHTML = scores.join("");

  $("gpShort").textContent = info.short || "Sem descrição disponível para este jogo.";
  $("gpAbout").textContent = info.about || "";
  $("gpMore").hidden = !info.about;
  $("gpMore").textContent = "Ler mais";

  const facts = [];
  if (info.subtitle) facts.push(["Tipo", info.subtitle]);
  if (info.developers?.length) facts.push(["Desenvolvedor", info.developers.join(", ")]);
  if (info.publishers?.length) facts.push(["Distribuidora", info.publishers.join(", ")]);
  if (info.released) facts.push(["Lançamento", info.released]);
  if (info.franchise) facts.push(["Franquia", info.franchise]);
  if (info.genres?.length > 4) facts.push(["Gêneros", info.genres.join(", ")]);
  facts.push(["Biblioteca", SOURCES[game.source] || game.source]);
  if (details.installDir) facts.push(["Instalado em", details.installDir.split(/[\\/]/).filter(Boolean).slice(-2).join("\\")]);
  $("gpFacts").innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

  const media = [];
  (info.trailers || []).forEach((tr, i) => media.push(
    `<div class="media-item trailer" data-trailer="${i}"><img src="${tr.thumb}" alt="" loading="lazy">
      <i><svg viewBox="0 0 24 24"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg></i><em>Trailer</em></div>`));
  (info.screenshots || []).forEach((s, i) => media.push(
    `<div class="media-item" data-shot="${i}"><img src="${s.thumb}" alt="" loading="lazy"></div>`));
  if (!media.length && state.art[game.id]?.hero) {
    media.push(`<div class="media-item" data-art="1"><img src="${state.art[game.id].hero}" alt=""></div>`);
  }
  $("gpMedia").innerHTML = media.join("");
  $("gpMedia").querySelectorAll("[data-shot]").forEach((el) => el.addEventListener("click", () =>
    openLightbox(info.screenshots.map((s, i) => ({ url: s.full, caption: `${game.name} · imagem ${i + 1} de ${info.screenshots.length}` })), +el.dataset.shot)));
  $("gpMedia").querySelectorAll("[data-trailer]").forEach((el) => el.addEventListener("click", () => {
    playTrailer(info.trailers[+el.dataset.trailer], true);
    $("gamePage").scrollTo({ top: 0, behavior: "smooth" });
  }));
  $("gpMedia").querySelector("[data-art]")?.addEventListener("click", () =>
    openLightbox([{ url: state.art[game.id].hero, caption: game.name }], 0));
}

async function renderNews(gameId) {
  $("gpNewsBlock").hidden = true;
  const news = await api().get_game_news(gameId);
  if (state.pageGame !== gameId || !news.length) return;
  $("gpNews").innerHTML = news.slice(0, 4).map((n, i) => `
    <button class="news-item" data-i="${i}">
      <small>${esc(n.feed || "Notícia")} · ${fmtDate(n.date)}</small>
      <strong>${esc(n.title)}</strong><p>${esc(n.text)}</p>
    </button>`).join("");
  $("gpNews").querySelectorAll(".news-item").forEach((b) => b.addEventListener("click", () => api().open_url(news[+b.dataset.i].url)));
  $("gpNewsBlock").hidden = false;
}

function renderGameTiles(game, d) {
  const tiles = [
    [fmtHours(d.playtime), "tempo total"],
    [d.sessions.toLocaleString("pt-BR"), d.sessions === 1 ? "sessão registrada" : "sessões registradas"],
    [d.longest ? fmtHours(d.longest) : "—", "sessão mais longa"],
    [d.average ? fmtHours(d.average) : "—", "média por sessão"],
  ];
  if (game.lastPlayed) tiles.push([relativeTime(game.lastPlayed), "última vez"]);
  $("gpTiles").innerHTML = tiles.map(([v, l]) =>
    `<div class="tile-stat"><b class="${/^[\d—]/.test(v) ? "" : "text"}">${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
}

function renderTrophySummary(data) {
  const ratio = data.total ? data.unlocked / data.total : 0;
  const unlocked = data.trophies.filter((t) => t.unlocked).sort((a, b) => (b.unlocked_at || 0) - (a.unlocked_at || 0));
  // sugestão: o troféu bloqueado (e não secreto) que mais jogadores já têm
  const next = data.trophies.filter((t) => !t.unlocked && !t.hidden).sort((a, b) => (b.percent || 0) - (a.percent || 0))[0];
  $("gpTrophySummary").innerHTML = `
    <div class="tp-ring"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15.5"/><circle class="fill" cx="18" cy="18" r="15.5"
      style="stroke-dashoffset:${97.4 * (1 - ratio)}"/></svg><b>${Math.round(ratio * 100)}%</b></div>
    <div>
      <div class="recent-icons">${unlocked.slice(0, 6).map((t) => `<img src="${t.icon}" alt="" title="${esc(t.name)}" style="--tier:${TIER_COLORS[t.tier]}">`).join("")}</div>
      <p>${data.unlocked} de ${data.total} troféus${data.platinum ? " · platina!" : ""}</p>
      ${next ? `<p>Próximo mais fácil: <b>${esc(next.name)}</b>${next.percent != null ? ` (${next.percent.toFixed(1).replace(".", ",")}% dos jogadores têm)` : ""}</p>` : ""}
    </div>`;
}

function renderStars(rating) {
  const el = $("gpStars");
  el.innerHTML = [1, 2, 3, 4, 5].map((n) =>
    `<button class="${rating >= n ? "on" : ""}" data-n="${n}" title="${n} de 5"><svg viewBox="0 0 24 24"><path d="${STAR_PATH}"/></svg></button>`).join("");
  const buttons = [...el.querySelectorAll("button")];
  buttons.forEach((b, i) => {
    b.addEventListener("mouseenter", () => buttons.forEach((x, j) => x.classList.toggle("hot", j <= i)));
    b.addEventListener("mouseleave", () => buttons.forEach((x) => x.classList.remove("hot")));
    b.addEventListener("click", async () => {
      const n = +b.dataset.n === rating ? null : +b.dataset.n;  // clicar na mesma estrela tira a nota
      await api().set_game_options(state.pageGame, { rating: n });
      Sfx.play(n ? "favOn" : "favOff");
      renderStars(n);
    });
  });
}

function playTrailer(trailer, withSound) {
  const video = $("gpVideo");
  stopTrailer();
  if (!$("gamePage").classList.contains("show")) return;
  if (trailer.mp4) video.src = trailer.mp4;
  else if (trailer.hls && window.Hls?.isSupported()) {
    state.hls = new Hls({ capLevelToPlayerSize: true, startLevel: -1 });
    state.hls.loadSource(trailer.hls);
    state.hls.attachMedia(video);
  } else return;
  video.muted = !withSound;
  video.volume = state.sound?.volume ?? 0.6;
  // no fundo automático, pula a abertura (classificação etária, logos de estúdio)
  video.onloadedmetadata = () => { if (!withSound && video.duration > 30) video.currentTime = 6; };
  video.onplaying = () => video.classList.add("playing");
  video.play().catch(() => {});
  $("gpSound").hidden = false;
  $("gpSound").classList.toggle("muted", video.muted);
  const index = (state.pageInfo?.trailers || []).indexOf(trailer);
  $("gpMedia").querySelectorAll("[data-trailer]").forEach((el) => el.classList.toggle("active", +el.dataset.trailer === index));
}

function stopTrailer() {
  clearTimeout(state.trailerTimer);
  const video = $("gpVideo");
  video.classList.remove("playing");
  video.pause();
  state.hls?.destroy();
  state.hls = null;
  video.removeAttribute("src");
  video.load();
}

function toggleTrailerSound() {
  const video = $("gpVideo");
  video.muted = !video.muted;
  $("gpSound").classList.toggle("muted", video.muted);
}

// Colunas de uma série só (horas por dia): ≤24px de largura, topo arredondado 4px, base reta,
// 2px de folga entre colunas, grade fina, rótulo só no pico e dica ao passar o mouse.
function renderChart(el, days) {
  const total = days.reduce((s, d) => s + d.seconds, 0);
  if (!total) {
    el.innerHTML = `<div class="chart-empty">Ainda sem sessões registradas nesse período.<br>O GameDock começa a contar a partir da próxima vez que você jogar.</div>`;
    return;
  }
  const W = el.clientWidth || 900, H = 190, top = 18, bottom = 24, left = 34;
  const plotW = W - left, plotH = H - top - bottom;
  const hours = days.map((d) => d.seconds / 3600);
  const max = Math.max(...hours);
  const step = max <= 1 ? 0.5 : max <= 3 ? 1 : max <= 6 ? 2 : Math.ceil(max / 4);
  const ceil = Math.max(step, Math.ceil(max / step) * step);
  const band = plotW / days.length;
  const barW = Math.min(24, band - 2);
  const y = (h) => top + plotH - (h / ceil) * plotH;
  const peak = hours.indexOf(max);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Horas jogadas por dia nos últimos 30 dias">`;
  for (let v = 0; v <= ceil + 1e-9; v += step) {
    svg += `<line class="grid" x1="${left}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text class="axis" x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${String(+v.toFixed(1)).replace(".", ",")}h</text>`;
  }
  days.forEach((d, i) => {
    const x = left + i * band + (band - barW) / 2;
    const h = hours[i];
    if (h > 0) {
      const bh = Math.max(2, (h / ceil) * plotH), by = top + plotH - bh, r = Math.min(4, barW / 2, bh);
      // caminho com só os cantos de cima arredondados (a base fica reta, presa na linha do zero)
      svg += `<rect class="hit" x="${left + i * band}" y="${top}" width="${band}" height="${plotH}" data-i="${i}"/>`;
      svg += `<path class="bar" data-i="${i}" d="M${x},${by + bh}V${by + r}Q${x},${by} ${x + r},${by}H${x + barW - r}Q${x + barW},${by} ${x + barW},${by + r}V${by + bh}Z"/>`;
      if (i === peak) svg += `<text class="peak" x="${x + barW / 2}" y="${by - 6}" text-anchor="middle">${fmtHours(d.seconds)}</text>`;
    }
    // um rótulo por semana + o último dia (se não ficar colado no rótulo semanal anterior)
    const last = i === days.length - 1 && i % 7 >= 3;
    if (i % 7 === 0 || last) {
      const [, m, dd] = d.date.split("-");
      svg += `<text class="axis" x="${x + barW / 2}" y="${H - 6}" text-anchor="middle">${dd}/${m}</text>`;
    }
  });
  el.innerHTML = svg + `</svg><div class="chart-tip" id="chartTip"></div>`;
  const tip = el.querySelector(".chart-tip");
  el.querySelectorAll(".hit").forEach((hit) => {
    const i = +hit.dataset.i, bar = el.querySelector(`.bar[data-i="${i}"]`);
    hit.addEventListener("mouseenter", () => {
      bar?.classList.add("hover");
      const [yy, m, dd] = days[i].date.split("-");
      tip.innerHTML = `<b>${fmtHours(days[i].seconds)}</b>${dd}/${m}/${yy}`;
      const rect = bar.getBBox();
      tip.style.left = `${((rect.x + rect.width / 2) / W) * 100}%`;
      tip.style.top = `${(rect.y / H) * 100}%`;
      tip.style.opacity = 1;
    });
    hit.addEventListener("mouseleave", () => { bar?.classList.remove("hover"); tip.style.opacity = 0; });
  });
}

async function saveGameOptions() {
  const id = state.pageGame;
  const result = await api().set_game_options(id, { name: $("gpNameInput").value, args: $("gpArgs").value });
  Sfx.play("success");
  toast("Opções salvas");
  $("gpName").textContent = result.name;
  await loadGames(id);
}

async function hideGame() {
  const id = state.pageGame;
  const game = state.all.find((g) => g.id === id);
  await api().set_game_options(id, { hidden: true });
  hidePage("gamePage", "gamePage");
  toast(`${game?.name || "Jogo"} ocultado. Para mostrar de novo: Configurações → Dados.`);
  await loadGames();
}

async function changeCover(reset = false) {
  const id = state.pageGame;
  const art = reset ? await api().reset_cover(id) : await api().choose_cover(id);
  if (!art) return;
  state.art[id] = art;
  delete state.accent[id];
  const game = state.all.find((g) => g.id === id);
  $("gpCover").replaceChildren(coverElement(game));
  paintTile(game);
  showHero(current());
  $("gpResetCover").hidden = reset;
  Sfx.play("success");
}

// ---------------------------------------------------------------- capturas (grade + galeria + ampliada)

function renderShots(el, shots, emptyText) {
  state.shotLists = state.shotLists || {};
  state.shotLists[el.id] = shots;
  if (!shots.length) {
    el.innerHTML = `<p class="shots-empty">${esc(emptyText)}</p>`;
    return;
  }
  el.innerHTML = shots.map((s, i) => `
    <figure class="shot" style="--i:${Math.min(i, 20)}" data-i="${i}">
      ${s.kind === "clip" ? `<video src="${s.url}#t=1" preload="metadata" muted></video><span class="badge clip">▶ Clipe</span>`
        : `<img src="${s.url}" alt="" loading="lazy">`}
      ${s.kind === "trophy" ? `<span class="badge">🏆 ${esc(s.trophy || "Troféu")}</span>` : ""}
      <figcaption>${esc(el.id === "glGrid" ? s.game : "")}<span>${fmtDate(s.ts)}</span></figcaption>
    </figure>`).join("");
  el.querySelectorAll(".shot").forEach((fig) => fig.addEventListener("click", () =>
    openLightbox(shots.map((s) => ({ ...s, caption: `${s.trophy ? s.trophy + " · " : ""}${s.game} · ${fmtDate(s.ts)}` })), +fig.dataset.i)));
}

async function openGallery(gameId = null) {
  state.galleryFilter = gameId;
  showPage("galleryPage", "gallery", {});
  await renderGallery();
}

async function renderGallery() {
  const all = await api().list_captures(null);
  const games = [...new Map(all.map((s) => [s.gameId, s.game])).entries()];
  const filter = state.galleryFilter;
  $("glFilters").innerHTML = [[null, "Todas", all.length], ...games.map(([id, name]) => [id, name, all.filter((s) => s.gameId === id).length])]
    .map(([id, name, n]) => `<button class="tab ${id === filter ? "on" : ""}" data-game="${id ?? ""}">${esc(name)} <small>${n}</small></button>`).join("");
  $("glFilters").querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    state.galleryFilter = b.dataset.game || null;
    Sfx.play("move");
    renderGallery();
  }));
  const key = (await api().get_settings()).captureHotkey;
  $("glHint").textContent = key ? `Aperte ${key} durante um jogo para capturar` : "Tecla de captura desligada (Configurações)";
  renderShots($("glGrid"), filter ? all.filter((s) => s.gameId === filter) : all,
              key ? `Nenhuma captura ainda. Aperte ${key} durante um jogo.` : "Nenhuma captura ainda.");
}

function openLightbox(items, index) {
  state.lightbox = { items, index };
  $("lightbox").classList.add("show");
  pushLayer("lightbox", closeLightbox, { ArrowLeft: () => stepLightbox(-1), ArrowRight: () => stepLightbox(1) });
  paintLightbox();
}

function paintLightbox() {
  const { items, index } = state.lightbox;
  const item = items[index];
  const video = $("lightboxVideo");
  const isClip = item.kind === "clip";
  $("lightboxImg").hidden = isClip;
  video.hidden = !isClip;
  video.pause();
  if (isClip) { video.src = item.url; video.play().catch(() => {}); }
  else { video.removeAttribute("src"); $("lightboxImg").src = item.url; }
  $("lightboxCaption").textContent = item.caption || "";
  $("lbPrev").hidden = index === 0;
  $("lbNext").hidden = index === items.length - 1;
  $("lbDelete").hidden = !item.id;
}

function stepLightbox(dir) {
  const lb = state.lightbox;
  const next = lb.index + dir;
  if (next < 0 || next >= lb.items.length) return Sfx.play("edge");
  lb.index = next;
  Sfx.play("move");
  paintLightbox();
}

function closeLightbox() {
  $("lightboxVideo").pause();
  $("lightbox").classList.remove("show");
  popLayer("lightbox");
}

async function deleteCapture() {
  const lb = state.lightbox;
  const item = lb.items[lb.index];
  if (!item?.id || !(await api().delete_capture(item.id))) return;
  Sfx.play("favOff");
  lb.items.splice(lb.index, 1);
  if (!lb.items.length) closeLightbox();
  else { lb.index = Math.min(lb.index, lb.items.length - 1); paintLightbox(); }
  if ($("galleryPage").classList.contains("show")) renderGallery();
  if ($("gamePage").classList.contains("show")) {
    const shots = await api().list_captures(state.pageGame);
    renderShots($("gpShots"), shots.slice(0, 8), "Nenhuma captura ainda.");
  }
}

// ---------------------------------------------------------------- perfil

const TIER_COLORS = { gold: "#f5c542", silver: "#c9d2de", bronze: "#d08a4c", platinum: "#a9dcff" };

const PROFILE_COLORS = ["#8b7bff", "#3dd6ff", "#2fd18b", "#f5c542", "#ff8a3d", "#ff3d81", "#e8e8ee", "#c23b3b"];

function avatarHtml(p) {
  return p.avatarUrl ? `<img src="${p.avatarUrl}" alt="">` : esc((p.name || "?").trim().charAt(0).toUpperCase());
}

function paintAvatar(el, p) {
  el.innerHTML = avatarHtml(p);
  el.classList.remove("frame-bronze", "frame-silver", "frame-gold", "frame-platinum");
  el.classList.add(`frame-${p.frame}`);
}

async function refreshProfileButton(p = null) {
  p = p || await api().get_profile();
  state.profile = p;
  paintAvatar($("tbAvatar"), p);
  $("tbLevel").textContent = `Nv ${p.level}`;
  return p;
}

function hexToHsl(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [h, s, l] = rgbToHsl(n >> 16, (n >> 8) & 255, n & 255);
  return [Math.round(h), Math.round(s * 100), Math.round(l * 100)];
}

// a cor do perfil vale só para a página do perfil e o editor (o resto do app segue a cor do jogo)
async function applyProfileColor(p) {
  let hsl = null;
  if (p.color) hsl = hexToHsl(p.color);
  else if (p.bannerUrl) hsl = await accentFromImage(p.bannerUrl);
  for (const id of ["profilePage", "profileEdit"]) {
    const style = $(id).style;
    if (!hsl) { ["--ah", "--as", "--al"].forEach((v) => style.removeProperty(v)); continue; }
    style.setProperty("--ah", hsl[0]);
    style.setProperty("--as", Math.max(55, hsl[1]) + "%");
    style.setProperty("--al", Math.min(72, Math.max(58, hsl[2])) + "%");
  }
}

async function openProfile() {
  showPage("profilePage", "profile", { e: openProfileEditor });
  renderProfile(await refreshProfileButton());
}

function renderProfile(p) {
  $("pfBannerImg").src = p.bannerUrl || "";
  $("pfBanner").style.setProperty("--pos", `${p.bannerPos}%`);
  paintAvatar($("pfAvatar"), p);
  $("pfName").textContent = p.name;
  $("pfBio").textContent = p.bio || "";
  $("pfLevel").textContent = `Nível ${p.level}`;
  $("pfXp").textContent = `${p.xpInto.toLocaleString("pt-BR")} / ${p.xpNeeded.toLocaleString("pt-BR")} XP`;
  $("pfXpBar").style.width = "0%";
  requestAnimationFrame(() => requestAnimationFrame(() => { $("pfXpBar").style.width = `${(p.xpInto / p.xpNeeded) * 100}%`; }));
  applyProfileColor(p);
  renderShowcase(p);

  const t = p.trophies;
  const dot = (tier, n) => `<span><i style="--tier:${TIER_COLORS[tier]}"></i>${n}</span>`;
  $("pfTiles").innerHTML = [
    [fmtHours(p.hours * 3600), "jogadas no total"],
    [p.games, p.games === 1 ? "jogo instalado" : "jogos instalados"],
    [p.sessions.toLocaleString("pt-BR"), "sessões"],
    [`${t.total}`, `troféus <div class="dots">${dot("gold", t.gold)}${dot("silver", t.silver)}${dot("bronze", t.bronze)}</div>`],
    [`${t.platinum}`, t.platinum === 1 ? "platina" : "platinas"],
  ].map(([v, l]) => `<div class="tile-stat"><b>${esc(v)}</b><span>${l}</span></div>`).join("");

  const top = p.top, max = Math.max(1, ...top.map((g) => g.seconds));
  $("pfTop").innerHTML = top.length ? top.map((g) => `
    <div class="bar-row"><strong title="${esc(g.name)}">${esc(g.name)}</strong>
      <div class="track"><i style="width:${(g.seconds / max) * 100}%"></i></div><span>${fmtHours(g.seconds)}</span></div>`).join("")
    : `<p class="shots-empty">Jogue um pouco que os seus jogos aparecem aqui.</p>`;

  const rec = [];
  if (p.longestSession) rec.push(["Sessão mais longa", `${fmtHours(p.longestSession.seconds)} · ${esc(p.longestSession.game)}`]);
  if (p.rarest) rec.push(["Troféu mais raro", `${esc(p.rarest.name)} · ${p.rarest.percent.toFixed(1).replace(".", ",")}% dos jogadores`]);
  if (p.since) rec.push(["No GameDock desde", new Date(p.since).toLocaleDateString("pt-BR", { day: "2-digit", month: "long", year: "numeric" })]);
  rec.push(["XP total", `${p.xp.toLocaleString("pt-BR")} XP`]);
  $("pfRecords").innerHTML = rec.map(([l, v]) => `<div><span>${l}</span><b>${v}</b></div>`).join("");

  $("pfRecent").innerHTML = p.recent.length ? p.recent.map((r) => `
    <div class="rt" style="--tier:${TIER_COLORS[r.tier]}">${r.icon ? `<img src="${r.icon}" alt="">` : ""}
      <div><strong>${esc(r.name)}</strong><span>${esc(r.game)} · ${r.at ? fmtDate(r.at) : ""}</span></div></div>`).join("")
    : `<p class="shots-empty">Nenhum troféu ainda.</p>`;
}

function renderShowcase(p) {
  const parts = [];
  if (p.showcase) {
    const s = p.showcase, tr = s.trophies;
    parts.push(`<div class="showcase-game" data-game="${s.id}">${s.cover ? `<img src="${s.cover}" alt="">` : ""}
      <div><small>Jogo favorito</small><strong>${esc(s.name)}</strong>
      <span>${s.seconds ? fmtHours(s.seconds) + " jogadas" : "ainda não jogado pelo GameDock"}</span>
      ${tr.total ? `<span>${tr.unlocked}/${tr.total} troféus${tr.platinum ? " · platina" : ""}</span>` : ""}</div></div>`);
  }
  if (p.featured.length) {
    parts.push(`<div class="showcase-trophies"><small>Troféus em destaque</small><div class="row">${p.featured.map((f) => `
      <div class="featured" style="--tier:${TIER_COLORS[f.tier]}">${f.icon ? `<img src="${f.icon}" alt="">` : ""}
        <div><strong>${esc(f.name)}</strong><span>${esc(f.game)}</span>
        <span>${f.percent != null ? f.percent.toFixed(1).replace(".", ",") + "% dos jogadores" : ""}</span></div></div>`).join("")}</div></div>`);
  }
  $("pfShowcase").innerHTML = parts.join("");
  $("pfShowcase").style.gridTemplateColumns = parts.length === 2 ? "auto 1fr" : "1fr";
  $("pfShowcase").querySelector(".showcase-game")?.addEventListener("click", () => {
    const i = state.games.findIndex((g) => g.id === p.showcase.id);
    if (i < 0) return;
    hidePage("profilePage", "profile");
    select(i, { force: true });
    openGamePage();
  });
}

// ---------------------------------------------------------------- editor do perfil

async function openProfileEditor() {
  const p = state.profile || await refreshProfileButton();
  $("profileEdit").classList.add("show");
  pushLayer("profileEdit", closeProfileEditor, null, { form: true });
  Sfx.play("open");
  $("peAvatarGrid").hidden = true;
  $("peBannerGrid").hidden = true;
  $("peName").value = p.name;
  $("peBio").value = p.bio || "";
  $("peBioCount").textContent = `${(p.bio || "").length}/120`;
  $("peBannerPos").value = p.bannerPos;
  const games = state.all;
  $("peShowGame").innerHTML = `<option value="">Nenhum</option>` +
    games.map((g) => `<option value="${g.id}" ${g.id === p.showcaseGame ? "selected" : ""}>${esc(g.name)}</option>`).join("");
  state.unlockedTrophies = await api().list_unlocked_trophies();
  paintEditor(p);
}

function closeProfileEditor() {
  $("profileEdit").classList.remove("show");
  popLayer("profileEdit");
  Sfx.play("close");
}

function paintEditor(p) {
  paintAvatar($("peAvatar"), p);
  $("peBannerImg").src = p.bannerUrl || "";
  $("peBanner").style.setProperty("--pos", `${p.bannerPos}%`);
  $("peColors").innerHTML = `<button class="auto ${p.color ? "" : "on"}" data-color="">Automática</button>` +
    PROFILE_COLORS.map((c) => `<button style="--c:${c}" class="${p.color === c ? "on" : ""}" data-color="${c}" title="${c}"></button>`).join("");
  $("peColors").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => saveProfile({ color: b.dataset.color || null })));
  const chosen = new Set((p.showcaseTrophies || []).map((t) => `${t.game}|${t.api}`));
  $("peTrophyCount").textContent = `${chosen.size}/3`;
  $("peTrophies").innerHTML = state.unlockedTrophies.length ? state.unlockedTrophies.map((t) => `
    <button class="${chosen.has(`${t.gameId}|${t.api}`) ? "on" : ""}" data-key="${t.gameId}|${t.api}" style="--tier:${TIER_COLORS[t.tier]}">
      ${t.icon ? `<img src="${t.icon}" alt="">` : ""}<div><strong>${esc(t.name)}</strong>
      <span>${esc(t.game)}${t.percent != null ? " · " + t.percent.toFixed(1).replace(".", ",") + "%" : ""}</span></div></button>`).join("")
    : `<p class="showcase-empty">Ganhe troféus para colocar na vitrine.</p>`;
  $("peTrophies").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.key;
    if (chosen.has(key)) chosen.delete(key);
    else if (chosen.size >= 3) return Sfx.play("edge");
    else chosen.add(key);
    saveProfile({ showcase_trophies: [...chosen].map((k) => { const [game, api_] = k.split("|"); return { game, api: api_ }; }) });
  }));
}

async function saveProfile(changes) {
  const p = await api().set_profile(changes);
  Sfx.play("move");
  await afterProfileChange(p);
}

async function afterProfileChange(p) {
  if (!p) return;
  await refreshProfileButton(p);
  paintEditor(p);
  if ($("profilePage").classList.contains("show")) renderProfile(p);
}

function pickerGrid(gridId, kind) {
  const grid = $(gridId);
  if (!grid.hidden) { grid.hidden = true; return; }
  const key = kind === "avatar" ? "cover" : "hero";
  const games = state.all.filter((g) => state.art[g.id]?.[key] || state.art[g.id]?.cover);
  grid.innerHTML = games.map((g) =>
    `<button data-id="${g.id}" title="${esc(g.name)}"><img src="${state.art[g.id][key] || state.art[g.id].cover}" alt="" loading="lazy"></button>`).join("");
  grid.hidden = false;
  grid.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    grid.hidden = true;
    saveProfile(kind === "avatar" ? { avatar_mode: "game", avatar: b.dataset.id } : { banner_mode: "game", banner_game: b.dataset.id });
  }));
}

function bindProfileEditor() {
  $("pfEdit").addEventListener("click", openProfileEditor);
  $("peClose").addEventListener("click", closeProfileEditor);
  $("profileEdit").addEventListener("click", (e) => { if (e.target.id === "profileEdit") closeProfileEditor(); });
  $("peAvatarUpload").addEventListener("click", async () => afterProfileChange(await api().choose_profile_image("avatar")));
  $("peBannerUpload").addEventListener("click", async () => afterProfileChange(await api().choose_profile_image("banner")));
  $("peAvatarGame").addEventListener("click", () => pickerGrid("peAvatarGrid", "avatar"));
  $("peBannerGame").addEventListener("click", () => pickerGrid("peBannerGrid", "banner"));
  $("peAvatarInitial").addEventListener("click", () => saveProfile({ avatar_mode: "initial" }));
  $("peBannerDefault").addEventListener("click", () => saveProfile({ banner_mode: "default" }));
  $("peBannerPos").addEventListener("input", (e) => {  // prévia ao vivo; salva ao soltar
    for (const id of ["peBanner", "pfBanner"]) $(id).style.setProperty("--pos", `${e.target.value}%`);
  });
  $("peBannerPos").addEventListener("change", (e) => saveProfile({ banner_pos: +e.target.value }));
  $("peName").addEventListener("change", (e) => saveProfile({ name: e.target.value }));
  $("peBio").addEventListener("input", (e) => { $("peBioCount").textContent = `${e.target.value.length}/120`; });
  $("peBio").addEventListener("change", (e) => saveProfile({ bio: e.target.value }));
  $("peShowGame").addEventListener("change", (e) => saveProfile({ showcase_game: e.target.value || null }));
}

// ---------------------------------------------------------------- configurações extras

async function loadExtraSettings(settings) {
  loadEmulators();
  const shellSettings = await api().get_shell_settings();
  $("closeToTray").checked = shellSettings.closeToTray;
  $("autostart").checked = shellSettings.autostart;
  const select = $("captureKey");
  select.innerHTML = [["", "Desligada"], ...settings.hotkeys.map((k) => [k, k])]
    .map(([v, l]) => `<option value="${v}" ${v === settings.captureHotkey ? "selected" : ""}>${l}</option>`).join("");
  $("captureStatus").textContent = "";
  paintRecording(await api().get_recording(), settings.hotkeys);
  $("backupStatus").textContent = "";
  const hidden = await api().list_hidden_games();
  $("hiddenGames").innerHTML = hidden.length
    ? `<p style="margin:16px 0 8px">Jogos ocultos:</p>` + hidden.map((g) =>
        `<div class="settings-actions" style="margin-top:6px"><span>${esc(g.name)}</span><button type="button" class="ghost-btn" data-unhide="${g.id}">Mostrar</button></div>`).join("")
    : "";
  $("hiddenGames").querySelectorAll("[data-unhide]").forEach((b) => b.addEventListener("click", async () => {
    await api().set_game_options(b.dataset.unhide, { hidden: false });
    b.closest(".settings-actions").remove();
    loadGames(current()?.id);
  }));
}

async function changeCaptureKey() {
  const result = await api().set_capture_hotkey($("captureKey").value);
  const el = $("captureStatus");
  el.textContent = result.ok ? ($("captureKey").value ? `Pronto: ${$("captureKey").value} captura a tela.` : "Tecla de captura desligada.") : result.error;
  el.className = "settings-status " + (result.ok ? "ok" : "error");
  Sfx.play(result.ok ? "success" : "error");
}

async function backupNow() {
  const el = $("backupStatus");
  el.textContent = "Fazendo backup...";
  el.className = "settings-status";
  const result = await api().make_backup();
  el.textContent = result.ok ? `Backup salvo: ${result.path}` : `Não deu: ${result.error}`;
  el.className = "settings-status " + (result.ok ? "ok" : "error");
  Sfx.play(result.ok ? "success" : "error");
}

// ---------------------------------------------------------------- clipes de vídeo

const CLIP_KEYS = ["Alt+F10", "Ctrl+F10", "F9", "F10"];

function paintRecording(r) {
  $("clipInstallRow").hidden = r.ffmpegReady;
  $("clipOptions").hidden = !r.ffmpegReady;
  const inst = r.install || {};
  $("clipInstall").disabled = !!inst.running;
  $("clipInstallText").textContent = inst.running
    ? `Baixando… ${inst.total ? Math.round((inst.done / inst.total) * 100) : 0}%`
    : inst.error ? `Falhou: ${inst.error}` : "Precisa baixar o componente de gravação (ffmpeg, ~110 MB, uma vez).";
  $("clipEnabled").checked = r.enabled;
  $("clipAudio").checked = r.audio;
  $("clipSeconds").value = String(r.seconds);
  $("clipFps").value = String(r.fps);
  $("clipKey").innerHTML = [["", "Desligada"], ...CLIP_KEYS.map((k) => [k, k])]
    .map(([v, l]) => `<option value="${v}" ${v === r.hotkey ? "selected" : ""}>${l}</option>`).join("");
  $("clipEncoder").textContent = r.encoder ? `codificando com ${r.encoder.replace("h264_", "").toUpperCase()}` : "usa o codificador da placa de vídeo";
  $("clipStatus").textContent = !r.enabled ? "" : r.running ? "● gravando o buffer agora" : "começa quando um jogo abrir";
  $("clipStatus").className = "settings-status " + (r.running ? "ok" : "");
  if (inst.running) setTimeout(async () => state.settingsOpen && paintRecording(await api().get_recording()), 700);
}

function bindRecording() {
  $("clipInstall").addEventListener("click", async () => { await api().install_ffmpeg(); setTimeout(async () => paintRecording(await api().get_recording()), 300); });
  const save = async () => {
    paintRecording(await api().set_recording({ enabled: $("clipEnabled").checked, audio: $("clipAudio").checked,
      seconds: +$("clipSeconds").value, fps: +$("clipFps").value }));
    Sfx.play("move");
  };
  for (const id of ["clipEnabled", "clipAudio", "clipSeconds", "clipFps"]) $(id).addEventListener("change", save);
  $("clipKey").addEventListener("change", async () => {
    const r = await api().set_clip_hotkey($("clipKey").value);
    if (!r.ok) { toast(r.error, true); Sfx.play("error"); } else Sfx.play("success");
  });
  $("clipFolder").addEventListener("click", () => api().open_clips_folder());
}

// ---------------------------------------------------------------- eventos ao vivo

window.onCapture = ({ gameId }) => {
  if ($("galleryPage").classList.contains("show")) renderGallery();
  if ($("gamePage").classList.contains("show") && state.pageGame === gameId)
    api().list_captures(gameId).then((s) => renderShots($("gpShots"), s.slice(0, 8), ""));
};
window.onLevelUp = ({ level }) => {
  refreshProfileButton();
  toast(`Você subiu para o nível ${level}!`);
};

// ---------------------------------------------------------------- ligações

function bindPages() {
  bindSearch();
  bindRecap();
  $("infoBtn").addEventListener("click", openGamePage);
  $("galleryBtn").addEventListener("click", () => openGallery());
  $("profileBtn").addEventListener("click", openProfile);
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => topLayer()?.close()));
  $("gpPlay").addEventListener("click", play);
  $("gpFav").addEventListener("click", async () => {
    await toggleFavorite();
    $("gpFav").classList.toggle("on", !!state.all.find((g) => g.id === state.pageGame)?.favorite);
  });
  $("gpSound").addEventListener("click", toggleTrailerSound);
  $("gpStore").addEventListener("click", () => state.pageInfo?.storeUrl && api().open_url(state.pageInfo.storeUrl));
  $("gpMore").addEventListener("click", () => {
    const open = $("gpAbout").classList.toggle("open");
    $("gpMore").textContent = open ? "Mostrar menos" : "Ler mais";
  });
  $("gpTrophies").addEventListener("click", openTrophies);
  $("gpFolder").addEventListener("click", () => api().open_game_folder(state.pageGame));
  $("gpCoverBtn").addEventListener("click", () => changeCover());
  $("gpResetCover").addEventListener("click", () => changeCover(true));
  $("gpSave").addEventListener("click", saveGameOptions);
  $("gpHide").addEventListener("click", hideGame);
  $("gpNotGame").addEventListener("change", async (e) => {
    await api().set_game_options(state.pageGame, { track: !e.target.checked });
    Sfx.play("move");
    toast(e.target.checked ? "Ok: esse app não conta mais tempo nem XP" : "Voltou a contar como jogo");
    refreshProfileButton();
  });
  $("gpAllShots").addEventListener("click", () => openGallery(state.pageGame));
  bindProfileEditor();
  $("lbPrev").addEventListener("click", (e) => { e.stopPropagation(); stepLightbox(-1); });
  $("lbNext").addEventListener("click", (e) => { e.stopPropagation(); stepLightbox(1); });
  $("lbDelete").addEventListener("click", (e) => { e.stopPropagation(); deleteCapture(); });
  $("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox" || e.target.tagName === "FIGURE") closeLightbox(); });
  $("captureKey").addEventListener("change", changeCaptureKey);
  const saveShell = () => { api().set_shell_settings($("closeToTray").checked, $("autostart").checked); Sfx.play("move"); };
  $("closeToTray").addEventListener("change", saveShell);
  $("autostart").addEventListener("change", saveShell);
  $("backupNow").addEventListener("click", backupNow);
  bindRecording();
  $("backupOpen").addEventListener("click", () => api().open_backups());
  window.addEventListener("resize", () => {
    if ($("gamePage").classList.contains("show") && state.pageDetails) renderChart($("gpChart"), state.pageDetails.days);
  });
}
