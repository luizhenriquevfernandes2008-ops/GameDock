from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from . import __version__, sgdb
from .detect import THRESHOLD, analyze
from .launcher import LaunchError, launch
from .models import Game
from .scanners import build_scanners, run_scanners, scanned_sources
from .scanners.base import slug
from .storage import Library, data_dir, load_config, save_config


def pick_one(library: Library, query: str) -> Game:
    matches = library.find(query)
    if not matches:
        sys.exit(f"Nenhum jogo encontrado para '{query}'.")
    if len(matches) > 1:
        print(f"'{query}' bate com vários jogos, seja mais específico ou use o id:")
        for g in matches:
            print(f"  {g.id:<32} {g.display_name}")
        sys.exit(1)
    return matches[0]


# ---------------------------------------------------------------- comandos

def cmd_scan(args) -> None:
    config = load_config()
    library = Library()
    scanners = build_scanners(config)
    games, errors = run_scanners(scanners)
    sources = scanned_sources(scanners, errors) | {g.source for g in games}
    report = library.merge_scan(games, sources)
    library.save()

    print(f"Scan concluído: {len(games)} jogos detectados.")
    for label, key in (("Novos", "new"), ("Atualizados", "updated"), ("Não encontrados (desinstalados?)", "missing")):
        if report[key]:
            print(f"\n{label}:")
            for g in report[key]:
                print(f"  + [{g.source}] {g.display_name}")
    for name, err in errors.items():
        print(f"\n[aviso] scanner '{name}' falhou: {err}")


def cmd_list(args) -> None:
    library = Library()
    games = library.visible(include_hidden=args.all, include_missing=args.all)
    if args.source:
        games = [g for g in games if g.source == args.source]
    if not games:
        print("Biblioteca vazia. Rode o comando 'scan' primeiro.")
        return
    print(f"{'':2} {'NOME':<38} {'FONTE':<10} {'JOGADO':>6}  ID")
    for g in games:
        flags = ("*" if g.favorite else " ") + ("h" if g.hidden else ("x" if not g.installed else " "))
        print(f"{flags} {g.display_name[:38]:<38} {g.source:<10} {g.play_count:>6}  {g.id}")
    print(f"\n{len(games)} jogos   (* favorito, h oculto, x não instalado)")


def cmd_play(args) -> None:
    library = Library()
    game = pick_one(library, args.game)
    print(f"Iniciando {game.display_name}...")
    try:
        launch(game)
    except (LaunchError, OSError) as exc:
        sys.exit(f"Falha ao iniciar: {exc}")
    library.save()


def cmd_info(args) -> None:
    game = pick_one(Library(), args.game)
    for key, value in game.to_dict().items():
        print(f"{key:<14} {value}")


def cmd_add(args) -> None:
    exe = Path(args.exe).resolve()
    if not exe.is_file():
        sys.exit(f"Arquivo não existe: {exe}")
    library = Library()
    game = Game(
        id=f"manual:{slug(str(exe))}",
        name=args.name or exe.stem,
        source="manual",
        launch_type="exe",
        launch_target=str(exe),
        launch_args=shlex.split(args.args or "", posix=False),
        working_dir=str(exe.parent),
        install_dir=str(exe.parent),
        exe=str(exe),
        manual=True,
    )
    library.games[game.id] = game
    library.save()
    print(f"Adicionado: {game.name}  ({game.id})")


def cmd_remove(args) -> None:
    library = Library()
    game = pick_one(library, args.game)
    if not game.manual:
        # jogos detectados voltariam no próximo scan; ocultar é o comportamento certo
        game.hidden = True
        print(f"{game.display_name} foi ocultado (jogos detectados voltam no scan; use 'unhide' para reverter).")
    else:
        del library.games[game.id]
        print(f"{game.display_name} removido.")
    library.save()


def cmd_set(args) -> None:
    library = Library()
    game = pick_one(library, args.game)
    if args.action == "fav":
        game.favorite = not game.favorite
    elif args.action == "hide":
        game.hidden = True
    elif args.action == "unhide":
        game.hidden = False
    elif args.action == "rename":
        game.custom_name = args.value or None
    elif args.action == "args":
        game.extra_args = shlex.split(args.value or "", posix=False)
        if game.launch_type == "uri":
            print("[aviso] este jogo abre pelo launcher; argumentos extras só valem para jogos 'exe'.")
    library.save()
    print(f"OK: {game.display_name}")


