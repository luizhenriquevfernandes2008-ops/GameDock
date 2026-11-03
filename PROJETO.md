# GameDock

Biblioteca unificada de jogos para Windows: um único app que **encontra sozinho** todos os
jogos instalados (Steam, Epic, GOG, Ubisoft, Riot, Google Play Games, jogos soltos em pastas...),
coloca tudo num só lugar e permite **dar play** em qualquer um, com uma interface própria
no estilo console.

> Status: **v0.5.1** (a v0.5.0 trazia um sistema de downloads, removido na 0.5.1). Desde a v0.4: backup de saves, atualização automática pelo GitHub, PS1/PS2/NES, Modo Jogo, desafios e
> uso completo pelo controle. Código e versões em github.com/luizhenriquevfernandes2008-ops/GameDock.
> (O projeto se chamava *GameHub* na v0.1; a pasta continua `C:\Users\luizv\GameHub`.)


### Ideias futuras (registradas, ainda não feitas)

- **GameDock no celular**: QR code que abre uma página no celular (Wi-Fi de casa) com tempos, troféus e capturas,
  e que abre jogos no PC. O servidor local já existe; precisaria escutar na rede local com um código de pareamento.
- **Jogar na TV / no celular**: integração com Sunshine (servidor) + Moonlight (cliente), grátis.
- **Versão em inglês**: tirar os textos da interface para um arquivo de tradução (pt-BR / en).

### Fase final — contas, social e saves na nuvem (planejado, decidido em 03/11/2026)

Público: poucas pessoas (~3), cada uma no próprio PC com o próprio login.

**Contas e social → Firebase** (Auth + Firestore), plano grátis "Spark", sem cartão.
- Por que não Supabase: o plano grátis pausa o projeto depois de ~1 semana sem uso. O Firebase não pausa.
- Login: e-mail e senha (confirmação de e-mail, "esqueci minha senha", troca de e-mail/senha, e-mails em PT-BR enviados
  pelo próprio Firebase) + "Entrar com Google" (pelo navegador do sistema: o Google bloqueia login dentro de WebView).
  Discord: dá para adicionar depois com uma função no servidor (exige o plano Blaze).
- No app: a interface (WebView2) usa o SDK JavaScript do Firebase, guardado localmente (login persistente, Firestore em
  tempo real). O app só leva a configuração pública (`apiKey`, `projectId`...); quem protege os dados são as
  **regras de segurança** do Firestore (cada um só escreve nos próprios dados; perfil visível conforme a privacidade).
  Testar as regras tentando ler/escrever dados de outra conta.
- Sincronizado: perfil (nome de usuário único, foto, banner, bio, cor, vitrine, nível/XP), troféus, tempo de jogo,
  estatísticas. O perfil local atual migra para a conta no primeiro login. Sem conta, o GameDock continua funcionando.
- Social: buscar pessoas, pedido de amizade, perfil dos amigos, **"jogando agora"** ao vivo (com aviso quando um amigo
  abre um jogo), feed (platinas, troféus raros, jogos zerados), comparar troféus de um jogo, privacidade
  (público / amigos / privado), bloquear e denunciar.
- Fotos de perfil/banner: pequenas (redimensionadas no PC, poucos KB), guardadas no próprio Firestore, para não precisar
  do Cloud Storage (que exige o plano Blaze com cartão).
- LGPD: política de privacidade + termos (rascunho), botão "apagar minha conta" que apaga tudo de verdade, contato.

**Saves → Google Drive da própria pessoa** (não no Firebase).
- "Conectar Google Drive" (OAuth no navegador, permissão `drive.file`: o GameDock só enxerga os arquivos que ele mesmo
  criou). Quem entrou com Google pode conectar na mesma hora; quem usa e-mail e senha conecta à parte.
- Pasta `GameDock/Saves/<jogo>/` no Drive, com o mesmo zip do backup local de hoje (`saves.py`) + um manifesto
  (hash, data, nome do PC).
- **Ao fechar o jogo**: o backup sobe automaticamente (igual ao anterior = não sobe de novo).
- **Ao abrir o jogo**: se o Drive tiver um save mais novo vindo de **outro PC**, pergunta "Usar o save do PC X de
  <data>?" antes de o jogo começar. Os dois lados mudaram → mostra os dois (data, PC) e guarda uma cópia do que não for
  escolhido. Nunca apaga sozinho, nunca sincroniza com o jogo aberto.
- Histórico das últimas N versões por jogo (restaurar pela página do jogo), também os saves/memory cards dos emuladores.
- Token do Google guardado criptografado com a DPAPI do Windows.
- Custo zero para o projeto: o espaço é o da pessoa (15 GB grátis do Google).

**A fazer pelo dono do projeto** (com guia passo a passo): criar o projeto no console do Firebase, ativar Auth
(e-mail/senha + Google) e Firestore; criar no Google Cloud (mesmo projeto) a credencial OAuth "app para computador" e
ativar a Google Drive API; `npx firebase-tools login` para publicar as regras. Enquanto o app OAuth estiver em
"teste", só entram os e-mails cadastrados como testadores (até 100), o que basta para ~3 pessoas.

