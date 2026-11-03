from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime


# Campos que vêm do scanner e podem ser atualizados a cada novo scan.
# Os demais (custom_name, favorite, hidden, play_count...) pertencem ao usuário
# e nunca são sobrescritos por um rescan.
DETECTED_FIELDS = (
    "name",
    "source",
    "launch_type",
    "launch_target",
    "launch_args",
    "working_dir",
    "install_dir",
    "exe",
    "icon",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


STATUSES = ("playing", "beaten", "platinum", "abandoned", "backlog")


@dataclass
class Game:
    id: str                      # estável, ex: "steam:221100"
    name: str
    source: str                  # steam, epic, gog, ubisoft, riot, google, folder, manual...
    launch_type: str             # "uri" (steam://, com.epicgames.launcher://...) ou "exe"
    launch_target: str
    launch_args: list[str] = field(default_factory=list)
    working_dir: str | None = None
    install_dir: str | None = None
    exe: str | None = None
    icon: str | None = None      # .ico/.png do atalho, quando existir

    # configurações do usuário
    custom_name: str | None = None
    extra_args: list[str] = field(default_factory=list)
    favorite: bool = False
    rating: int | None = None    # sua nota, 1 a 5 estrelas
    track: bool = True           # False = "não é um jogo" (ex: Wallpaper Engine): sem tempo, sessões nem XP
    status: str | None = None    # playing | beaten | platinum | abandoned | backlog (veja STATUSES)
    status_at: str | None = None
    notes: str = ""              # anotações livres ("parei no chefe do capítulo 3")
    beat_hours: float | None = None   # tempo para zerar que o próprio usuário digitou (se o IGDB não souber)
    hidden: bool = False
    manual: bool = False

    # estado
    installed: bool = True
    play_count: int = 0          # sessões detectadas
    playtime_seconds: int = 0    # tempo total de jogo (inclui o que a Steam já tinha registrado)
    last_played: str | None = None
    added_at: str = field(default_factory=now_iso)

    @property
    def display_name(self) -> str:
        return self.custom_name or self.name

    @property
    def launch_signature(self) -> tuple:
        """Usado para remover duplicatas (ex: atalho .url da Steam x scanner da Steam)."""
        return (self.launch_type, self.launch_target.lower(), tuple(self.launch_args))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Game":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
