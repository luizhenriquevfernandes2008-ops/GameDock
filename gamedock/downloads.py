"""Gerenciador de downloads: links diretos (HTTP com retomada) e torrents/magnets (libtorrent).

Cada download vai para <pasta de downloads>/<nome do jogo>/. O estado fica em downloads.json
(e o "fastresume" dos torrents em downloads/<id>.resume), então fechar o GameDock no meio não
perde nada: ao abrir de novo, o que estava baixando continua.

Depois de baixar: extrair (.zip nativo; .7z/.rar com o 7-Zip instalado no PC), abrir o instalador
que veio junto (só quando o usuário clica) e adicionar a pasta à biblioteca.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
import urllib.request

from . import __version__
from .storage import _read_json, _write_json, data_dir, load_config, save_config

try:
    import libtorrent as lt
except ImportError:  # sem libtorrent o GameDock só baixa links diretos
    lt = None

CHUNK = 1024 * 1024
ARCHIVES = (".zip", ".7z", ".rar")
SETUP_NAMES = re.compile(r"^(setup|install|installer|start ?here)[^\\/]*\.exe$", re.IGNORECASE)
ACTIVE = ("queued", "downloading", "metadata", "extracting")
SEVEN_ZIP = [Path(os.environ.get(v, "")) / "7-Zip" / "7z.exe" for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
NO_WINDOW = 0x08000000


def default_dir() -> str:
    return str(Path.home() / "Downloads" / "GameDock")


def downloads_dir() -> str:
    return load_config().get("downloads_dir") or default_dir()


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or "download"


def seven_zip() -> Path | None:
    return next((p for p in SEVEN_ZIP if p.is_file()), None) or (Path(shutil.which("7z")) if shutil.which("7z") else None)


def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


class DownloadManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._path = data_dir() / "downloads.json"
        self._resume_dir = data_dir() / "downloads"
        self._resume_dir.mkdir(exist_ok=True)
        self.jobs: dict[str, dict] = {j["id"]: j for j in _read_json(self._path, [])}
        self._runtime: dict[str, dict] = {}      # velocidade, pares... (não vai para o disco)
        self._stop: dict[str, threading.Event] = {}
        self._handles: dict[str, object] = {}    # id -> torrent_handle
        self._session = None
        self.on_change = None                     # app.py liga isto à interface
        self._closing = False
        self._last_save = 0.0
        for job in self.jobs.values():
            if job["status"] in ("downloading", "metadata", "queued"):
                job["status"] = "queued"          # retoma ao abrir
            elif job["status"] == "extracting":
                job["status"] = "done"            # extração interrompida: o usuário pode extrair de novo
        threading.Thread(target=self._loop, name="downloads", daemon=True).start()

    # ------------------------------------------------------------ estado

    def _save(self) -> None:
        with self._lock:
            _write_json(self._path, list(self.jobs.values()))
            self._last_save = time.time()

    def _set(self, job_id: str, **changes) -> None:
        with self._lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(changes)
                self._save()
        self._notify()

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.summary())
            except Exception:
                pass

    def summary(self) -> dict:
        with self._lock:
            jobs = [dict(j, **self._runtime.get(j["id"], {})) for j in self.jobs.values()]
        active = [j for j in jobs if j["status"] in ACTIVE]
        return {"active": len(active), "speed": sum(j.get("speed", 0) for j in active),
                "progress": (sum(j["done"] for j in active) / max(1, sum(j["total"] or 0 for j in active))) if active else None}

    def list(self) -> list[dict]:
        with self._lock:
            jobs = [dict(j, **self._runtime.get(j["id"], {})) for j in self.jobs.values()]
        for job in jobs:
            if job["status"] == "done":
                job.update(self._after_info(job))
        return sorted(jobs, key=lambda j: (j["status"] not in ACTIVE, -j["created"]))

    # ------------------------------------------------------------ ações

    def start(self, option: dict, uri: str, folder: str | None = None) -> dict:
        if not isinstance(uri, str) or not uri.lower().startswith(("magnet:", "http://", "https://")):
            return {"ok": False, "error": "link não suportado"}
        is_torrent = uri.lower().startswith("magnet:") or urlparse(uri).path.lower().endswith(".torrent")
        if is_torrent and lt is None:
            return {"ok": False, "error": "o suporte a torrent (libtorrent) não está instalado"}
        with self._lock:
            if any(j["uri"] == uri and j["status"] != "error" for j in self.jobs.values()):
                return {"ok": False, "error": "esse download já está na lista"}
        root = Path(folder or downloads_dir())
        name = _safe_name(option.get("name") or option.get("title") or "download")
        target, n = root / name, 2
        while target.exists() and any(target.iterdir()):   # nunca baixa por cima de uma pasta que já existe
            target, n = root / f"{name} ({n})", n + 1
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"não deu para criar a pasta: {exc}"}
        need = option.get("bytes") or 0
        free = shutil.disk_usage(root).free
        if need and need * 1.05 > free:
            return {"ok": False, "error": f"espaço insuficiente em {root.anchor} (precisa de ~{need / 2**30:.1f} GB, "
                                          f"tem {free / 2**30:.1f} GB livres)"}
        job = {"id": uuid.uuid4().hex[:12], "name": name, "title": option.get("title") or name,
               "source": option.get("source") or "", "uri": uri, "kind": "torrent" if is_torrent else "http",
               "dir": str(target), "root": str(root), "file": None, "status": "queued", "error": None,
               "total": need or 0, "done": 0, "created": time.time(), "finished": None, "extracted": False}
        with self._lock:
            self.jobs[job["id"]] = job
            self._save()
        cfg = load_config()
        if folder and cfg.get("downloads_dir") != str(root):
            cfg["downloads_dir"] = str(root)
            save_config(cfg)
        self._notify()
        return {"ok": True, "job": job}

    def pause(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or job["status"] not in ACTIVE or job["status"] == "extracting":
            return
        if job["kind"] == "torrent" and job_id in self._handles:
            handle = self._handles[job_id]
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()
            handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        if job_id in self._stop:
            self._stop[job_id].set()
        self._runtime.pop(job_id, None)
        self._set(job_id, status="paused")

    def resume(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job["status"] in ("paused", "error"):
            if job["kind"] == "torrent" and job_id in self._handles:
                handle = self._handles[job_id]
                handle.set_flags(lt.torrent_flags.auto_managed)
                handle.resume()
                self._set(job_id, status="downloading", error=None)
            else:
                self._set(job_id, status="queued", error=None)

    def cancel(self, job_id: str, delete_files: bool = True) -> None:
        """Tira da lista. Com delete_files, apaga também o que já foi baixado (só dentro da pasta do download)."""
        job = self.jobs.get(job_id)
        if not job:
            return
        if job_id in self._stop:
            self._stop[job_id].set()
        handle = self._handles.pop(job_id, None)
        if handle is not None and self._session is not None:
            self._session.remove_torrent(handle)
        (self._resume_dir / f"{job_id}.resume").unlink(missing_ok=True)
        with self._lock:
            self.jobs.pop(job_id, None)
            self._runtime.pop(job_id, None)
            self._save()
        if delete_files and job["status"] != "done":
            folder, root = Path(job["dir"]), Path(job.get("root") or downloads_dir())
            # só apaga a pasta que o próprio GameDock criou para esse download (sempre vazia no início)
            if folder.is_dir() and folder.parent == root:
                def remove():
                    time.sleep(1.5)   # o libtorrent solta os arquivos
                    shutil.rmtree(folder, ignore_errors=True)
                threading.Thread(target=remove, daemon=True).start()
        self._notify()

    # ------------------------------------------------------------ depois de baixar

    def _after_info(self, job: dict) -> dict:
        folder = Path(job["dir"])
        archives, setups = [], []
        if folder.is_dir():
            for path in self._walk(folder, 2):
                low = path.name.lower()
                if low.endswith(ARCHIVES) and not re.search(r"\.part(?!0*1\.rar$)\d+\.rar$", low):
                    archives.append(str(path))
                elif SETUP_NAMES.match(path.name):
                    setups.append(str(path))
        return {"archives": archives[:5], "setups": setups[:3], "canExtract": bool(archives),
                "sevenZip": bool(seven_zip()), "exists": folder.is_dir()}

    @staticmethod
    def _walk(folder: Path, depth: int):
        try:
            for entry in folder.iterdir():
                if entry.is_file():
                    yield entry
                elif entry.is_dir() and depth > 0:
                    yield from DownloadManager._walk(entry, depth - 1)
        except OSError:
            return

    def extract(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job or job["status"] != "done":
            return {"ok": False, "error": "o download ainda não terminou"}
        archives = [Path(a) for a in self._after_info(job)["archives"]]
        if not archives:
            return {"ok": False, "error": "nenhum arquivo compactado na pasta"}
        if any(a.suffix.lower() != ".zip" for a in archives) and not seven_zip():
            return {"ok": False, "error": "para .rar/.7z instale o 7-Zip (7-zip.org) e tente de novo"}
        self._set(job_id, status="extracting", error=None)
        threading.Thread(target=self._extract_worker, args=(job_id, archives), daemon=True).start()
        return {"ok": True}

    def _extract_worker(self, job_id: str, archives: list[Path]) -> None:
        job = self.jobs[job_id]
        target = Path(job["dir"])
        try:
            for index, archive in enumerate(archives):
                base = index / len(archives)
                if archive.suffix.lower() == ".zip":
                    with zipfile.ZipFile(archive) as zf:
                        members = zf.infolist()
                        for n, member in enumerate(members):
                            dest = target / member.filename
                            if not _inside(target, dest):   # "zip slip": caminho saindo da pasta
                                raise ValueError(f"caminho inválido dentro do zip: {member.filename}")
                            zf.extract(member, target)
                            if n % 20 == 0:
                                self._runtime[job_id] = {"extract": base + n / max(1, len(members)) / len(archives)}
                else:
                    # -p sem senha: se o arquivo pedir senha, falha em vez de esperar uma digitação
                    proc = subprocess.Popen([str(seven_zip()), "x", str(archive), f"-o{target}", "-y", "-p", "-bsp1", "-bb0"],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            creationflags=NO_WINDOW, text=True, errors="replace")
                    buffer = ""
                    while True:
                        char = proc.stdout.read(1)
                        if not char:
                            break
                        buffer = (buffer + char)[-200:]
                        m = re.findall(r"(\d+)%", buffer)
                        if m:
                            self._runtime[job_id] = {"extract": base + int(m[-1]) / 100 / len(archives)}
                    if proc.wait() != 0:
                        raise ValueError("o 7-Zip não conseguiu extrair (arquivo com senha ou incompleto?)")
            self._runtime.pop(job_id, None)
            self._set(job_id, status="done", extracted=True)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._runtime.pop(job_id, None)
            self._set(job_id, status="done", error=f"extração: {exc}")

    def delete_archives(self, job_id: str) -> int:
        """Depois de extrair: apaga os compactados para liberar espaço."""
        job = self.jobs.get(job_id)
        if not job or not job.get("extracted"):
            return 0
        freed = 0
        for path in map(Path, self._after_info(job)["archives"]):
            stem = re.sub(r"\.part0*1$", "", path.stem, flags=re.IGNORECASE)
            for part in path.parent.glob(f"{glob_escape(stem)}*{path.suffix}"):
                if _inside(Path(job["dir"]), part):
                    freed += part.stat().st_size
                    part.unlink(missing_ok=True)
        self._notify()
        return freed

    def run_setup(self, job_id: str, path: str) -> dict:
        job = self.jobs.get(job_id)
        if not job or path not in self._after_info(job)["setups"]:
            return {"ok": False, "error": "instalador não encontrado"}
        try:
            os.startfile(path)   # pede administrador sozinho se o instalador precisar
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------ laço principal

    def _loop(self) -> None:
        while not self._closing:
            try:
                self._tick()
            except Exception as exc:   # nunca deixa a thread morrer
                print("downloads:", exc)
            time.sleep(1)

    def _tick(self) -> None:
        with self._lock:
            queued = [j for j in self.jobs.values() if j["status"] == "queued"]
        for job in queued:
            if job["kind"] == "torrent":
                self._start_torrent(job)
            else:
                self._start_http(job)
        changed = bool(queued)
        if self._session is not None:
            changed |= self._poll_torrents()
        changed |= any(j["status"] in ACTIVE for j in self.jobs.values())
        if changed:
            self._notify()
        if time.time() - self._last_save > 15 and any(j["status"] in ACTIVE for j in self.jobs.values()):
            self._save()

    # ------------------------------------------------------------ HTTP

    def _start_http(self, job: dict) -> None:
        stop = threading.Event()
        self._stop[job["id"]] = stop
        self._set(job["id"], status="downloading")
        threading.Thread(target=self._http_worker, args=(job["id"], stop), daemon=True).start()

    def _http_worker(self, job_id: str, stop: threading.Event) -> None:
        job = self.jobs[job_id]
        folder = Path(job["dir"])
        try:
            part = folder / f"{job['file']}.part" if job.get("file") else None
            offset = part.stat().st_size if part and part.exists() else 0
            headers = {"User-Agent": f"GameDock/{__version__}"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            resp = urllib.request.urlopen(urllib.request.Request(job["uri"], headers=headers), timeout=30)
            if "text/html" in (resp.headers.get("Content-Type") or "").lower():
                resp.close()
                raise ValueError("esse link abre uma página de site, não um arquivo. Abra no navegador.")
            if not job.get("file"):
                job["file"] = self._filename(resp, job)
                part = folder / f"{job['file']}.part"
            if offset and resp.status != 206:   # servidor não aceita continuar: recomeça
                offset = 0
            length = int(resp.headers.get("Content-Length") or 0)
            total = offset + length if length else 0
            self._set(job_id, total=total or job["total"], done=offset, file=job["file"])
            done, last_t, last_b = offset, time.time(), offset
            with resp, part.open("ab" if offset else "wb") as out:
                while not stop.is_set():
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    job["done"] = done
                    now = time.time()
                    if now - last_t >= 1:
                        speed = (done - last_b) / (now - last_t)
                        last_t, last_b = now, done
                        self._runtime[job_id] = {"speed": speed, "eta": (total - done) / speed if total and speed else None}
            if stop.is_set():
                return   # pausado ou cancelado: o .part fica para continuar depois
            if total and done < total:
                raise OSError("a conexão caiu antes do fim; clique em continuar")
            final = folder / job["file"]
            if final.exists():
                final.unlink()
            part.replace(final)
            self._runtime.pop(job_id, None)
            self._set(job_id, status="done", done=done, total=done, finished=time.time())
        except (OSError, ValueError) as exc:
            self._runtime.pop(job_id, None)
            if job_id in self.jobs and not stop.is_set():
                self._set(job_id, status="error", error=str(exc)[:300])
        finally:
            self._stop.pop(job_id, None)

    @staticmethod
    def _filename(resp, job: dict) -> str:
        disposition = resp.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE) or \
            re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        name = unquote(m.group(1)) if m else unquote(Path(urlparse(resp.url).path).name)
        return _safe_name(Path(name).name) if name else _safe_name(job["name"])

    # ------------------------------------------------------------ torrent

    def _lt_session(self):
        if self._session is None:
            settings = {
                "user_agent": f"GameDock/{__version__} libtorrent/{lt.__version__}",
                "listen_interfaces": "0.0.0.0:6881,[::]:6881",
                "alert_mask": lt.alert_category.error | lt.alert_category.storage | lt.alert_category.status,
                "enable_dht": True, "enable_lsd": True, "enable_upnp": True, "enable_natpmp": True,
            }
            limit = load_config().get("downloads_limit_kb") or 0
            if limit:
                settings["download_rate_limit"] = int(limit) * 1024
            self._session = lt.session(settings)
        return self._session

    def _start_torrent(self, job: dict) -> None:
        job_id = job["id"]
        try:
            session = self._lt_session()
            resume = self._resume_dir / f"{job_id}.resume"
            if resume.exists():
                params = lt.read_resume_data(resume.read_bytes())
            elif job["uri"].lower().startswith("magnet:"):
                params = lt.parse_magnet_uri(job["uri"])
            else:
                req = urllib.request.Request(job["uri"], headers={"User-Agent": f"GameDock/{__version__}"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read(20 * 1024 * 1024)
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(lt.bdecode(data))
            params.save_path = job["dir"]
            handle = session.add_torrent(params)
            self._handles[job_id] = handle
            self._set(job_id, status="metadata" if not handle.status().has_metadata else "downloading", error=None)
        except Exception as exc:
            self._set(job_id, status="error", error=f"torrent: {exc}"[:300])

    def _poll_torrents(self) -> bool:
        changed = False
        for alert in self._session.pop_alerts():
            if isinstance(alert, lt.save_resume_data_alert):
                job_id = next((i for i, h in self._handles.items() if h == alert.handle), None)
                if job_id:
                    (self._resume_dir / f"{job_id}.resume").write_bytes(lt.write_resume_data_buf(alert.params))
            elif isinstance(alert, lt.metadata_received_alert):
                alert.handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        for job_id, handle in list(self._handles.items()):
            job = self.jobs.get(job_id)
            if not job or job["status"] not in ("downloading", "metadata"):
                continue
            s = handle.status()
            if not s.has_metadata:
                self._runtime[job_id] = {"peers": s.num_peers, "speed": s.download_rate}
                continue
            if job["status"] == "metadata":
                job["status"] = "downloading"
                if s.name and not job.get("file"):
                    job["file"] = s.name
                changed = True
            job["total"], job["done"] = s.total_wanted, s.total_wanted_done
            speed = s.download_rate
            self._runtime[job_id] = {"speed": speed, "peers": s.num_peers, "seeds": s.num_seeds,
                                     "eta": (s.total_wanted - s.total_wanted_done) / speed if speed else None,
                                     "checking": s.state in (lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data)}
            if s.is_finished:
                if not load_config().get("downloads_seed"):
                    handle.unset_flags(lt.torrent_flags.auto_managed)
                    handle.pause()
                handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
                self._runtime.pop(job_id, None)
                job.update(status="done", finished=time.time(), done=s.total_wanted)
                self._save()
                changed = True
        return changed

    # ------------------------------------------------------------ fechar

    def shutdown(self) -> None:
        """Ao sair do app: guarda o progresso dos torrents (para continuar de onde parou)."""
        self._closing = True
        for stop in list(self._stop.values()):
            stop.set()
        if self._session is not None and self._handles:
            for handle in self._handles.values():
                try:
                    handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
                except Exception:
                    pass
            pending, deadline = len(self._handles), time.time() + 4
            while pending and time.time() < deadline:
                for alert in self._session.pop_alerts():
                    if isinstance(alert, lt.save_resume_data_alert):
                        job_id = next((i for i, h in self._handles.items() if h == alert.handle), None)
                        if job_id:
                            (self._resume_dir / f"{job_id}.resume").write_bytes(lt.write_resume_data_buf(alert.params))
                        pending -= 1
                    elif isinstance(alert, lt.save_resume_data_failed_alert):
                        pending -= 1
                time.sleep(0.1)
        self._save()


def glob_escape(text: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", text)


_manager: DownloadManager | None = None


def manager() -> DownloadManager:
    global _manager
    if _manager is None:
        _manager = DownloadManager()
    return _manager