---

## Roadmap

| Fase | Entrega | Status |
|------|---------|--------|
| v0.1 | Detecção automática, biblioteca persistente, iniciar jogos, configs via CLI | ✅ |
| v0.2 | Interface estilo console, cor dinâmica, capas automáticas, `GameDock.exe` | ✅ |
| v0.3 | App de verdade (instalador, bandeja, instância única), troféus, capturas, clipes, perfil, PS3 | ✅ |
| **v0.4** | Saves, atualização automática, logs, PS1/PS2/NES + RetroAchievements, Modo Jogo, Alt+G completo, status/anotações/tempo para zerar, desafios, editor de mídia, primeira abertura, controle em tudo | ✅ |
| final | Perfis, contas e backup de saves na nuvem | ⏳ |

---

## Identidade visual

**Referências:** home do PS5, dashboard do Xbox, Steam Big Picture.

| Elemento | Decisão |
|---|---|
| Layout | Fundo em tela cheia com a arte do jogo selecionado; logo, informações e botão **Jogar** à esquerda; **prateleira horizontal** de capas embaixo, com o item selecionado sempre ancorado à esquerda (como no PS5) |
| Cor | Base quase preta `#07070a` + **uma cor de destaque que vem da capa** do jogo selecionado. Tudo que brilha (botão Jogar, contorno da capa, brilho do fundo, ponto do chip) usa essa cor, que transiciona suavemente entre jogos |
| Como a cor é escolhida | A capa é reduzida para 40×60, os pixels são agrupados por matiz e pesados por saturação (ignorando quase-preto/branco). O grupo mais forte vence e a cor é normalizada para ficar legível no escuro. Capa sem cor → destaque branco (ex: DayZ) |
| Tipografia | Segoe UI Variable Display (nativa do Windows 11), pesos 650–850 nos títulos |
| Textura | Grão de filme sutil por cima de tudo (ruído SVG, 7%) e o fundo com um "drift" lento (zoom de 40 s) |
| Capas | Steam: capa, fundo e logo oficiais (cache local da Steam → API `IStoreBrowseService/GetItems`, que informa os caminhos com hash → CDN antigo). **Jogos de fora da Steam que existem na loja** (ex: Castlevania): busca pelo nome, aceitando só nome idêntico, sem pontuação nem sufixo de edição (assim "Lords of Shadow" nunca vira "Lords of Shadow 2"); sem fundo oficial, a 1ª captura de tela da loja vira o fundo. **Jogos antigos sem capa de biblioteca**: a Steam só tem uma capa automática (banner + borrão), então usamos a **capa da caixa do artigo na Wikipedia** (título idêntico, descrito como jogo, imagem vertical); a capa automática só entra em último caso. Cada imagem baixada guarda a URL de origem (`.src`): se a Steam publicar arte nova, o cache se renova sozinho. **Riot** (LoL, VALORANT, 2XKO, TFT, Runeterra): o mesmo tema que o Riot Client usa (veja abaixo). Outros: **capa tipográfica gerada**, com gradiente na cor do nome do jogo e ícone do atalho/.exe |
| Marca | ▶ sobre uma "doca" com gradiente rosa → laranja → ciano (as cores representam a cor que muda a cada jogo). Wordmark `GAME`**`DOCK`** espaçado |
| Janela | Sem borda do Windows: barra de título própria, com relógio, botão de procurar jogos e controles de janela |
| Iniciar jogo | Overlay com blur, capa em destaque, "INICIANDO" e uma barra animada; depois a janela minimiza |
| Sons | **Sintetizados na hora** (Web Audio, `web/sound.js`), sem nenhum arquivo de áudio: "tock" macio ao navegar (com ±15 cents de variação para não soar robótico), batida grave no fim da lista, acorde ascendente + "whoosh" de decolagem + grave descendo ao **Jogar**, brilho de 3 notas ao favoritar, sopros ao abrir/fechar painel. **Assinatura de abertura**: Dó maior com sétima abrindo devagar, com brilho por cima. Reverb curto gerado por ruído. Liga/desliga, som de abertura e volume nas Configurações; tecla **M** para mudo |
| Controles | Teclado (←→, Enter, F, R, M, F11), mouse (clique, duplo clique, roda) e **controle** via Gamepad API (direcional/analógico, A joga, Y favorita). As dicas no rodapé trocam para os botões do controle quando ele é conectado |

### Tempo de jogo (`playtime.py`)

Um monitor olha os processos a cada 5 s (`processes.py`, via Win32, ~10 ms): um jogo está rodando quando algum
processo tem o `.exe` dentro da pasta de instalação dele (crash handlers, anti-cheat e afins são ignorados).
Funciona **mesmo se o jogo for aberto fora do GameDock**, desde que ele esteja rodando. O tempo é somado a cada
verificação e salvo a cada minuto (nada se perde se o PC desligar); a sessão termina após ~10 s sem o processo.
Para jogos da Steam, o total do `localconfig.vdf` entra como ponto de partida (ex: Deadlock 68 h, ULTRAKILL 48 h).

### Troféus (`achievements.py`, `live.py`, `overlay.py`)

