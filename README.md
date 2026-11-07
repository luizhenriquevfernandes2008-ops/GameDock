<p align="center"><img src="assets/gamedock.png" width="120" alt="GameDock"></p>

<h1 align="center">GameDock</h1>

<p align="center">
  <b>Biblioteca unificada de jogos para Windows.</b><br>
  Encontra sozinho os jogos da Steam, Epic, GOG, Ubisoft, Riot e de pastas soltas, junta tudo num só lugar e abre qualquer um com uma interface no estilo console.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/pywebview-WebView2-0078D4" alt="pywebview">
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white" alt="Windows">
  <a href="https://github.com/luizhenriquevfernandes2008-ops/GameDock/releases/latest"><img src="https://img.shields.io/github/v/release/luizhenriquevfernandes2008-ops/GameDock?label=vers%C3%A3o" alt="Versão"></a>
</p>

---

## O que ele faz

- **Detecção automática** de jogos em vários launchers lendo os arquivos que eles próprios mantêm (registro do Windows, manifestos VDF/ACF/JSON, atalhos `.lnk`/`.url`), sem varrer o disco inteiro. O scan completo leva cerca de 1 segundo.
- **Detector de jogos por pontuação** para executáveis soltos: analisa o motor (Unity, Unreal, Godot...), middleware, a tabela de importação do PE e o tamanho dos dados para decidir se um `.exe` é um jogo.
- **Tempo de jogo** medido pelos processos (via Win32), mesmo quando o jogo é aberto fora do GameDock.
- **Troféus** da Steam lidos do formato binário KeyValues da Valve (leitor próprio), com aviso por cima do jogo e captura de tela no momento do desbloqueio.
- **Backup de saves**, emuladores (PS1, PS2, PS3, NES) com RetroAchievements, clipes, Modo Jogo e uso completo pelo controle (Gamepad API).
- **Integrações**: Discord (calls e voz) e YouTube Music dentro do app.
- **Atualização automática** pelas Releases do GitHub, com verificação de hash SHA-256.

## Destaques técnicos

| Tema | Como foi resolvido |
|---|---|
| Interface nativa e leve | pywebview (WebView2) com HTML/CSS/JS; o `.exe` final tem ~13 MB, contra ~150 MB de um app Electron |
| Abertura rápida | build *onedir* do PyInstaller e servidor local para as imagens: de 2,6 s para ~1,1 s até a janela |
| Formatos binários | leitores próprios para VDF binário da Valve, `.lnk` do Windows e ISO 9660 |
| Cor dinâmica | a cor de destaque vem da capa do jogo (agrupamento por matiz pesado por saturação) |
| Extensível | cada fonte é um `Scanner` com `scan()` que gera `Game(...)`; adicionar uma loja nova é criar um arquivo |
| Sons | sintetizados na hora com Web Audio, sem nenhum arquivo de áudio |

## Instalar

Baixe o `GameDock-Setup.exe` na página de [Releases](https://github.com/luizhenriquevfernandes2008-ops/GameDock/releases/latest) e execute.

## Rodar a partir do código

```powershell
python -m pip install pywebview pystray pillow psutil
python run_gamedock.py            # abre a interface
python -m gamedock scan           # linha de comando: scan | list | play <nome> | info <nome>
```

Para gerar o instalador: `.\build.ps1` (requer PyInstaller).
Para testar sem mexer nos dados reais, defina a variável `GAMEDOCK_HOME` com outra pasta.

## Estrutura

```
gamedock/
  app.py          janela pywebview + API exposta ao JavaScript
  scanners/       steam, epic, gog, ubisoft, atalhos, pastas
  detect.py       detector de jogos por pontuação
  playtime.py     tempo de jogo pelos processos
  achievements.py troféus (Steam e arquivos locais)
  binvdf.py       leitor do KeyValues binário da Valve
  saves.py        backup de saves
  updater.py      atualização automática pelo GitHub
  web/            interface (HTML, CSS e JavaScript puro)
```

A documentação completa de cada decisão, o roadmap e as limitações conhecidas estão em [PROJETO.md](PROJETO.md).
