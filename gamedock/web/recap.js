// GameDock — retrospectiva da semana/mês e o card para salvar como imagem (1080×1350, formato de post).

async function openRecap(period = state.recapPeriod || "week", offset = 0) {
  state.recapPeriod = period;
  state.recapOffset = offset;
  if (!$("recapPage").classList.contains("show")) {
    showPage("recapPage", "recap", {
      ArrowLeft: () => openRecap(state.recapPeriod, state.recapOffset + 1),
      ArrowRight: () => state.recapOffset > 0 && openRecap(state.recapPeriod, state.recapOffset - 1),
      q: () => openRecap("week", 0),
      e: () => openRecap("month", 0),
    });
  }
  const data = await api().get_recap(period, offset);
  state.recap = data;
  renderRecap(data);
}

function renderRecap(d) {
  $("rcLabel").textContent = d.label;
  $("rcPeriods").querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.period === d.period));
  $("rcNext").disabled = d.isCurrent;
  $("rcHeroImg").src = d.top[0]?.hero || "";
  $("rcTotal").textContent = d.total ? fmtHours(d.total) : "0 h";
  let compare = d.isCurrent ? "até agora" : "";
  if (d.previous) {
    const pct = Math.round(((d.total - d.previous) / d.previous) * 100);
    const word = d.period === "week" ? "semana anterior" : "mês anterior";
    compare = `<span class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "+" : ""}${pct}%</span> em relação à ${word}`;
  } else if (!d.total) {
    compare = "Nenhuma sessão registrada nesse período.";
  }
  $("rcCompare").innerHTML = compare;
  $("rcTop").innerHTML = d.top.length ? `<em>Mais jogados</em>` + d.top.slice(0, 3).map((g) =>
    `<div>${g.cover ? `<img src="${g.cover}" alt="">` : "<i></i>"}<strong>${esc(g.name)}</strong><span>${fmtHours(g.seconds)}</span></div>`).join("") : "";

  const c = d.trophyCounts;
  $("rcTiles").innerHTML = [
    [d.sessions, d.sessions === 1 ? "sessão" : "sessões"],
    [`${d.daysPlayed}/${d.daysTotal}`, "dias com jogo"],
    [d.longest ? fmtHours(d.longest.seconds) : "—", d.longest ? `maior sessão · ${esc(d.longest.game)}` : "maior sessão"],
    [d.busiestDay || "—", "dia mais ativo"],
    [d.trophyTotal, `troféus <div class="dots"><span><i style="--tier:${TIER_COLORS.gold}"></i>${c.gold}</span><span><i style="--tier:${TIER_COLORS.silver}"></i>${c.silver}</span><span><i style="--tier:${TIER_COLORS.bronze}"></i>${c.bronze}</span></div>`],
  ].map(([v, l]) => `<div class="tile-stat"><b class="${/^[\d—]/.test(String(v)) ? "" : "text"}">${esc(v)}</b><span>${l}</span></div>`).join("");

  renderChart($("rcChart"), d.days);
  $("rcTrophies").innerHTML = d.trophies.length ? d.trophies.map((t) => `
    <div class="rt" style="--tier:${TIER_COLORS[t.tier]}">${t.icon ? `<img src="${t.icon}" alt="">` : ""}
      <div><strong>${esc(t.name)}</strong><span>${esc(t.game)} · ${fmtDate(t.at)}</span></div></div>`).join("")
    : `<p class="shots-empty">Nenhum troféu nesse período.</p>`;
  renderShots($("rcShots"), d.captures.map((s) => ({ ...s, kind: "manual", game: "" })), "Nenhuma captura nesse período.");
}

// ---------------------------------------------------------------- card para compartilhar

function loadImage(src) {
  return new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;  // só imagens do próprio GameDock (/art/...), para o canvas poder ser exportado
  });
}