| Origem | Esquema (nome, descrição, ícone) | Progresso |
|---|---|---|
| Jogos da Steam | cache local `appcache/stats/UserGameStatsSchema_<appid>.bin` (nomes em PT-BR) | `UserGameStats_<conta>_<appid>.bin` (bits + horário) |
| Outros jogos que existem na Steam | JSON embutido na página pública de conquistas da comunidade Steam | arquivos locais de conquistas que o jogo grava (`.ini`/`.json` em vários formatos, lidos de forma tolerante) |

- Os `.bin` da Steam usam o KeyValues **binário** da Valve: leitor próprio em `binvdf.py`.
- **Níveis** pela raridade global (API pública `GetGlobalAchievementPercentagesForApp`): <10% ouro, <35% prata,
  senão bronze. Completar tudo dá a **platina**.
- A primeira leitura de cada jogo é só a **linha de base**: o que já estava desbloqueado não vira aviso.
- Durante uma sessão, os arquivos de progresso são vigiados a cada 2 s. Desbloqueio novo →
  1. **captura de tela** do monitor do jogo (antes do aviso, para sair limpa) → `art/<jogo>/trophies/*.jpg`;
  2. **aviso por cima do jogo**: janela própria que nunca recebe foco (`WS_EX_NOACTIVATE` + `SW_SHOWNOACTIVATE`),
     fora da barra de tarefas, com cantos arredondados do DWM, som por nível e brilho;
  3. o launcher atualiza a contagem e o painel (tecla **T**) mostra o troféu com a miniatura da captura.
- A janela do aviso é criada *visível fora da tela*: criada escondida, o WebView2 não desenha nada.
- Limitação: jogos em **tela cheia exclusiva** podem sair pretos na captura e esconder o aviso (o Windows não deixa
  sobrepor/copiar); em **janela sem borda** (padrão da maioria dos jogos atuais) funciona.

### App de verdade (v0.3)

| Recurso | Como funciona |
|---|---|
| **Instalador** | `dist\GameDock-Setup.exe` (onefile com o app dentro, janela tkinter): instala em `%LOCALAPPDATA%\Programs\GameDock`, atalhos no Menu Iniciar (e na Área de Trabalho, opcional), entrada em *Configurações → Aplicativos* com desinstalador, e "iniciar com o Windows" opcional. Sem administrador. Se o GameDock estiver aberto, pergunta antes de fechá-lo. **Extras opcionais** (`extras.py`): emuladores (PS3 já com o firmware oficial, PS2, PS1, NES), BIOS do PS1/PS2 a partir de uma pasta do usuário (reconhecido pelo tamanho: 512 KB / 4 MB; o GameDock nunca baixa BIOS) e **DS4Windows** 3.9.9 do fork do schmaldeo (o original saiu do ar; versão e SHA-256 fixos no código porque a busca do GitHub está cheia de cópias falsas) em `%LOCALAPPDATA%\Programs\DS4Windows`, com o .NET 8 da Microsoft se faltar. Os extras rodam numa thread; uma falha num extra não desfaz a instalação |
| **Desinstalar** | `GameDock.exe --uninstall` (chamado pelo Windows): remove atalhos, registro e pasta; os dados (troféus, capturas, perfil) só saem se você confirmar |
| **Bandeja** | fechar (✕ ou Alt+F4) manda para a bandeja (desligável). Menu: abrir, jogar um dos recentes, capturar, iniciar com o Windows, sair. Depois de 15 s escondido, a interface é trocada por uma página vazia (`sleep.html`) e o WebView2 devolve a memória; ao abrir, recarrega sem o som de abertura |
| **Depois do play** | a janela vai para a bandeja (e libera a memória) em vez de só minimizar |
| **Instância única** | mutex do Windows; abrir de novo pede para a cópia aberta aparecer (pelo servidor local) e sai |
| **Iniciar com o Windows** | chave `Run` do usuário, com `--tray`: abre direto na bandeja, sem carregar a interface |
| **Janela** | lembra tamanho e posição; redimensiona pelas bordas (bordas invisíveis no HTML que repassam o arrasto ao Windows com `WM_NCLBUTTONDOWN`) |

### Clipes próprios (replay buffer) — substitui a Game Bar