def cmd_detect(args) -> None:
    verdict = analyze(Path(args.exe))
    print(f"{verdict.exe}\npasta do jogo: {verdict.root}\n")
    for reason in verdict.reasons or ["(nenhuma evidência)"]:
        print(f"  {reason}")
    print(f"\npontuação {verdict.score} (mínimo {THRESHOLD}) → {'É JOGO' if verdict.is_game else 'não parece jogo'}")


def cmd_steamgriddb(args) -> None:
    config = load_config()
    if args.key:
        if not sgdb.check_key(args.key):
            sys.exit("Chave recusada pelo SteamGridDB.")
        config["steamgriddb_key"] = args.key
        save_config(config)
        print("Chave do SteamGridDB salva.")
    else:
        print("Chave configurada." if config.get("steamgriddb_key") else f"Sem chave. Pegue uma em {sgdb.KEY_PAGE}")


def cmd_folders(args) -> None:
    config = load_config()
    folders: list[str] = config["scan_folders"]
    if args.action == "add":
        path = str(Path(args.path).resolve())
        if path not in folders:
            folders.append(path)
    elif args.action == "remove":
        path = str(Path(args.path).resolve())
        config["scan_folders"] = [f for f in folders if f.lower() != path.lower()]
    save_config(config)
    print("Pastas escaneadas:" if config["scan_folders"] else "Nenhuma pasta configurada.")
    for f in config["scan_folders"]:
        print(f"  {f}")


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gamedock", description="Biblioteca unificada de jogos.")
    p.add_argument("--version", action="version", version=f"GameDock {__version__} — dados em {data_dir()}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="detecta jogos instalados").set_defaults(func=cmd_scan)

    s = sub.add_parser("list", help="lista a biblioteca")
    s.add_argument("--source", help="filtra por fonte (steam, epic, riot...)")
    s.add_argument("--all", action="store_true", help="inclui ocultos e desinstalados")
    s.set_defaults(func=cmd_list)

    for name, func, help_ in (("play", cmd_play, "inicia um jogo"),
                              ("info", cmd_info, "mostra detalhes de um jogo"),
                              ("remove", cmd_remove, "remove/oculta um jogo")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("game", help="id ou parte do nome")
        s.set_defaults(func=func)

    s = sub.add_parser("add", help="adiciona um .exe manualmente")
    s.add_argument("exe")
    s.add_argument("--name")
    s.add_argument("--args", help='argumentos, ex: "-windowed -nosound"')
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("set", help="configura um jogo")
    s.add_argument("action", choices=["fav", "hide", "unhide", "rename", "args"])
    s.add_argument("game")
    s.add_argument("value", nargs="?", help="novo nome / argumentos")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("detect", help="explica se um .exe parece jogo e por quê")
    s.add_argument("exe")
    s.set_defaults(func=cmd_detect)

    s = sub.add_parser("steamgriddb", help="configura a chave do SteamGridDB (capas em alta)")
    s.add_argument("key", nargs="?")
    s.set_defaults(func=cmd_steamgriddb)

    s = sub.add_parser("folders", help="pastas extras para procurar jogos")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("path", nargs="?")
    s.set_defaults(func=cmd_folders)
    return p


def interactive() -> None:
    """Modo usado quando o GameDock.exe é aberto com duplo clique (sem argumentos)."""
    print(f"GameDock {__version__}\n")
    if not Library().games:
        print("Primeira execução, procurando jogos...\n")
        cmd_scan(None)
        print()
    while True:
        games = Library().visible()
        for i, g in enumerate(games, 1):
            print(f"  {i:>3}. {'* ' if g.favorite else ''}{g.display_name}  [{g.source}]")
        print("\n  número = jogar   s = escanear de novo   q = sair")
        try:
            choice = input("> ").strip("﻿ \t").lower()
        except (EOFError, KeyboardInterrupt):
            return
        if choice in ("q", "sair", ""):
            return
        if choice == "s":
            cmd_scan(None)
        elif choice.isdigit() and 1 <= int(choice) <= len(games):
            library = Library()
            game = library.games[games[int(choice) - 1].id]
            print(f"Iniciando {game.display_name}...")
            try:
                launch(game)
                library.save()
            except (LaunchError, OSError) as exc:
                print(f"Falha ao iniciar: {exc}")
        else:
            print("Opção inválida.")
        print()


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not (sys.argv[1:] if argv is None else argv):
        interactive()
        return
    args = build_parser().parse_args(argv)
    if args.command == "folders" and args.action != "list" and not args.path:
        sys.exit("Informe a pasta.")
    args.func(args)