function drawCover(ctx, img, x, y, w, h) {
  const scale = Math.max(w / img.width, h / img.height);
  const sw = w / scale, sh = h / scale;
  ctx.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

async function saveRecapImage() {
  const d = state.recap, p = state.profile;
  if (!d) return;
  const W = 1080, H = 1350;
  const canvas = Object.assign(document.createElement("canvas"), { width: W, height: H });
  const ctx = canvas.getContext("2d");
  const accent = getComputedStyle($("recapPage")).getPropertyValue("--accent").trim() || "#8b7bff";
  const font = (weight, size) => `${weight} ${size}px "Segoe UI Variable Display", "Segoe UI", sans-serif`;

  ctx.fillStyle = "#07070a";
  ctx.fillRect(0, 0, W, H);
  const hero = await loadImage(d.top[0]?.hero);
  if (hero) {
    ctx.globalAlpha = 0.55;
    drawCover(ctx, hero, 0, 0, W, 760);
    ctx.globalAlpha = 1;
  }
  let g = ctx.createLinearGradient(0, 0, 0, 820);
  g.addColorStop(0, "rgba(7,7,10,.25)");
  g.addColorStop(1, "#07070a");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, 820);
  g = ctx.createRadialGradient(80, H, 0, 80, H, 900);
  g.addColorStop(0, accent.replace("hsl(", "hsla(").replace(")", " / .35)"));
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);

  const left = 84;
  ctx.fillStyle = "#f4f4f6";
  ctx.font = font(800, 30);
  ctx.letterSpacing = "10px";
  ctx.fillText("GAMEDOCK", left, 120);
  ctx.letterSpacing = "6px";
  ctx.fillStyle = accent;
  ctx.font = font(750, 24);
  ctx.fillText("RETROSPECTIVA", left, 170);
  ctx.letterSpacing = "0px";
  ctx.fillStyle = "rgba(244,244,246,.75)";
  ctx.font = font(600, 34);
  ctx.fillText(d.label, left, 220);

  ctx.fillStyle = "#f4f4f6";
  ctx.font = font(900, 210);
  ctx.fillText(d.total ? fmtHours(d.total) : "0 h", left - 8, 520);
  ctx.font = font(600, 36);
  ctx.fillStyle = "rgba(244,244,246,.7)";
  ctx.fillText("jogadas", left, 580);

  // números do período
  const stats = [[d.sessions, d.sessions === 1 ? "sessão" : "sessões"], [`${d.daysPlayed}/${d.daysTotal}`, "dias com jogo"],
                 [d.trophyTotal, d.trophyTotal === 1 ? "troféu" : "troféus"]];
  stats.forEach(([v, l], i) => {
    const x = left + i * 310;
    roundRect(ctx, x, 640, 280, 150, 26);
    ctx.fillStyle = "rgba(255,255,255,.06)";
    ctx.fill();
    ctx.fillStyle = "#f4f4f6";
    ctx.font = font(850, 64);
    ctx.fillText(String(v), x + 30, 725);
    ctx.fillStyle = "rgba(244,244,246,.6)";
    ctx.font = font(600, 26);
    ctx.fillText(l, x + 30, 765);
  });

  // mais jogados, com capa e barra
  ctx.fillStyle = accent;
  ctx.font = font(750, 22);
  ctx.letterSpacing = "6px";
  ctx.fillText("MAIS JOGADOS", left, 870);
  ctx.letterSpacing = "0px";
  const max = Math.max(1, ...d.top.map((t) => t.seconds));
  for (const [i, t] of d.top.slice(0, 3).entries()) {
    const y = 905 + i * 120;
    const cover = await loadImage(t.cover);
    if (cover) {
      ctx.save();
      roundRect(ctx, left, y, 64, 96, 10);
      ctx.clip();
      drawCover(ctx, cover, left, y, 64, 96);
      ctx.restore();
    }
    ctx.fillStyle = "#f4f4f6";
    ctx.font = font(750, 32);
    ctx.fillText(t.name.length > 30 ? t.name.slice(0, 29) + "…" : t.name, left + 90, y + 38);
    roundRect(ctx, left + 90, y + 58, (W - left * 2 - 260) * (t.seconds / max), 14, 7);
    ctx.fillStyle = accent;
    ctx.fill();
    ctx.fillStyle = "rgba(244,244,246,.7)";
    ctx.font = font(650, 28);
    ctx.textAlign = "right";
    ctx.fillText(fmtHours(t.seconds), W - left, y + 74);
    ctx.textAlign = "left";
  }
  if (!d.top.length) {
    ctx.fillStyle = "rgba(244,244,246,.55)";
    ctx.font = font(600, 30);
    ctx.fillText("Nenhuma sessão registrada nesse período.", left, 950);
  }

  // rodapé: quem é
  ctx.fillStyle = "rgba(244,244,246,.55)";
  ctx.font = font(650, 26);
  ctx.fillText(`${p?.name || ""} · nível ${p?.level ?? ""}`, left, H - 70);
  ctx.textAlign = "right";
  ctx.fillText("feito com GameDock", W - left, H - 70);
  ctx.textAlign = "left";

  const name = `GameDock ${d.label}`;
  const result = await api().save_image(canvas.toDataURL("image/png"), name);
  Sfx.play("shutter");
  toast(result.ok ? `Imagem salva em ${result.path}` : "Não foi possível salvar a imagem", !result.ok);
}

function bindRecap() {
  $("pfRecap").addEventListener("click", () => openRecap("week", 0));
  $("rcPeriods").querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => openRecap(b.dataset.period, 0)));
  $("rcPrev").addEventListener("click", () => openRecap(state.recapPeriod, state.recapOffset + 1));
  $("rcNext").addEventListener("click", () => state.recapOffset > 0 && openRecap(state.recapPeriod, state.recapOffset - 1));
  $("rcSave").addEventListener("click", saveRecapImage);
  $("rcOpenFolder").addEventListener("click", () => api().open_pictures());
}