`recorder.py`: enquanto um jogo roda (e a opção está ligada), o `ffmpeg` grava a tela com `ddagrab` (Desktop
Duplication) direto no codificador da placa de vídeo (NVENC na RTX 3050; AMF/QSV/x264 como reserva, escolhido por
teste), em pedaços de 2 s num anel. O som do PC vem do loopback do WASAPI (`soundcard`), guardado na memória.
**Alt+F10** (configurável) junta os últimos 15/30/60/120 s + o áudio do mesmo intervalo em `.mp4` (sem recodificar o
vídeo) em `Vídeos\GameDock\<jogo>\`. O ffmpeg (build "essentials" do gyan.dev, ~110 MB) é baixado na primeira vez,
com SHA-256 conferido. Configurações → Clipes de vídeo.

### Emuladores (`emulators.py`)

Cada console é uma subclasse de `Emulator` (baixar, instalar extras, achar jogos, montar a linha de comando).
Jogos precisam ser cópias dos discos do próprio usuário; o GameDock não baixa jogos.

- **PS3 — RPCS3**: baixa a versão oficial mais recente (GitHub `RPCS3/rpcs3-binaries-win`, SHA-256 do release
  conferido; o `.7z` usa BCJ2, então a extração é feita pelo `tar.exe` do Windows), ajusta o `CurrentSettings.ini`
  (sem tela de boas-vindas, sem confirmação ao sair, sem checar atualização) e instala o **firmware oficial da Sony**
  (`PS3UPDAT.PUP` da lista de atualização do PS3) com `rpcs3 --installfw`. Jogos: pastas do usuário (formato de disco,
  com `PS3_GAME/PARAM.SFO`) e jogos instalados em `dev_hdd0/game`. Nome e ID vêm do `PARAM.SFO`; a categoria filtra
  atualizações/DLCs. Abrir: `rpcs3.exe --no-gui [--fullscreen] <EBOOT.BIN>` — o jogo abre direto, sem a janela do
  emulador. Arte: `PIC1.PNG` (fundo) e `ICON0.PNG` do próprio jogo + capa do SteamGridDB. O tempo de jogo usa o PID
  do processo que o GameDock abriu.
- **ISO**: `.iso` descriptografado é lido sem montar (`iso9660.py` acha `PS3_GAME/PARAM.SFO`, ícone e fundo).
  Ao jogar, o Windows monta a imagem só leitura (`Mount-DiskImage`), o RPCS3 abre `X:\PS3_GAME\USRDIR\EBOOT.BIN` e,
  quando ele fecha, a imagem é desmontada. ISO criptografado (dump Redump "cru") gera aviso nas Configurações.
- **Armadilha do PyInstaller**: o app empacotado chama `SetDllDirectory(_internal)` e **processos filhos herdam isso**
  — o RPCS3 carregava o `vcruntime140.dll` de dentro do GameDock e recusava rodar. Toda abertura de programa externo
  (jogos, RPCS3, ffmpeg) passa por `launcher.clean_dll_search()`, que zera a pasta de DLLs só durante a criação.
- **Controle automático** (ligado por padrão): antes de cada jogo, o GameDock detecta o controle conectado e grava
  o perfil global do Jogador 1 (`config/input_configs/global/Default.yml`, vale para todos os jogos). Com um conversor
  rodando (DS4Windows, DSX, InputMapper, x360ce, BetterJoy) e um controle XInput presente, usa o **XInput** (senão o
  jogo receberia o DualSense físico e o Xbox virtual ao mesmo tempo); sem conversor, usa o handler nativo do
  DualSense/DualShock 4; controle de Xbox → XInput. Jogadores 2–7 ficam desligados. Os valores vão **entre aspas**:
  em YAML ` #` começa comentário, e `Device: XInput Pad #1` virava "XInput Pad" (o RPCS3 caía no NullPadHandler —
  visto no `log/RPCS3.log`). Controle desligado na hora de configurar → usa o último detectado.
- **Firmware**: o RPCS3 instala e *continua aberto*; o GameDock acompanha os arquivos em `dev_flash` e fecha o RPCS3
  quando eles param de mudar. Instalações antigas presas (`--installfw`) são fechadas antes.
- **Troféus do PS3** (`ps3trophies.py`): o RPCS3 grava os troféus como um PS3 de verdade em
  `dev_hdd0/home/<usuário>/trophy/<NPCOMMID>/`: `TROPCONF.SFM` (XML com nome, descrição, secreto e o nível real
  P/G/S/B), `TROP###.PNG` (ícones) e `TROPUSR.DAT` (binário big-endian; tabela tipo 6 = estado e hora de cada troféu,
  em CellRtcTick, µs desde 01/01/0001). O NPCOMMID vem do próprio disco (`PS3_GAME/TROPDIR/`, lido do ISO sem montar)
  ou, se não der, do `<title-name>` igual ao nome do jogo; fica em cache. O vigia de troféus acompanha o `TROPUSR.DAT`
  → mesmo fluxo da Steam (captura + aviso + XP). A platina do PS3 é um troféu de verdade (`Trophy.grade`), então não
  há o aviso extra de "todos os troféus".
- **Tela cheia sem borda**: antes de cada jogo o GameDock grava `Exclusive Fullscreen Mode: Disable` no
  `config/config.yml` do RPCS3. Em tela cheia exclusiva nada aparece por cima do jogo e a imagem sai preta — o aviso
  de troféu, o Alt+G, a captura (Print Screen) e a gravação de clipes não funcionariam.
- **Launcher**: jogo de emulador só **minimiza** o GameDock (não vai para a bandeja nem descarrega a interface); quando
  o jogo fecha, o GameDock volta para a frente, como num console.
- **PS2 (PCSX2), PS1 (DuckStation), NES (RetroArch + núcleo Mesen)** — `emulators_retro.py`, v0.4: mesmo esquema
  (download oficial com SHA-256 quando publicado, modo portátil em `%LOCALAPPDATA%\GameDock\emulators\<console>`,
  controle, tela cheia sem borda, teclas de save state, token do RetroAchievements). PS1/PS2 precisam do BIOS do
  console do próprio usuário (o GameDock não baixa BIOS).

