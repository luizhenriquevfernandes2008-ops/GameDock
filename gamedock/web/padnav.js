// GameDock — usar tudo pelo controle: nas telas com campos (Configurações, editar perfil, página do jogo...)
// o direcional move um destaque entre os itens, A confirma, B volta; nos campos de texto abre um teclado na tela.
// O teclado e o mouse continuam funcionando como antes (isto só entra quando o comando vem do controle).

const PAD_ROOTS = {
  settings: "settings", profileEdit: "profileEdit", gamePage: "gamePage", profile: "profilePage", gallery: "galleryPage",
  recap: "recapPage", free: "freePage", onboarding: "onboarding", random: "randomOverlay", osk: "osk",
  discord: "discordPage", music: "musicPage",
};
const FOCUSABLE = 'button:not([disabled]), input:not([type="checkbox"]):not([disabled]), select:not([disabled]), textarea, label.switch-row, .tp-item, .shot, [data-trailer], [data-shot]';

function padFocusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter((el) => {
    if (el.closest("[hidden]")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && getComputedStyle(el).visibility !== "hidden";
  });
}

function padSetFocus(el) {
  document.querySelectorAll(".pad-focus").forEach((x) => x.classList.remove("pad-focus"));
  if (!el) return;
  if (!el.matches("input, select, textarea, button")) el.tabIndex = -1;
  el.focus({ preventScroll: true });
  el.classList.add("pad-focus");
  el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
}

function padMove(root, dir) {
  const items = padFocusables(root);
  if (!items.length) return false;
  const active = root.contains(document.activeElement) && document.activeElement.classList.contains("pad-focus") ? document.activeElement : null;
  if (!active) { padSetFocus(items[0]); Sfx.play("move"); return true; }
  const a = active.getBoundingClientRect();
  const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of items) {
    if (el === active) continue;
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const dx = x - ax, dy = y - ay;
    const ok = dir === "ArrowRight" ? dx > 4 && r.left >= a.left + 2 : dir === "ArrowLeft" ? dx < -4 && r.right <= a.right - 2
      : dir === "ArrowDown" ? dy > 4 && r.top >= a.top + 2 : dy < -4 && r.bottom <= a.bottom - 2;
    if (!ok) continue;
    const along = dir === "ArrowRight" || dir === "ArrowLeft" ? Math.abs(dx) : Math.abs(dy);
    const across = dir === "ArrowRight" || dir === "ArrowLeft" ? Math.abs(dy) : Math.abs(dx);
    const score = along + across * 2.2;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (!best) return false;
  padSetFocus(best);
  Sfx.play("move");
  return true;
}

function padAdjust(el, dir) {
  const step = dir === "ArrowRight" ? 1 : -1;
  if (el.tagName === "SELECT") {
    const i = Math.max(0, Math.min(el.options.length - 1, el.selectedIndex + step));
    if (i === el.selectedIndex) return Sfx.play("edge");
    el.selectedIndex = i;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    Sfx.play("move");
    return true;
  }
  if (el.type === "range") {
    el.value = Number(el.value) + step * Number(el.step || 1);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  return false;
}

function padActivate(el) {
  if (el.matches("label.switch-row")) {
    const box = el.querySelector("input[type=checkbox]");
    if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change", { bubbles: true })); }
    return;
  }
  if (el.matches('input:not([type="range"]), textarea')) return openOsk(el);
  if (el.tagName === "SELECT") return padAdjust(el, "ArrowRight");
  el.click();
}

// teclado e mouse seguem pelo caminho normal; aqui só o que vem do controle (sem evento de teclado)
const _handleInput = handleInput;
handleInput = function (key, e) {
  if (!e && padNav(key)) return;
  return _handleInput(key, e);
};

function padNav(key) {
  const top = topLayer();
  const rootId = top && PAD_ROOTS[top.name];
  if (!rootId) return false;
  const root = $(rootId);
  if (top.name === "osk") return oskKey(key);
  const active = root.contains(document.activeElement) && document.activeElement.classList.contains("pad-focus") ? document.activeElement : null;
  if (key.startsWith("Arrow")) {
    if (active && (key === "ArrowLeft" || key === "ArrowRight") && (active.tagName === "SELECT" || active.type === "range")) return padAdjust(active, key);
    if (padMove(root, key)) return true;
    return false;   // nada nessa direção: a tela rola (teclas da própria tela)
  }
  if (key === "Enter" && active) { padActivate(active); return true; }
  return false;
}

// ---------------------------------------------------------------- teclado na tela

const OSK_ROWS = ["1234567890", "qwertyuiop", "asdfghjklç", "zxcvbnm,.-@"];
const osk = { target: null, shift: false };

function openOsk(input) {
  osk.target = input;
  osk.shift = false;
  $("oskInput").value = input.value;
  $("oskInput").type = input.type === "password" ? "password" : "text";
  paintOsk();
  $("osk").classList.add("show");
  pushLayer("osk", () => closeOsk(false), {});
  padSetFocus($("oskKeys").querySelector("button"));
}

function paintOsk() {
  const rows = OSK_ROWS.map((r) => [...r].map((c) => `<button type="button" data-k="${c}">${osk.shift ? c.toUpperCase() : c}</button>`).join(""));
  rows.push(`<button type="button" data-k="shift" class="wide ${osk.shift ? "on" : ""}">⇧ Maiúscula</button><button type="button" data-k=" " class="space">Espaço (Y)</button>
             <button type="button" data-k="back" class="wide">⌫ Apagar (X)</button><button type="button" data-k="ok" class="wide ok">OK</button>`);
  $("oskKeys").innerHTML = rows.map((r) => `<div class="osk-row">${r}</div>`).join("");
  $("oskKeys").querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => oskPress(b.dataset.k)));
}

function oskPress(k) {
  const field = $("oskInput");
  if (k === "ok") return closeOsk(true);
  if (k === "back") field.value = field.value.slice(0, -1);
  else if (k === "shift") { osk.shift = !osk.shift; const focused = document.activeElement?.dataset.k; paintOsk(); padSetFocus($("oskKeys").querySelector(`[data-k="${focused}"]`)); }
  else field.value += osk.shift ? k.toUpperCase() : k;
  Sfx.play("move");
}

function oskKey(key) {
  const root = $("osk");
  if (key.startsWith("Arrow")) { padMove(root, key) || Sfx.play("edge"); return true; }
  if (key === "Enter") { const a = document.activeElement; if (a?.dataset.k) oskPress(a.dataset.k); return true; }
  if (key === "t") { oskPress("back"); return true; }    // X
  if (key === "f") { oskPress(" "); return true; }       // Y
  if (key === "Menu") { closeOsk(true); return true; }   // Start = OK
  return false;
}

function closeOsk(save) {
  $("osk").classList.remove("show");
  popLayer("osk");
  const target = osk.target;
  osk.target = null;
  if (!target) return;
  if (save && target.value !== $("oskInput").value) {
    target.value = $("oskInput").value;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
  }
  padSetFocus(target);
}
