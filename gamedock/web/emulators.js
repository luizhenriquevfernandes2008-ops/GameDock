// GameDock — emuladores nas Configurações: baixar, instalar firmware, pastas de jogos e opções.

async function loadEmulators() {
  renderEmulators(await api().list_emulators());
}

function renderEmulators(list) {
  $("emuList").innerHTML = list.map((e) => {
    const t = e.task || {};
    const pct = t.total ? ` ${Math.round((t.done / t.total) * 100)}%` : "";
    const step = (ok, label, button) =>
      `<div class="emu-step"><span class="${ok ? "ok" : "todo"}">${ok ? "✓" : "○"} ${label}</span>${button || ""}</div>`;
    const busy = t.running ? "disabled" : "";
    return `
      <div class="emu-card" data-emu="${e.id}">
        <header><strong>${esc(e.platform)}</strong><span>${esc(e.name)}${e.version ? " · " + esc(e.version) : ""}</span></header>
        ${t.running ? `<p class="emu-progress">${esc(t.label || "Trabalhando…")}${pct}</p>` : ""}
        ${t.error ? `<p class="settings-status error" style="margin:0 0 10px !important">${esc(t.error)}</p>` : ""}
        ${(e.warnings || []).map((w) => `<p class="settings-status error" style="margin:0 0 10px !important">${esc(w)}</p>`).join("")}
        <div class="emu-steps">
          ${step(e.installed, e.installed ? "Emulador instalado" : "Emulador",
                 `<button type="button" class="ghost-btn" data-act="install" ${busy}>${e.installed ? "Atualizar" : "Baixar e instalar"}</button>`)}
          ${e.needsFirmware ? step(e.firmware, e.firmware ? "Firmware oficial instalado" : "Firmware oficial da Sony",
                 e.installed && !e.firmware ? `<button type="button" class="ghost-btn" data-act="firmware" ${busy}>Instalar firmware</button>` : "") : ""}
          ${step(e.folders.length > 0, "Pasta dos seus jogos", `<button type="button" class="ghost-btn" data-act="folder" ${e.installed ? "" : "disabled"}>Adicionar pasta</button>`)}
        </div>
        ${e.folders.length ? `<div class="emu-folders">${e.folders.map((f) => `
          <div class="emu-folder"><code title="${esc(f)}">${esc(f)}</code><button type="button" class="link-btn" data-remove="${esc(f)}">remover</button></div>`).join("")}</div>` : ""}
        <label class="switch-row" style="margin-bottom:8px !important">
          <span>Abrir os jogos em tela cheia <small>o jogo abre direto, sem a janela do emulador</small></span>
          <input type="checkbox" data-opt="fullscreen" ${e.fullscreen ? "checked" : ""}><i class="switch"></i>
        </label>
        ${e.installed ? `<div class="emu-step" style="margin-bottom:10px"><span class="${e.controller && !e.controller.startsWith("Nenhum") ? "ok" : "todo"}">🎮 ${esc(e.controller || "Controle ainda não detectado")}</span><button type="button" class="ghost-btn" data-act="controller">Detectar controle</button></div><label class="switch-row" style="margin-bottom:8px !important"><span>Configurar o controle sozinho <small>antes de cada jogo, o controle conectado vira o Jogador 1</small></span><input type="checkbox" data-opt="auto_controller" ${e.autoController ? "checked" : ""}><i class="switch"></i></label>` : ""}
        ${e.installed ? `<button type="button" class="link-btn" data-act="open">Abrir o ${esc(e.name)} completo (ajustes avançados) ↗</button>` : ""}
      </div>`;
  }).join("");

  $("emuList").querySelectorAll(".emu-card").forEach((card) => {
    const id = card.dataset.emu;
    card.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.act === "folder") {
        const s = await api().emulator_add_folder(id);
        renderEmulators(await api().list_emulators());
        if (s.found !== undefined) {
          toast(s.found ? `${s.found} jogo(s) de ${s.platform} encontrados` : "Nenhum jogo encontrado nessa pasta");
          if (s.found) scan({ silent: true });
        }
        return;
      }
      const s = await api().emulator_action(id, b.dataset.act);
      Sfx.play("move");
      if (b.dataset.act === "controller") { toast(s.controller || "Nenhum controle"); return renderEmulators([s]); }
      pollEmulators();
    }));
    card.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
      renderEmulators([await api().emulator_remove_folder(id, b.dataset.remove)].concat(
        (await api().list_emulators()).filter((x) => x.id !== id)));
      scan({ silent: true });
    }));
    card.querySelector("[data-opt=auto_controller]")?.addEventListener("change", async (e) => {
      renderEmulators([await api().emulator_set(id, { auto_controller: e.target.checked })]);
    });
    card.querySelector("[data-opt=fullscreen]").addEventListener("change", async (e) => {
      await api().emulator_set(id, { fullscreen: e.target.checked });
      scan({ silent: true });  // a linha de comando dos jogos muda
    });
  });
}

// acompanha downloads/instalações em andamento
async function pollEmulators() {
  const list = await api().list_emulators();
  renderEmulators(list);
  if (list.some((e) => e.task?.running) && state.settingsOpen) setTimeout(pollEmulators, 800);
  else if (list.some((e) => e.installed)) scan({ silent: true });
}