### Dentro do jogo e retrospectiva

- **Painel Alt+G** (`panel.html`): por cima do jogo, sem tirar o foco (mesma técnica do aviso de troféu; dá para clicar
  sem ativar a janela). Mostra a sessão ao vivo, o tempo total, os troféus com os 3 próximos mais fáceis, as últimas
  capturas e botões de **capturar** (o painel some antes, para não sair na foto) e **gravar clipe**.
- **Clipes** (`clips.py`): usa a gravação em segundo plano do próprio Windows (Xbox Game Bar), para não pesar no jogo.
  "Gravar clipe" aperta Win+Alt+G; os `.mp4` da pasta Capturas entram na galeria a cada 10 s, associados ao jogo pelo
  nome do arquivo (a Game Bar usa o título da janela) ou pelo jogo que estava rodando. O servidor local serve os vídeos
  com `Range` (para avançar). Apagar um clipe na galeria manda o vídeo para a **Lixeira**. Requer "Gravar o que
  aconteceu" ligado em Configurações → Jogos → Capturas (o GameDock avisa e abre essa tela).
- **Retrospectiva** (`recap.py`, tecla W ou Perfil → Retrospectiva): semana ou mês, com navegação para períodos
  anteriores. Tempo total, comparação com o período anterior, mais jogados, sessões, dias com jogo, maior sessão, dia
  mais ativo, gráfico por dia, troféus e capturas do período. **Salvar como imagem** desenha um card 1080×1350 num
  `<canvas>` (só com imagens do próprio app, para poder exportar) e salva em `Imagens\GameDock`.
- **Novidades** na página do jogo: `ISteamNews/GetNewsForApp` (pública), sem notícias em outros alfabetos, cache de 6 h.
- **"Não é um jogo"** (página do jogo → Gerenciar): para de contar tempo e sessões e tira o app do perfil e do XP
  (ex: Wallpaper Engine, que somava 63 h).

### v0.4 — base para a fase final e extras

| Recurso | Como funciona |
|---|---|
| **Registro de erros** (`logs.py`) | `print()` e erros não tratados vão para `%LOCALAPPDATA%\GameDock\logs\gamedock.log` (gira em 1 MB, 3 antigos). Configurações → Sobre: versão, "abrir registro de erros" |
| **Atualização automática** (`updater.py`) | Uma vez por dia lê `releases/latest` do repositório do GitHub; baixa `GameDock-Setup.exe`, confere com o `.sha256` publicado e roda o instalador com `--silent` (espera o GameDock fechar, instala e reabre). `config.json → update_repo` troca o repositório |
| **Backup de saves** (`saves.py`) | Locais de save do manifesto público do **Ludusavi** (milhares de jogos) + pastas de emuladores de Steam. Backup automático ao fechar o jogo em `Documentos\GameDock\Saves` (backup igual ao anterior não repete; guarda os últimos N), botão "Restaurar" na página do jogo |
| **Biblioteca** | Status (Jogando, Zerado, Platinado, Abandonado, Quero jogar) com confete, anotações, **tempo para zerar** pelo IGDB (`timetobeat.py`; o HowLongToBeat não tem API) ou estimativa própria, **espaço em disco** e mover jogo de HD (`disk.py`; só jogos que o GameDock abre direto), **sortear** do "Quero jogar" (tecla S), **grátis da Epic** (`freegames.py`) |
| **Modo Jogo** (`gamemode.py`) | Energia em desempenho máximo, notificações pausadas e apps escolhidos fechados (pedindo, como o X); tudo volta ao fechar o jogo, inclusive se o GameDock cair |
| **Alt+G** (`quickset.py`) | FPS/CPU/GPU/memória, volume, saída de som, brilho (WMI ou DDC/CI), save state dos emuladores, lembrete de pausa |
| **Discord Rich Presence** | "Jogando <jogo>" mesmo para jogos de emulador |
| **RetroAchievements** (`retroach.py`) | Troféus de PS1, PS2 e NES: token vai para os emuladores, progresso lido pela Web API. Capas de console do libretro-thumbnails (`retroart.py`) |
| **Desafios** (`challenges.py`) | Sequência de dias, metas da semana, 16 troféus do próprio GameDock com XP, calendário do ano e mapa dia × hora |
| **Editor de mídia** (`mediaedit.py`) | Cortar clipe, exportar vertical 9:16, recortar captura, copiar e enviar para um webhook do Discord |
| **DualSense** (`dualsense.py`) | Bateria na barra de cima e luz na cor do perfil (HID direto; DS4 e Xbox só bateria) |
| **Visual** | Temas, fundo animado, música do menu dos jogos de PS3, tamanho do texto, cores para daltônicos, reduzir movimento, Modo Console (liga direto em tela cheia) |
| **Primeira abertura** | Apresentação guiada (nome, jogos encontrados, emuladores, controle, dicas); de novo em Configurações → Sobre |
| **Tudo pelo controle** (`web/padnav.js`) | Nas telas com campos o direcional move um destaque, A confirma, B volta; campos de texto abrem um teclado na tela |

