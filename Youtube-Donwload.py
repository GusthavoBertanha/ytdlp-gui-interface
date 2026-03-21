import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unicodedata
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_TITLE = "YT-DLP Gusthavo"
MAX_FILENAME_LEN = 80
GITHUB_API_VERSION = "2022-11-28"

YTDLP_LATEST_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

# Evermeet (macOS)
EVERMEET_FFMPEG_INFO_URL = "https://evermeet.cx/ffmpeg/info/ffmpeg/release"
EVERMEET_FFMPEG_DOWNLOAD_URL = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"


class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1000x720")
        self.root.minsize(860, 560)

        self.log_queue = queue.Queue()
        self.proc = None
        self.worker_thread = None

        self.download_dir = tk.StringVar(value=str(self.get_default_download_dir()))
        self.url_var = tk.StringVar()

        self.ytdlp_version_var = tk.StringVar(value="yt-dlp: desconhecido")
        self.ffmpeg_version_var = tk.StringVar(value="ffmpeg: desconhecido")

        self.mode_var = tk.StringVar(value="video_best")
        self.bin_dir()

        self.build_ui()
        self.root.after(100, self.flush_logs)
        self.root.after(300, self.refresh_versions_async)

    # =========================
    # PATHS / PLATFORM
    # =========================
    def app_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    def get_default_download_dir(self) -> Path:
        p = self.app_dir() / "arquivos"
        p.mkdir(exist_ok=True)
        return p

    def is_windows(self) -> bool:
        return sys.platform.startswith("win")

    def is_macos(self) -> bool:
        return sys.platform == "darwin"

    def yt_dlp_path(self) -> Path:
        return self.bin_dir() / ("yt-dlp.exe" if self.is_windows() else "yt-dlp")

    def ffmpeg_path(self) -> Path:
        return self.bin_dir() / ("ffmpeg.exe" if self.is_windows() else "ffmpeg")

    def ffprobe_path(self) -> Path:
        return self.bin_dir() / ("ffprobe.exe" if self.is_windows() else "ffprobe")

    def make_executable_if_needed(self, path: Path) -> None:
        if not self.is_windows() and path.exists():
            os.chmod(path, 0o755)

    # =========================
    # UI
    # =========================
    def build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="URL:").pack(anchor="w")
        self.url_entry = ttk.Entry(frame, textvariable=self.url_var)
        self.url_entry.pack(fill="x", pady=(4, 10))
        self.url_entry.focus()

        path_row = ttk.Frame(frame)
        path_row.pack(fill="x", pady=(0, 10))

        ttk.Label(path_row, text="Salvar em:").pack(side="left")
        ttk.Entry(path_row, textvariable=self.download_dir).pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(path_row, text="Escolher pasta", command=self.choose_folder).pack(side="left")

        options_row = ttk.Frame(frame)
        options_row.pack(fill="x", pady=(0, 10))

        ttk.Label(options_row, text="Modo:").pack(side="left")
        self.mode_combo = ttk.Combobox(
            options_row,
            state="readonly",
            width=38,
            textvariable=self.mode_var,
            values=[
                "video_best",
                "audio_mp3",
                "video_1080p",
                "video_720p",
            ],
        )
        self.mode_combo.pack(side="left", padx=(8, 0))
        self.mode_combo.set("video_best")

        ttk.Label(
            options_row,
            text="  video_best = melhor MP4 | audio_mp3 = MP3 | video_1080p | video_720p"
        ).pack(side="left", padx=(12, 0))

        version_box = ttk.LabelFrame(frame, text="Versões")
        version_box.pack(fill="x", pady=(0, 10))

        ttk.Label(version_box, textvariable=self.ytdlp_version_var).pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Label(version_box, textvariable=self.ffmpeg_version_var).pack(anchor="w", padx=8, pady=(0, 8))

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(0, 10))

        self.download_btn = ttk.Button(btn_row, text="Baixar", command=self.start_download)
        self.download_btn.pack(side="left")

        self.stop_btn = ttk.Button(btn_row, text="Parar", command=self.stop_download, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        ttk.Button(btn_row, text="Checar versões", command=self.refresh_versions_async).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Atualizar yt-dlp", command=self.update_ytdlp_async).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Atualizar ffmpeg", command=self.update_ffmpeg_async).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Atualizar tudo", command=self.update_all_async).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Limpar logs", command=self.clear_logs).pack(side="left", padx=(8, 0))

        status_frame = ttk.LabelFrame(frame, text="Logs")
        status_frame.pack(fill="both", expand=True)

        self.log_text = ScrolledText(status_frame, wrap="word", height=20)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text.configure(state="disabled")

    # =========================
    # LOGS
    # =========================
    def log(self, text: str) -> None:
        self.log_queue.put(text)

    def flush_logs(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.flush_logs)

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # =========================
    # NETWORK / FILE HELPERS
    # =========================
    def fetch_text(self, url: str, extra_headers: dict | None = None) -> str:
        headers = {"User-Agent": "yt-dlp-gui-updater"}
        if extra_headers:
            headers.update(extra_headers)
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def fetch_json(self, url: str, extra_headers: dict | None = None) -> dict:
        headers = {
            "User-Agent": "yt-dlp-gui-updater",
            "Accept": "application/vnd.github+json, application/json",
        }
        if "api.github.com" in url:
            headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
        if extra_headers:
            headers.update(extra_headers)

        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def download_file(self, url: str, dest: Path) -> None:
        req = Request(url, headers={"User-Agent": "yt-dlp-gui-updater"})
        with urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)

    def sha256_of_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def atomic_replace(self, new_file: Path, final_file: Path) -> None:
        backup = final_file.with_suffix(final_file.suffix + ".bak")
        if final_file.exists():
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
            final_file.replace(backup)
        new_file.replace(final_file)
        self.make_executable_if_needed(final_file)

    # =========================
    # VERSION CHECKS
    # =========================
    def get_local_ytdlp_version(self) -> str:
        yt = self.yt_dlp_path()
        if not yt.exists():
            return "não encontrado"
        try:
            self.make_executable_if_needed(yt)
            out = subprocess.run(
                [str(yt), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if self.is_windows() else 0,
            )
            text = (out.stdout or out.stderr).strip().splitlines()
            return text[0].strip() if text else "desconhecido"
        except Exception as e:
            return f"erro: {e}"

    def get_local_ffmpeg_version(self) -> str:
        ff = self.ffmpeg_path()
        if not ff.exists():
            return "não encontrado"
        try:
            self.make_executable_if_needed(ff)
            out = subprocess.run(
                [str(ff), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if self.is_windows() else 0,
            )
            lines = (out.stdout or out.stderr).strip().splitlines()
            if not lines:
                return "desconhecido"
            line = lines[0]
            m = re.search(r"ffmpeg version\s+([^\s]+)", line, re.IGNORECASE)
            return m.group(1) if m else line[:80]
        except Exception as e:
            return f"erro: {e}"

    def refresh_versions_async(self) -> None:
        threading.Thread(target=self.refresh_versions, daemon=True).start()

    def refresh_versions(self) -> None:
        yv = self.get_local_ytdlp_version()
        fv = self.get_local_ffmpeg_version()
        self.root.after(0, lambda: self.ytdlp_version_var.set(f"yt-dlp: {yv}"))
        self.root.after(0, lambda: self.ffmpeg_version_var.set(f"ffmpeg: {fv}"))
        self.log(f"[versão] yt-dlp local: {yv}\n")
        self.log(f"[versão] ffmpeg local: {fv}\n")

    # =========================
    # UPDATE ALL
    # =========================
    def update_all_async(self) -> None:
        threading.Thread(target=self.update_all, daemon=True).start()

    def update_all(self) -> None:
        self.log("[atualização] Iniciando atualização completa...\n")
        self.update_ytdlp()
        self.update_ffmpeg()
        self.refresh_versions()
        self.log("[atualização] Finalizado.\n")

    # =========================
    # YT-DLP UPDATE
    # =========================
    def get_latest_ytdlp_info(self) -> dict:
        data = self.fetch_json(YTDLP_LATEST_RELEASE_API)
        assets = data.get("assets", [])
        tag = data.get("tag_name", "").lstrip("v")

        if self.is_windows():
            wanted_names = ["yt-dlp.exe"]
        elif self.is_macos():
            wanted_names = ["yt-dlp_macos", "yt-dlp"]
        else:
            raise RuntimeError("Plataforma não suportada para este app.")

        asset = None
        for wanted in wanted_names:
            for a in assets:
                if a.get("name") == wanted:
                    asset = a
                    break
            if asset:
                break

        if not asset:
            raise RuntimeError("Asset do yt-dlp não encontrado para esta plataforma.")

        return {
            "tag": tag,
            "asset_name": asset["name"],
            "download_url": asset["browser_download_url"],
        }

    def update_ytdlp_async(self) -> None:
        threading.Thread(target=self.update_ytdlp, daemon=True).start()

    def update_ytdlp(self) -> None:
        try:
            self.log("[yt-dlp] Checando última versão...\n")
            latest = self.get_latest_ytdlp_info()
            local_ver = self.get_local_ytdlp_version()
            remote_ver = latest["tag"]

            self.log(f"[yt-dlp] Versão local: {local_ver}\n")
            self.log(f"[yt-dlp] Versão remota: {remote_ver}\n")

            if local_ver == remote_ver:
                self.log("[yt-dlp] Já está atualizado.\n")
                return

            yt_path = self.yt_dlp_path()
            tmp_path = yt_path.with_suffix(yt_path.suffix + ".new")

            self.log(f"[yt-dlp] Baixando {latest['asset_name']}...\n")
            self.download_file(latest["download_url"], tmp_path)
            self.make_executable_if_needed(tmp_path)

            self.atomic_replace(tmp_path, yt_path)
            self.log("[yt-dlp] Atualização concluída.\n")
            self.refresh_versions()

        except Exception as e:
            self.log(f"[yt-dlp] Erro ao atualizar: {e}\n")

    # =========================
    # FFMPEG UPDATE
    # =========================
    def update_ffmpeg_async(self) -> None:
        threading.Thread(target=self.update_ffmpeg, daemon=True).start()

    def update_ffmpeg(self) -> None:
        try:
            if self.is_windows():
                self.update_ffmpeg_windows()
            elif self.is_macos():
                self.update_ffmpeg_macos()
            else:
                self.log("[ffmpeg] Plataforma não suportada.\n")
        except Exception as e:
            self.log(f"[ffmpeg] Erro ao atualizar: {e}\n")

    # ---------- Windows / Gyan ----------
    def update_ffmpeg_windows(self) -> None:
        self.log("[ffmpeg] Checando última versão para Windows...\n")

        # usando RELEASE ZIP em vez de GIT 7Z
        archive_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        sha_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256"
        ver_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.ver"

        local_ver = self.get_local_ffmpeg_version()
        self.log(f"[ffmpeg] Versão local: {local_ver}\n")

        try:
            remote_ver = self.fetch_text(ver_url).strip()
        except Exception:
            remote_ver = "desconhecida"

        self.log(f"[ffmpeg] Versão remota: {remote_ver}\n")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            archive_path = tmpdir / "ffmpeg-release-essentials.zip"
            sha_path = tmpdir / "ffmpeg-release-essentials.zip.sha256"

            self.log("[ffmpeg] Baixando pacote ZIP...\n")
            self.download_file(archive_url, archive_path)

            self.log("[ffmpeg] Baixando SHA256...\n")
            self.download_file(sha_url, sha_path)

            expected_sha = self.parse_sha256_file(sha_path)
            real_sha = self.sha256_of_file(archive_path)

            self.log(f"[ffmpeg] SHA esperado:  {expected_sha}\n")
            self.log(f"[ffmpeg] SHA calculado: {real_sha}\n")

            if expected_sha.lower() != real_sha.lower():
                raise RuntimeError("SHA256 do ffmpeg não confere. Atualização cancelada.")

            self.log("[ffmpeg] Extraindo ZIP...\n")
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(tmpdir)

            found_ffmpeg = None
            found_ffprobe = None

            for p in tmpdir.rglob("*"):
                if p.is_file() and p.name.lower() == "ffmpeg.exe":
                    found_ffmpeg = p
                elif p.is_file() and p.name.lower() == "ffprobe.exe":
                    found_ffprobe = p

            if not found_ffmpeg:
                raise RuntimeError("ffmpeg.exe não encontrado no ZIP extraído.")

            ffmpeg_new = self.ffmpeg_path().with_suffix(".exe.new")
            shutil.copy2(found_ffmpeg, ffmpeg_new)
            self.atomic_replace(ffmpeg_new, self.ffmpeg_path())

            if found_ffprobe:
                ffprobe_new = self.ffprobe_path().with_suffix(".exe.new")
                shutil.copy2(found_ffprobe, ffprobe_new)
                self.atomic_replace(ffprobe_new, self.ffprobe_path())

            self.log("[ffmpeg] Atualização do Windows concluída.\n")
            self.refresh_versions()

    def parse_sha256_file(self, sha_file: Path) -> str:
        text = sha_file.read_text(encoding="utf-8", errors="replace").strip()
        m = re.search(r"\b([a-fA-F0-9]{64})\b", text)
        if not m:
            raise RuntimeError("Não foi possível ler o SHA256 do arquivo.")
        return m.group(1)

    # ---------- macOS / Evermeet ----------
    def get_latest_evermeet_info(self) -> dict:
        try:
            data = self.fetch_json(EVERMEET_FFMPEG_INFO_URL)
        except Exception:
            data = {}

        version = (
            data.get("version")
            or data.get("name")
            or data.get("release")
            or "desconhecido"
        )

        return {
            "version": str(version),
            "download_url": EVERMEET_FFMPEG_DOWNLOAD_URL,
        }

    def update_ffmpeg_macos(self) -> None:
        self.log("[ffmpeg] Checando última versão para macOS...\n")
        info = self.get_latest_evermeet_info()
        local_ver = self.get_local_ffmpeg_version()

        self.log(f"[ffmpeg] Versão local: {local_ver}\n")
        self.log(f"[ffmpeg] Versão remota: {info['version']}\n")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = tmpdir / "ffmpeg_macos.zip"

            self.log("[ffmpeg] Baixando pacote do Evermeet...\n")
            self.download_file(info["download_url"], zip_path)

            self.log("[ffmpeg] Extraindo ZIP...\n")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

            found_ffmpeg = None
            for p in tmpdir.rglob("*"):
                if p.is_file() and p.name == "ffmpeg":
                    found_ffmpeg = p
                    break

            if not found_ffmpeg:
                raise RuntimeError("Binário ffmpeg não encontrado no ZIP do Evermeet.")

            ffmpeg_new = self.ffmpeg_path().with_suffix(".new")
            shutil.copy2(found_ffmpeg, ffmpeg_new)
            self.make_executable_if_needed(ffmpeg_new)
            self.atomic_replace(ffmpeg_new, self.ffmpeg_path())

            self.log("[ffmpeg] Atualização do macOS concluída.\n")
            self.refresh_versions()

    # =========================
    # ACTIONS
    # =========================
    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.download_dir.get())
        if folder:
            self.download_dir.set(folder)

    def start_download(self) -> None:
        url = self.url_var.get().strip()
        out_dir = Path(self.download_dir.get().strip())

        if not url:
            messagebox.showwarning(APP_TITLE, "Cole uma URL primeiro.")
            return

        if not out_dir.exists():
            messagebox.showwarning(APP_TITLE, "A pasta de destino não existe.")
            return

        yt = self.yt_dlp_path()
        ff = self.ffmpeg_path()

        if not yt.exists():
            messagebox.showerror(APP_TITLE, f"Não encontrei o yt-dlp em:\n{yt}")
            return

        if not ff.exists():
            messagebox.showerror(APP_TITLE, f"Não encontrei o ffmpeg em:\n{ff}")
            return

        self.make_executable_if_needed(yt)
        self.make_executable_if_needed(ff)

        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        self.log("\n==============================\n")
        self.log(f"URL: {url}\n")
        self.log(f"Pasta: {out_dir}\n")
        self.log(f"Modo: {self.mode_var.get()}\n")
        self.log("==============================\n\n")

        self.worker_thread = threading.Thread(
            target=self.run_download,
            args=(url, out_dir, self.mode_var.get()),
            daemon=True
        )
        self.worker_thread.start()

    def stop_download(self) -> None:
        if not self.proc:
            return

        self.log("\n[app] Parando download...\n")
        try:
            if self.is_windows():
                self.proc.terminate()
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception as e:
            self.log(f"[app] Erro ao parar: {e}\n")

    # =========================
    # DOWNLOAD COMMAND
    # =========================
    def build_download_command(self, url: str, out_dir: Path, mode: str) -> list[str]:
        yt = self.yt_dlp_path()
        ff = self.ffmpeg_path()

        outtmpl = str(out_dir / "%(title).120B [%(id)s].%(ext)s")

        cmd = [
            str(yt),
            url,
            "--ffmpeg-location", str(ff),
            "--newline",
            "--restrict-filenames",
            "--no-playlist",
            "-o", outtmpl,
        ]

        if mode == "audio_mp3":
            cmd += [
                "-f", "bestaudio[ext=m4a]/bestaudio/best",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
            ]
        elif mode == "video_1080p":
            cmd += [
                "-f",
                "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
                "b[height<=1080][ext=mp4]/"
                "bv*[height<=1080]+ba/b[height<=1080]/best",
                "--merge-output-format", "mp4",
            ]
        elif mode == "video_720p":
            cmd += [
                "-f",
                "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
                "b[height<=720][ext=mp4]/"
                "bv*[height<=720]+ba/b[height<=720]/best",
                "--merge-output-format", "mp4",
            ]
        else:
            cmd += [
                "-f",
                "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/best",
                "--merge-output-format", "mp4",
            ]

        return cmd

    def run_download(self, url: str, out_dir: Path, mode: str) -> None:
        cmd = self.build_download_command(url, out_dir, mode)
        self.log("[app] Iniciando yt-dlp...\n")
        self.log("[app] Comando:\n" + " ".join(self.safe_quote(c) for c in cmd) + "\n\n")

        before_files = self.snapshot_files(out_dir)

        try:
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }

            if self.is_windows():
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs["preexec_fn"] = os.setsid

            self.proc = subprocess.Popen(cmd, **popen_kwargs)

            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.log(line)

            return_code = self.proc.wait()

            after_files = self.snapshot_files(out_dir)
            new_files = sorted(after_files - before_files)

            if return_code == 0:
                self.log("\n[app] Download concluído.\n")
                final_file = self.pick_final_media_file(new_files, out_dir)
                if final_file:
                    renamed = self.rename_output_file(final_file)
                    self.log(f"[app] Arquivo final: {renamed.name}\n")
                    self.log(f"[app] Caminho: {renamed}\n")
                else:
                    self.log("[app] Não consegui identificar o arquivo final para renomear.\n")
            else:
                self.log(f"\n[app] yt-dlp finalizou com código {return_code}.\n")

        except Exception as e:
            self.log(f"[app] Erro: {e}\n")
        finally:
            self.proc = None
            self.root.after(0, self.on_download_finished)

    def on_download_finished(self) -> None:
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    # =========================
    # FILE HELPERS
    # =========================
    def snapshot_files(self, folder: Path) -> set[Path]:
        result = set()
        for p in folder.iterdir():
            if p.is_file():
                result.add(p.resolve())
        return result

    def pick_final_media_file(self, new_files: list[Path], out_dir: Path) -> Path | None:
        allowed_exts = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".mov"}
        candidates = [p for p in new_files if p.suffix.lower() in allowed_exts]

        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]

        all_candidates = [
            p for p in out_dir.iterdir()
            if p.is_file() and p.suffix.lower() in allowed_exts
        ]
        if all_candidates:
            all_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return all_candidates[0]

        return None

    def rename_output_file(self, file_path: Path) -> Path:
        ext = file_path.suffix.lower()
        stem = self.clean_filename(file_path.stem, max_len=MAX_FILENAME_LEN)

        if not stem:
            stem = "video"

        new_path = file_path.with_name(f"{stem}{ext}")
        new_path = self.make_unique_path(new_path)

        if new_path != file_path:
            file_path.rename(new_path)

        return new_path

    def clean_filename(self, text: str, max_len: int = 80) -> str:
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\[[A-Za-z0-9_-]+\]$", "", text).strip()
        text = re.sub(r"[^A-Za-z0-9]+", "", text)
        return text[:max_len]

    def make_unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        counter = 2

        while True:
            candidate = path.with_name(f"{stem}{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def safe_quote(self, s: str) -> str:
        if " " in s or '"' in s:
            return '"' + s.replace('"', '\\"') + '"'
        return s

    def bin_dir(self) -> Path:
        p = self.app_dir() / "arquivos"
        p.mkdir(exist_ok=True)
        return p

def main() -> None:
    root = tk.Tk()

    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()