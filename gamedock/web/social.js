// GameDock — Discord (servidores, calls, voz) e YouTube Music (player, suas playlists, busca).
// Tudo dentro de uma função para não criar nomes globais que batam com os outros arquivos (um const repetido
// derruba o script inteiro); o que o Python chama fica em window.onDiscord / window.onMusic.
(() => {
  const ICON = {
    mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    micOff: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M4 4l16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    head: '<svg viewBox="0 0 24 24"><path d="M4 15v-3a8 8 0 0 1 16 0v3" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="14" width="5" height="7" rx="2" fill="currentColor"/><rect x="16" y="14" width="5" height="7" rx="2" fill="currentColor"/></svg>',
    headOff: '<svg viewBox="0 0 24 24"><path d="M4 15v-3a8 8 0 0 1 13.5-5.8M20 12v3" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="14" width="5" height="7" rx="2" fill="currentColor"/><rect x="16" y="14" width="5" height="7" rx="2" fill="currentColor"/><path d="M3 3l18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    leave: '<svg viewBox="0 0 24 24"><path d="M3.5 13.5c4.7-4.6 12.3-4.6 17 0l-2.3 2.3-3.4-1.5v-2.4a10 10 0 0 0-5.6 0v2.4l-3.4 1.5z" fill="currentColor"/></svg>',
    voice: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9zM16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>',
    group: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8.5" r="3.5" fill="currentColor"/><circle cx="17" cy="9.5" r="2.7" fill="currentColor"/><path d="M2.5 19.5c.6-3.6 3.3-5.5 6.5-5.5s5.9 1.9 6.5 5.5zM15.8 14.2c2.9-.3 5.2 1.3 5.7 4.8h-4.4c-.2-1.9-.6-3.5-1.3-4.8z" fill="currentColor"/></svg>',
    hash: '<svg viewBox="0 0 24 24"><path d="M10 3L8 21M16 3l-2 18M4 8.5h17M3 15.5h17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><rect x="6.5" y="5" width="4" height="14" rx="1.2" fill="currentColor"/><rect x="13.5" y="5" width="4" height="14" rx="1.2" fill="currentColor"/></svg>',
    next: '<svg viewBox="0 0 24 24"><path d="M5 6v12l9-6zM16 6h2.5v12H16z" fill="currentColor"/></svg>',
    prev: '<svg viewBox="0 0 24 24"><path d="M19 6v12l-9-6zM8 6H5.5v12H8z" fill="currentColor"/></svg>',
    heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
    heartOn: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z" fill="currentColor"/></svg>',
  };
  const clock = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  // ============================================================== Discord

  const dc = { open: false, guild: null, guilds: [], call: null, voice: { mute: false, deaf: false }, busy: false, refresh: null };

  async function openDiscord() {
    showPage("discordPage", "discord", { r: () => loadDiscord(true) });
    dc.open = true;
    await loadDiscord(false);
  }

  function closedDiscord() { dc.open = false; }

  async function loadDiscord(force) {
    const st = await api().discord_state();
    renderStatus(st);
    if (!st.connected) return;
    if (force || !dc.guilds.length) {
      const g = await api().discord_guilds();
      if (!g.ok) return dcError(g.error);
      dc.guilds = g.data;
    }
    renderGuilds();
    if (!dc.guild) dc.guild = HOME;   // grupos e DMs primeiro: é onde as calls com amigos acontecem
    await Promise.all([loadChannels(), loadCall()]);
  }

  function renderStatus(st) {
    const ok = st.connected;
    dc.me = st.user?.id || null;
    $("dcSetup").hidden = ok;
    $("dcMain").hidden = !ok;
    $("dcUser").innerHTML = ok && st.user ? `<img src="${esc(st.user.avatar)}" alt=""><b>${esc(st.user.name)}</b>
      <button class="link-btn" id="dcLogout">Desconectar</button>` : "";
    $("dcLogout")?.addEventListener("click", async () => { renderStatus(await api().discord_logout()); dc.guilds = []; });
    if (ok) return;
    const steps = [
      [st.clientId, "Application ID colocado em Configurações → Discord"],
      [st.running, "Discord aberto neste PC"],
    ];
    $("dcSteps").innerHTML = steps.map(([done, text]) => `<li class="${done ? "done" : ""}">${text}</li>`).join("");
    $("dcConnect").disabled = !st.clientId || !st.running || dc.busy;
    $("dcMsg").textContent = st.error ? `Último erro: ${st.error}` : "";
  }

  function dcError(msg) { toast(`Discord: ${msg}`, true); }

  const HOME = "@me";

  function renderGuilds() {
    $("dcGuilds").innerHTML = `<button class="dc-guild home${dc.guild === HOME ? " on" : ""}" data-guild="${HOME}" title="Grupos e DMs">${ICON.group}</button><i class="dc-sep"></i>` + dc.guilds.map((g) => `<button class="dc-guild${g.id === dc.guild ? " on" : ""}" data-guild="${esc(g.id)}" title="${esc(g.name)}">
      ${g.icon ? `<img src="${esc(g.icon)}" alt="">` : `<span>${esc(g.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 3))}</span>`}</button>`).join("");
    $("dcGuilds").querySelectorAll("[data-guild]").forEach((b) => b.addEventListener("click", () => {
      dc.guild = b.dataset.guild;
      renderGuilds();
      loadChannels();
    }));
  }

  async function loadChannels() {
    $("dcText").previousElementSibling.hidden = $("dcText").hidden = dc.guild === HOME;
    if (dc.guild === HOME) return loadGroups();
    const guild = dc.guilds.find((g) => g.id === dc.guild);
    $("dcGuildName").textContent = guild?.name || "";
    if (!guild) return;
    $("dcVoice").innerHTML = `<p class="shots-empty">Carregando…</p>`;
    const r = await api().discord_channels(guild.id);
    if (dc.guild !== guild.id) return;
    if (!r.ok) { $("dcVoice").innerHTML = `<p class="shots-empty">${esc(r.error)}</p>`; return; }
    const here = dc.call?.id;
    $("dcVoice").innerHTML = r.data.voice.length ? r.data.voice.map((c) => `
      <div class="dc-chan${c.id === here ? " here" : ""}">
        <button class="dc-chan-head" data-join="${esc(c.id)}">${ICON.voice}<b>${esc(c.name)}</b>
          <small>${c.members.length}${c.limit ? `/${c.limit}` : ""}</small><em>${c.id === here ? "Você está aqui" : "Entrar"}</em></button>
        ${c.members.length ? `<div class="dc-mini">${c.members.map((m) => `<span class="${m.speaking ? "speaking" : ""}" data-user="${esc(m.id)}">
          <img src="${esc(m.avatar)}" alt="">${esc(m.name)}${m.mute ? ICON.micOff : ""}</span>`).join("")}</div>` : ""}
      </div>`).join("") : `<p class="shots-empty">Este servidor não tem calls que você possa ver.</p>`;
    $("dcText").innerHTML = r.data.text.map((c) => `<button class="dc-text" data-text="${esc(c.id)}">${ICON.hash}${esc(c.name)}</button>`).join("");
    $("dcVoice").querySelectorAll("[data-join]").forEach((b) => b.addEventListener("click", () => join(b.dataset.join)));
    $("dcText").querySelectorAll("[data-text]").forEach((b) => b.addEventListener("click", () => api().discord_open_text(guild.id, b.dataset.text)));
  }

  // ---------------------------------------------- grupos e DMs

  async function loadGroups() {
    $("dcGuildName").textContent = "Grupos e DMs";
    if (!$("dcVoice").querySelector(".dc-group")) $("dcVoice").innerHTML = `<p class="shots-empty">Carregando…</p>`;
    const r = await api().discord_groups();
    if (dc.guild !== HOME) return;
    if (!r.ok) { $("dcVoice").innerHTML = `<p class="shots-empty">${esc(r.error)}</p>`; return; }
    const { auto, error, groups } = r.data;
    const setup = auto ? (error ? `<p class="dc-msg">${esc(error)}</p>` : "") : `
      <div class="dc-groups-setup">
        <div><b>Mostrar todos os seus grupos</b>
          <p>O Discord só entrega a lista de grupos com uma permissão a mais. Clique e aceite o pedido no Discord.
            Os grupos em que você entrar em call ou que te mandarem mensagem também aparecem aqui sozinhos.</p></div>
        <button class="play-btn small" id="dcGroupsAuto"><span>Carregar meus grupos</span></button>
        <div class="dc-add">
          <input id="dcAddInput" placeholder="ou cole o link/ID de um grupo" spellcheck="false" autocomplete="off">
          <button class="ghost-btn tall" id="dcAddBtn">Adicionar</button>
        </div>
        <small>ID do grupo: no Discord, Configurações → Avançado → Modo desenvolvedor; depois botão direito no grupo → Copiar ID do canal.</small>
      </div>`;
    const avatarStack = (g) => g.icon ? `<img class="dc-gicon" src="${esc(g.icon)}" alt="">`
      : `<span class="dc-gstack">${g.recipients.slice(0, 3).map((u) => `<img src="${esc(u.avatar)}" alt="">`).join("") || ICON.group}</span>`;
    const card = (g) => `
      <div class="dc-chan dc-group${g.here ? " here" : ""}">
        <div class="dc-chan-head">${avatarStack(g)}
          <b>${esc(g.name)}<small>${g.type === 3 ? `${g.recipients.length + 1} pessoas` : "Mensagem direta"}${g.members.length ? ` · <i class="live">${g.members.length} na call</i>` : ""}</small></b>
          <button class="ghost-btn" data-open="${esc(g.id)}" title="Abrir no Discord">Abrir</button>
          ${auto ? "" : `<button class="ghost-btn" data-forget="${esc(g.id)}" title="Tirar da lista">✕</button>`}
          <button class="play-btn small" data-join="${esc(g.id)}" ${g.here ? "disabled" : ""}><span>${g.here ? "Você está aqui" : g.members.length ? "Entrar na call" : "Ligar"}</span></button>
        </div>
        ${g.members.length ? `<div class="dc-mini">${g.members.map((m) => `<span class="${m.speaking ? "speaking" : ""}" data-user="${esc(m.id)}">
          <img src="${esc(m.avatar)}" alt="">${esc(m.name)}${m.mute ? ICON.micOff : ""}</span>`).join("")}</div>` : ""}
      </div>`;
    const live = groups.filter((g) => g.members.length);
    const rest = groups.filter((g) => !g.members.length);
    $("dcVoice").innerHTML = setup
      + (live.length ? `<h4 class="dc-sub">Calls acontecendo agora</h4>${live.map(card).join("")}` : "")
      + (rest.length ? `<h4 class="dc-sub">${live.length ? "Outros grupos" : "Seus grupos"}</h4>${rest.map(card).join("")}` : "")
      + (!groups.length ? `<p class="shots-empty" style="margin-top:14px">Nenhum grupo ainda.</p>` : "");
    $("dcVoice").querySelectorAll("[data-join]").forEach((b) => b.addEventListener("click", () => join(b.dataset.join)));
    $("dcVoice").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => api().discord_open_text("", b.dataset.open)));
    $("dcVoice").querySelectorAll("[data-forget]").forEach((b) => b.addEventListener("click", async () => { await api().discord_forget_group(b.dataset.forget); loadGroups(); }));
    $("dcGroupsAuto")?.addEventListener("click", async () => {
      toast("Olhe o Discord: aceite o pedido para o GameDock ver seus grupos.");
      const res = await api().discord_enable_groups();
      if (!res.ok) return dcError(res.error);
      Sfx.play("open");
      loadGroups();
    });
    const add = async () => {
      const res = await api().discord_add_group($("dcAddInput").value);
      if (!res.ok) return dcError(res.error);
      $("dcAddInput").value = "";
      loadGroups();
    };
    $("dcAddBtn")?.addEventListener("click", add);
    $("dcAddInput")?.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); add(); } });
  }

  async function join(id) {
    if (dc.call?.id === id) return;
    toast(dc.guild === HOME ? "Chamando no Discord…" : "Entrando na call…");
    const r = await api().discord_join(id);
    if (!r.ok) return dcError(r.error);
    Sfx.play("open");
    await loadCall();
    loadChannels();
  }

  async function loadCall() {
    const r = await api().discord_call();
    if (!r.ok) return;
    dc.call = r.data.call;
    dc.voice = r.data.voice;
    renderCall();
  }

  function renderCall() {
    const c = dc.call, v = dc.voice;
    $("callDot").hidden = !c;
    $("dcCall").classList.toggle("empty", !c);
    $("dcCallName").textContent = c ? c.name : "Fora de call";
    $("dcCallGuild").textContent = c ? c.guild || "Mensagem direta" : "Escolha um canal de voz para entrar";
    $("dcMute").innerHTML = (v.mute ? ICON.micOff : ICON.mic) + `<span>${v.mute ? "Mutado" : "Microfone"}</span>`;
    $("dcDeaf").innerHTML = (v.deaf ? ICON.headOff : ICON.head) + `<span>${v.deaf ? "Ensurdecido" : "Áudio"}</span>`;
    $("dcMute").classList.toggle("off", v.mute);
    $("dcDeaf").classList.toggle("off", v.deaf);
    $("dcLeave").hidden = !c;
    $("dcMembers").innerHTML = c ? c.members.map((m) => `
      <div class="dc-member${m.speaking ? " speaking" : ""}" data-user="${esc(m.id)}">
        <img src="${esc(m.avatar)}" alt="">
        <div><b>${esc(m.name)}</b>
          <input type="range" min="0" max="200" value="${m.volume}" data-vol="${esc(m.id)}" title="Volume de ${esc(m.name)}" ${m.id === myId() ? "hidden" : ""}></div>
        <i>${m.deaf ? ICON.headOff : m.mute ? ICON.micOff : ""}</i>
      </div>`).join("") : "";
    $("dcMembers").querySelectorAll("[data-vol]").forEach((el) => el.addEventListener("change", () => api().discord_user(el.dataset.vol, Number(el.value), null)));
  }

  const myId = () => dc.me;

  async function setVoice(key) {
    const r = await api().discord_voice(key, !dc.voice[key]);
    if (!r.ok) return dcError(r.error);
    dc.voice = r.data;
    renderCall();
  }

  function markSpeaking(user, on) {
    document.querySelectorAll(`[data-user="${CSS.escape(user)}"]`).forEach((el) => el.classList.toggle("speaking", on));
    const m = dc.call?.members.find((x) => x.id === user);
    if (m) m.speaking = on;
  }

  window.onDiscord = (ev) => {
    if (ev.type === "speaking") return markSpeaking(ev.user, ev.on);
    if (ev.type === "voice") { dc.voice = { ...dc.voice, mute: ev.mute, deaf: ev.deaf }; if (dc.open) renderCall(); return; }
    if (ev.type === "call" || ev.type === "status") {   // vários eventos chegam juntos (alguém entrando): espera e atualiza uma vez
      clearTimeout(dc.refresh);
      dc.refresh = setTimeout(() => {
        if (dc.open) { if (ev.type === "status") loadDiscord(false); else { loadCall(); loadChannels(); } }
        else updateCallDot();
      }, 350);
    }
  };

  // bolinha verde no botão da barra de título enquanto você está numa call
  async function updateCallDot() {
    const st = await api().discord_state();
    if (!st.connected) { $("callDot").hidden = true; return; }
    const r = await api().discord_call();
    if (r.ok) { dc.call = r.data.call; dc.voice = r.data.voice; }
    $("callDot").hidden = !dc.call;
  }

  async function connect() {
    dc.busy = true;
    $("dcConnect").disabled = true;
    $("dcMsg").textContent = "Olhe o Discord: ele vai pedir para autorizar o GameDock.";
    const r = await api().discord_connect();
    dc.busy = false;
    renderStatus(r);
    if (!r.ok) { $("dcMsg").textContent = r.error; return; }
    Sfx.play("open");
    loadDiscord(true);
  }

  // ============================================================== YouTube Music

  const mu = { open: false, st: {}, timer: null, lib: null, searchTimer: null };

  async function openMusic() {
    showPage("musicPage", "music", {
      " ": () => control("toggle"), k: () => control("toggle"), l: () => control("next"), j: () => control("prev"),
      "/": () => $("muSearch").focus(),
    });
    mu.open = true;
    await api().music_open(false);
    tickMusic();
    clearInterval(mu.timer);
    mu.timer = setInterval(tickMusic, 1000);
    loadLibrary();
  }

  function closedMusic() { mu.open = false; clearInterval(mu.timer); }

  async function tickMusic() {
    if (!mu.open) return;
    mu.st = await api().music_state();
    $("musicEq").hidden = !mu.st.playing;
    renderNow();
  }

  function renderNow() {
    const s = mu.st || {};
    const has = !!s.title;
    $("muNow").classList.toggle("idle", !has);
    $("muArt").style.backgroundImage = s.art ? `url("${s.art}")` : "";
    $("muTitle").textContent = has ? s.title : s.open ? (s.ready ? "Nada tocando" : "Abrindo o YouTube Music…") : "YouTube Music";
    $("muArtist").textContent = s.ad ? "Anúncio do YouTube…" : has ? [s.artist, s.album].filter(Boolean).join(" · ") : "Escolha uma playlist abaixo";
    $("muToggle").innerHTML = s.playing ? ICON.pause : ICON.play;
    $("muLike").innerHTML = s.liked ? ICON.heartOn : ICON.heart;
    $("muLike").classList.toggle("on", !!s.liked);
    $("muLike").hidden = !has;
    const pct = s.duration ? (s.time / s.duration) * 100 : 0;
    $("muBar").style.setProperty("--p", `${pct}%`);
    $("muTime").textContent = has && s.duration ? `${clock(s.time)} / ${clock(s.duration)}` : "";
    if (s.volume != null && document.activeElement !== $("muVol")) $("muVol").value = s.volume;
    $("muLogin").hidden = !(s.ready && !s.loggedIn);
    $("muWindow").textContent = s.visible ? "Esconder janela" : "Abrir janela do YouTube Music";
  }

  async function control(action, value = null) {
    mu.st = await api().music_control(action, value);
    renderNow();
  }

  async function loadLibrary() {
    $("muPlaylists").innerHTML = `<p class="shots-empty">Carregando suas playlists…</p>`;
    const r = await api().music_library();
    if (!r.ok) { $("muPlaylists").innerHTML = `<p class="shots-empty">${esc(r.error)} <button class="link-btn" data-retry>Tentar de novo</button></p>`;
      $("muPlaylists").querySelector("[data-retry]").addEventListener("click", loadLibrary); return; }
    mu.lib = r.data;
    if (!r.data.loggedIn) {
      $("muPlaylists").innerHTML = `<p class="shots-empty">Entre na sua conta para ver suas playlists.</p>`;
      $("muShelves").innerHTML = "";
      $("muLogin").hidden = false;
      return;
    }
    $("muPlaylists").innerHTML = r.data.playlists.map(card).join("");
    $("muShelves").innerHTML = r.data.shelves.map((s) => `<h3 class="mu-sub">${esc(s.title)}</h3><div class="mu-row">${s.items.map(card).join("")}</div>`).join("");
    bindCards($("musicPage"));
  }

  const card = (i) => `<button class="mu-card" data-pl="${esc(i.playlist)}" data-v="${esc(i.video)}" title="${esc(i.title)}">
    <div class="mu-img">${i.image ? `<img src="${esc(i.image)}" alt="" loading="lazy">` : `<span>${i.playlist === "LM" ? ICON.heartOn : ICON.play}</span>`}<i>${ICON.play}</i></div>
    <strong>${esc(i.title)}</strong><small>${esc(i.subtitle)}</small></button>`;

  const row = (i) => `<button class="mu-line" data-pl="${esc(i.playlist)}" data-v="${esc(i.video)}">
    ${i.image ? `<img src="${esc(i.image)}" alt="" loading="lazy">` : "<span></span>"}<div><strong>${esc(i.title)}</strong><small>${esc(i.subtitle)}</small></div>${ICON.play}</button>`;

  function bindCards(root) {
    root.querySelectorAll("[data-pl]:not([data-bound])").forEach((b) => {
      b.dataset.bound = "1";
      b.addEventListener("click", async () => {
        await api().music_play(b.dataset.pl, b.dataset.v);
        Sfx.play("open");
        toast(`Tocando ${b.querySelector("strong")?.textContent || ""}`);
        setTimeout(tickMusic, 2500);
      });
    });
  }

  async function search(q) {
    if (!q.trim()) { $("muResults").innerHTML = ""; $("muResults").hidden = true; return; }
    $("muResults").hidden = false;
    $("muResults").innerHTML = `<p class="shots-empty">Buscando…</p>`;
    const r = await api().music_search(q);
    if ($("muSearch").value !== q) return;
    if (!r.ok) { $("muResults").innerHTML = `<p class="shots-empty">${esc(r.error)}</p>`; return; }
    $("muResults").innerHTML = r.data.length ? `<div class="mu-lines">${r.data.map(row).join("")}</div>` : `<p class="shots-empty">Nada encontrado.</p>`;
    bindCards($("muResults"));
  }

  window.onMusic = (st) => {
    if (st.open === false) { mu.st = {}; $("musicEq").hidden = true; if (mu.open) renderNow(); return; }
    mu.st = { ...mu.st, ...st };
    if (mu.open) renderNow();
  };

  // ondinhas no botão de música enquanto toca (vale também com a página fechada)
  setInterval(async () => {
    if (mu.open || document.hidden || !window.pywebview?.api) return;
    const st = await api().music_state();
    $("musicEq").hidden = !st.playing;
  }, 4000);

  // ============================================================== ligações

  function bind() {
    $("discordBtn").addEventListener("click", openDiscord);
    $("musicBtn").addEventListener("click", openMusic);
    $("dcConnect").addEventListener("click", connect);
    $("dcPortal").addEventListener("click", () => api().discord_portal());
    $("dcMute").addEventListener("click", () => setVoice("mute"));
    $("dcDeaf").addEventListener("click", () => setVoice("deaf"));
    $("dcLeave").addEventListener("click", async () => { const r = await api().discord_leave(); if (!r.ok) dcError(r.error); else { Sfx.play("close"); loadCall(); loadChannels(); } });
    $("muPrev").innerHTML = ICON.prev;
    $("muNext").innerHTML = ICON.next;
    $("muToggle").addEventListener("click", () => control("toggle"));
    $("muNext").addEventListener("click", () => control("next"));
    $("muPrev").addEventListener("click", () => control("prev"));
    $("muLike").addEventListener("click", () => control("like"));
    $("muVol").addEventListener("input", () => api().music_control("volume", Number($("muVol").value)));
    $("muBar").addEventListener("click", (e) => {
      const r = $("muBar").getBoundingClientRect();
      if (mu.st.duration) control("seek", ((e.clientX - r.left) / r.width) * mu.st.duration);
    });
    $("muWindow").addEventListener("click", async () => { if (mu.st.visible) await api().music_hide(); else await api().music_open(true); tickMusic(); });
    $("muLoginBtn").addEventListener("click", async () => { await api().music_open(true); tickMusic(); });
    $("muReload").addEventListener("click", loadLibrary);
    $("muStop").addEventListener("click", async () => { await api().music_close(); mu.st = {}; renderNow(); });
    $("muSearch").addEventListener("input", () => { clearTimeout(mu.searchTimer); mu.searchTimer = setTimeout(() => search($("muSearch").value), 450); });
    $("muSearch").addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); $("muSearch").value = ""; search(""); $("muSearch").blur(); }
      if (e.key === "Enter") { e.preventDefault(); clearTimeout(mu.searchTimer); search($("muSearch").value); }
    });
    // fechar a página (Esc/B ou o X) para o relógio do player
    new MutationObserver(() => { if (!$("musicPage").classList.contains("show")) closedMusic(); })
      .observe($("musicPage"), { attributes: true, attributeFilter: ["class"] });
    new MutationObserver(() => { if (!$("discordPage").classList.contains("show")) closedDiscord(); })
      .observe($("discordPage"), { attributes: true, attributeFilter: ["class"] });
    document.addEventListener("keydown", (e) => {
      if (topLayer() || e.ctrlKey || e.altKey || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "d" || e.key === "D") { e.preventDefault(); openDiscord(); }
      if (e.key === "y" || e.key === "Y") { e.preventDefault(); openMusic(); }
    });
  }

  window.openDiscordPage = openDiscord;
  window.openMusicPage = openMusic;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
  window.addEventListener("pywebviewready", () => setTimeout(updateCallDot, 1500));
})();