### Navegação, páginas e perfil

| Tela | Como abrir | O que tem |
|---|---|---|
| Abas | Q / E (LB / RB) | Todos, Recentes, Favoritos e uma por fonte, com contagem |
| Busca | Ctrl+F ou / | filtra enquanto digita (ignora acentos); Esc limpa |
| **Página do jogo** | ↓ (▼) ou botão ≡ | tempo total, sessões, sessão mais longa, média, gráfico de **horas por dia (30 dias)**, capturas, abrir pasta, argumentos, renomear, ocultar, **trocar capa** |
| Troféus | T (X) | painel com anel de progresso, níveis, raridade e capturas |
| **Galeria** | G ou ícone de câmera | todas as capturas (manuais e de troféu), filtro por jogo, ampliada com ←/→ e apagar |
| **Perfil** | P (View) ou avatar | nível e XP, totais, mais jogados, recordes, troféus recentes, nome e avatar (capa de um jogo) |

- **Página do jogo, estilo loja** (`gameinfo.py`, cache de 7 dias): topo com a arte do jogo, que vira o **trailer sem som**
  depois de ~2 s (como no PS5; o fundo pula a abertura com classificação etária), capa, logo, gêneros, ano e classificação;
  notas **Metacritic** e **avaliações dos jogadores** ("Muito positivas · 80% · 8 mil análises"); faixa com trailers e
  imagens oficiais (abrem em tela cheia); **Sobre o jogo** em PT-BR com "ler mais"; informações (desenvolvedor,
  distribuidora, lançamento, franquia); **sua nota** de 1 a 5 estrelas; progresso; troféus com o **próximo mais fácil**
  (o bloqueado que mais jogadores têm); capturas; gerenciar.
  - Fontes: `appdetails` da Steam (`l=brazilian`) + `IStoreBrowseService/GetItems` (avaliações; a API antiga de
    reviews passou a redirecionar para a página da loja). Fora da Steam (ex: Riot): resumo da Wikipedia em PT (ou EN).
  - Os trailers da Steam agora são só HLS/DASH (sem .mp4): tocam com **hls.js 1.5.15**, empacotado em `web/vendor/`
    (o servidor de vídeo da Steam libera CORS). Não carregamos script da internet: a página tem acesso à API do app.
- **Camadas:** os painéis formam uma pilha. Esc/B sempre fecha o de cima. Teclado e controle passam pela mesma
  função (`handleInput`): cada botão do controle vira uma "tecla".
- **Histórico de sessões:** `sessions.jsonl` (uma linha por sessão). O gráfico começa a ter dados a partir desta versão.
  Sessões que passam da meia-noite são divididas entre os dias.
- **Captura manual:** tecla global (padrão **Print Screen**; também Ctrl+F12, Alt+F12, F10, F9 ou Ctrl+Shift+S).
  O Windows recusa F12 (reservado para depuradores) e Alt/Ctrl+Print Screen. A captura vai para `art/<jogo>/captures/`,
  associada ao jogo que estiver rodando, e mostra um aviso discreto com a miniatura e o som de obturador.
- **Perfil sem conta:** tudo é calculado dos arquivos locais (`player.py`). XP: bronze 15, prata 30, ouro 90,
  platina 300, +10 por hora jogada. Para ir do nível N ao N+1 são 100 + 50×(N−1) XP. Subiu de nível → aviso na tela.
- **Personalização do perfil** (Perfil → *Editar perfil*, ou tecla E no perfil): **foto** (enviar imagem, capa de um
  jogo ou a inicial), **banner** (enviar imagem, arte de um jogo ou o padrão, que é a arte do jogo mais jogado) com
  ajuste da posição do recorte, **nome** e **bio** (120 caracteres), **cor do perfil** (8 cores ou automática, tirada do
  banner), **vitrine** com jogo favorito e até 3 troféus em destaque. A **moldura do avatar** muda com o nível:
  bronze, prata (10+), ouro (25+) e platina (50+, com brilho animado). Imagens enviadas ficam em `art/_profile/`
  (banners grandes são reduzidos para 1920 px) e entram no backup.
- **Backup:** Configurações → Dados gera um `.zip` em `Documentos\GameDock\Backups` (biblioteca, troféus, sessões,
  perfil, capturas e capas personalizadas; as capas baixadas ficam de fora, porque o app baixa de novo sozinho).

### SteamGridDB (capas em alta, opcional)

