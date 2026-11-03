"""Backup dos dados do GameDock num .zip (biblioteca, troféus, sessões, perfil, capturas).

Sem conta online, é isso que protege o progresso se o PC for formatado. As capas baixadas
ficam de fora (o app baixa de novo sozinho); capturas e capas personalizadas entram.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from .storage import data_dir

# dentro de art/, só o que não dá para baixar de novo
KEEP_IN_ART = ("/captures/", "/trophies/", "/custom-cover", "/_profile/")


def backups_dir() -> Path:
    folder = Path.home() / "Documents" / "GameDock" / "Backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make_backup() -> Path:
    root = data_dir()
    dest = backups_dir() / f"GameDock-{datetime.now():%Y-%m-%d_%H-%M}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix == ".tmp":
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith("art/") and not any(k in "/" + rel for k in KEEP_IN_ART):
                continue
            if rel.startswith("trophies/cache/"):
                continue
            zf.write(path, rel)
    return dest