Com uma chave grátis (⚙ Configurações no app, ou `python -m gamedock steamgriddb <chave>`), o GameDock
busca no [SteamGridDB](https://www.steamgriddb.com):
- **capa 600×900** para todo jogo que não tem capa oficial da biblioteca da Steam: Riot, jogos antigos, jogos locais
  (a capa composta da Riot e a da Wikipedia viram reserva);
- **fundo e logo** que estiverem faltando, inclusive para jogos da Steam.

O jogo é identificado pelo appid da Steam quando existe (casamento exato); se não existir, pela busca por nome, aceitando só
nome idêntico. Os filtros `nsfw/humor/epilepsy=false` ficam ligados e a ordem é a dos votos da comunidade. A chave é
verificada antes de ser salva. Sem chave, ou se ela for inválida, tudo funciona como antes.

### Arte dos jogos da Riot

O Riot Client monta a biblioteca dele a partir de uma configuração pública
(`clientconfig.rpg.riotgames.com/.../config/public?namespace=keystone.products`). Cada produto
aponta para um `theme_manifest` com o card do jogo, o fundo e o logo. O GameDock segue o mesmo caminho:

1. descobre o produto pelo argumento do atalho (`--launch-product=lion` → 2XKO) ou pelo nome;
2. baixa o manifest e as imagens (cache de 7 dias em `art\`); se a configuração estiver fora do ar, usa manifests conhecidos;
3. fundos muito grandes (o do LoL é 4K, com 4 MB) são reduzidos para 1920 px;
4. o card é **horizontal e já tem o logo impresso**, então a capa vertical recorta o lado dos
   personagens (LoL: esquerda; demais: direita) e sobrepõe o logo oficial em cima de um degradê.

---

## Como usar

### App (interface)
- Dê duplo clique em `GameDock.exe` (na área de trabalho ou em `dist\`).
- Na primeira abertura, ele procura os jogos sozinho.

### Gerar e instalar
```powershell
python -m pip install pyinstaller pywebview   # só na primeira vez
.\build.ps1                                   # gera dist\GameDock\ (pasta com GameDock.exe)
.\install.ps1                                 # copia para %LOCALAPPDATA%\Programs\GameDock + atalho na Área de Trabalho
```

### Desempenho da abertura
| Antes | Agora | Por quê |
|---|---|---|
| 2,6 s até a janela | **~1,1 s** | build *onedir* (pasta) em vez de *onefile*, que descompactava 13 MB a cada abertura |
| 9,1 MB de base64 pela ponte Python↔JS | só URLs | servidor local (`127.0.0.1`, porta escolhida pelo Windows) entrega `/art/...` direto ao navegador, em paralelo e com cache |
| scan disputando CPU com as capas | scan depois das capas | o jogo em destaque carrega primeiro e o scan começa quando a primeira tela está pronta |

A primeira abertura depois de instalar leva ~3–4 s porque o Windows Defender verifica os arquivos novos. Isso só acontece uma vez.

### Linha de comando (continua disponível)
```powershell
python -m gamedock scan | list | play <nome> | info <nome>
python -m gamedock add "D:\Jogos\X\x.exe" --name "Meu Jogo" --args "-windowed"
python -m gamedock folders add "D:\Jogos"     # cada subpasta vira um jogo
python -m gamedock set fav|hide|unhide|rename|args <jogo> [valor]
```

Os dados ficam em `%APPDATA%\GameDock\`: `library.json`, `config.json` e `art\` (cache das imagens).
Se existir a pasta antiga `%APPDATA%\GameHub`, ela é renomeada automaticamente.
Pra testar sem mexer nos dados reais, defina `GAMEDOCK_HOME` com outra pasta.

---

## Como cada fonte é detectada

A ideia é **ler os arquivos que os próprios launchers mantêm** em vez de varrer o disco
inteiro atrás de `.exe`. É mais rápido e muito mais preciso.

| Fonte | Onde lê | Como inicia |
|-------|---------|-------------|
| **Steam** | Registro `HKCU\Software\Valve\Steam` → `libraryfolders.vdf` (todas as bibliotecas) → `appmanifest_*.acf` | `steam://rungameid/<appid>` |
| **Epic Games** | `C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item` (JSON) | `com.epicgames.launcher://apps/...?action=launch` |
| **GOG** | Registro `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\*` | executável direto |
| **Ubisoft Connect** | Registro `HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\*` | `uplay://launch/<id>/0` |
| **Riot** (LoL, Valorant, 2XKO) | Atalhos com `RiotClientServices.exe --launch-product=...` + `RiotClientInstalls.json` | Riot Client com os argumentos do atalho |
| **Google Play Games** | Atalhos dentro da pasta "Google Play Games" do Menu Iniciar | o que o atalho apontar |
| **Battle.net / EA / outros** | Atalhos `.lnk`/`.url` com protocolo de jogo ou launcher + argumento de jogo | o que o atalho apontar |
| **Instalados no PC** | Atalhos da Área de Trabalho / Menu Iniciar para um `.exe` qualquer, aprovados pelo **detector de jogos** (abaixo) | o `.exe` do atalho |
| **Pastas soltas** | Pastas configuradas com `folders add`; cada subpasta vira um jogo | `.exe` escolhido por heurística |

Nomes de atalho são limpos dos sufixos que o Windows adiciona (`Jogo - Atalho`, `- Shortcut`, `(2)`), e a busca
de capas compara nomes ignorando espaços (`MoonSweepers` = `Moon Sweepers`; números continuam contando).

O scan roda **toda vez que o app abre**, em segundo plano, e avisa só quando aparece jogo novo.

### Detector de jogos (`gamedock/detect.py`)

Para atalhos que não são de nenhum launcher (ex: *Castlevania: Lords of Shadow* instalado em
`D:\Games`), o GameDock analisa o `.exe` e a pasta e soma pontos. É jogo a partir de **6**.

| Evidência | Pontos |
|---|---|
| Motor de jogo: Unity (`UnityPlayer.dll`, `_Data`), Godot (`.pck`), GameMaker (`data.win`), RPG Maker, Ren'Py, Source; Unreal (`Engine`, `*-Shipping.exe`) | +3 a +6 |
| Middleware: Bink, FMOD, Wwise, PhysX, OpenAL, SDL; SDKs Steam/Epic/GOG ao lado do exe | +2 a +4 |
| Tabela de importação do PE: DirectX/OpenGL/Vulkan, XInput/DirectInput, XAudio, SDK de loja | +1 a +3 |
| Vídeos `.bik/.bk2/.usm`; ≥0,5 GB / ≥4 GB em arquivos de dados grandes | +2 / +2 / +4 |
| Caminho com pasta `Games`/`Jogos` | +2 |
| App Electron/Chromium, instalador Squirrel, pasta do sistema, nome de ferramenta (mod manager, launcher, updater...) | −3 a −10 |

Exemplo real: Castlevania = **28** pontos; Discord, VS Code, Docker, Hydra, Warp e os mod managers ficam
entre −18 e 2. Para ver o raciocínio de qualquer exe: `python -m gamedock detect "<caminho do .exe>"`.
No scan, a análise para no meio quando o resultado já está claro (scan inteiro em ~1 s).

Jogos de launcher (Steam/Epic/Ubisoft) abrem **pelo launcher**, para o DRM, o login, o overlay e
os saves na nuvem funcionarem.

**Heurística do .exe (pastas soltas):** descarta `unins*`, `setup`, `redist`, `crash`,
`easyanticheat`...; +100 se o nome bate com a pasta, +50 se parecido, até +50 pelo tamanho,
+20 para `*-Shipping.exe` (Unreal), −20 para `launcher`, −8 por nível de profundidade; pastas
que contêm o exe de um launcher conhecido são ignoradas.

**Duplicatas:** os scanners rodam em ordem de confiabilidade e um jogo é descartado se repetir
id, comando de launch ou pasta de instalação de um já encontrado.

---

## Arquitetura

```
GameHub/
├── PROJETO.md
├── build.ps1              # gera dist\GameDock.exe
├── run_gamedock.py        # entrada do .exe (abre a interface)
├── assets/
│   ├── make_icon.py       # desenha o ícone (só stdlib) → gamedock.ico / .png
│   └── gamedock.ico
└── gamedock/
    ├── app.py             # janela pywebview (WebView2) + Api exposta ao JavaScript
    ├── art.py             # capa/fundo/logo/ícone → data URIs, com cache
    ├── cli.py             # linha de comando (python -m gamedock)
    ├── models.py          # dataclass Game
    ├── storage.py         # Library (library.json) e config.json
    ├── launcher.py        # inicia o jogo (URI ou exe)
    ├── formats.py         # leitores: VDF/ACF da Valve, .url e .lnk (binário)
    ├── scanners/          # steam, epic, gog, ubisoft, shortcuts, folders
    └── web/
        ├── index.html
        ├── style.css      # toda a identidade visual (tokens no topo)
        └── app.js         # prateleira, cor dinâmica, controles, gamepad
```

**Por que pywebview?** A janela é nativa (WebView2, o motor do Edge, já vem no Windows 11) e a
interface é HTML/CSS, o que dá liberdade total de visual. O backend continua 100% em Python e o
`.exe` final tem ~13 MB (Electron teria ~150 MB).

**Para adicionar uma fonte nova:** crie `scanners/<nome>.py` com uma subclasse de `Scanner`
cujo `scan()` faz `yield Game(...)`, e registre em `build_scanners()`.

### Modelo de dados (`library.json`)
- **Campos detectados** (`name`, `source`, `launch_*`, `install_dir`, `exe`, `icon`): atualizados a cada scan.
- **Campos do usuário** (`custom_name`, `extra_args`, `favorite`, `hidden`, `play_count`, `last_played`): nunca são sobrescritos.
- Jogo que some do scan não é apagado, só fica com `installed: false`.

---

## Limitações conhecidas

- **Xbox / Microsoft Store** ainda não é detectado.
- **EA app** e **Battle.net** só via atalhos; **Google Play Games** ainda não foi validado numa máquina real.
- Jogos fora da Steam ainda não têm capa oficial (usam a capa gerada); o SteamGridDB resolve isso na v0.5.
- A interface ainda não tem busca, filtros nem configurações. Por enquanto, isso é feito pela CLI.
- Ainda não mede tempo de jogo (só conta sessões abertas pelo GameDock).
- O Windows Defender às vezes acusa falsamente executáveis gerados pelo PyInstaller.
- O detector só olha atalhos. Um jogo sem nenhum atalho precisa de `folders add` ou `add`.
- A busca na loja da Steam é conservadora: "Skyrim Special Edition" não casa com "The Elder Scrolls V: Skyrim
  Special Edition". Prefiro ficar sem capa a mostrar a capa errada.
