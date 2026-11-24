"""Prepare and test the relocatable x86-64 Windows or Linux runtime for a native Tauri build."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import wave
import zipfile
from array import array
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "sidecar/desktop-release.json"
RUNTIME = ROOT / "src-tauri/offline/meeting-runtime"
MODULES = ("__init__.py", "echo.py", "meeting_transcript.py", "process_meeting.py", "protocol.py")


def digest(file):
    with file.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch(url, expected, cache):
    path = cache / unquote(Path(urlsplit(url).path).name)
    if path.is_file() and digest(path) == expected:
        return path
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        temp = Path(directory) / path.name
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
                        "--output", str(temp), url], check=True)
        if digest(temp) != expected:
            raise ValueError(f"Checksum mismatch: {path.name}")
        temp.replace(path)
    return path


def stage_shared(archive, root, windows):
    with zipfile.ZipFile(archive) as data:
        for name in data.namelist():
            if name.startswith("models/") or name in {"THIRD-PARTY-LICENSES.zip", "FFmpeg-windows-build.txt"}:
                relative = name
            elif windows and name.startswith("windows-audio/"):
                relative = "bin/" + Path(name).name
            else:
                continue
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError(f"Invalid runtime entry: {name}")
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with data.open(name) as src, path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def smoke(root):
    windows = sys.platform == "win32"
    python = root / ("python/python.exe" if windows else "python/bin/python3.12")
    ffmpeg = str(root / "bin/ffmpeg.exe") if windows else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required by the Linux package")
    env = dict(os.environ, FFMPEG_BINARY=ffmpeg, PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYANNOTE_METRICS_ENABLED="0",
               OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if windows:
        env["PATH"] = str(root / "bin") + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="GigaAM test ") as directory:
        work = Path(directory)
        clips = []
        for name in ("besarab-clean.wav", "russian-clean.wav"):
            raw = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(ROOT / "tests/fixtures" / name),
                                          "-t", "9", "-f", "f32le", "-acodec", "pcm_f32le",
                                          "-ar", "16000", "-ac", "1", "-"])
            samples = array("f")
            samples.frombytes(raw)
            clips.append(array("h", (int(max(-1, min(1, x)) * 32767) for x in samples)).tobytes())
        for name, data in (("system", (clips[0] + bytes(32000) + clips[1] + bytes(32000)) * 2),
                           ("microphone", clips[0])):
            with wave.open(str(work / f"{name}.wav"), "wb") as dest:
                dest.setparams((1, 2, 16000, 0, "NONE", ""))
                dest.writeframes(data)
        (work / "tracks.json").write_text(json.dumps([{"file": "system.wav"},
                                           {"file": "microphone.wav", "offset_seconds": 45}]))
        subprocess.run([str(python), "-B", "-m", "sidecar.process_meeting", "--skip-protocol",
                        "--speakers", "2", "--asr-model", str(root / "models/runtime"),
                        "--diarization-model", str(root / "models/pyannote/community-1"),
                        "--output", str(work / "result"), str(work)], check=True, cwd=root, env=env)
        rows = json.loads((work / "result/segments.json").read_text(encoding="utf-8"))
        labels = {item["speaker_name"] for item in rows if item["text"].strip()}
        if not {"Я", "Участник 1", "Участник 2"}.issubset(labels):
            raise ValueError(f"Unexpected diarization labels: {labels}")
        print("Offline diarization verified:", ", ".join(sorted(labels)), flush=True)


def main():
    if sys.platform not in {"linux", "win32"} or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("This package targets native x86-64 Linux or Windows")
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        smoke(Path(sys.argv[2]).resolve())
        return
    config = json.loads(CONFIG.read_text())
    windows = sys.platform == "win32"
    target = config["platforms"]["windows" if windows else "linux"]
    cache = Path(os.environ.get("GIGAAM_PACKAGING_CACHE", tempfile.gettempdir())) / "gigaam-runtime-cache"
    cache.mkdir(parents=True, exist_ok=True)
    marker = RUNTIME / "release-manifest.sha256"
    stamp = hashlib.sha256()
    for source in (CONFIG, Path(__file__), ROOT / "sidecar/requirements.txt", *(ROOT / "sidecar" / m for m in MODULES)):
        stamp.update(source.read_bytes())
    signature = sys.platform + "-" + stamp.hexdigest()
    if marker.is_file() and marker.read_text().strip() == signature:
        return
    with tempfile.TemporaryDirectory(dir=RUNTIME.parent) as directory:
        root = Path(directory) / "meeting-runtime"
        root.mkdir()
        with tarfile.open(fetch(target["python_url"], target["python_sha256"], cache)) as python_archive:
            python_archive.extractall(root, filter="data")
        python = root / ("python/python.exe" if windows else "python/bin/python3.12")
        shared = config["shared"]
        stage_shared(fetch(shared["url"], shared["sha256"], cache), root, windows)
        (root / "sidecar").mkdir()
        for module in MODULES:
            shutil.copy2(ROOT / "sidecar" / module, root / "sidecar" / module)
        wheels = []
        for item in target["cpu_wheels"]:
            url, sep, sha = item.partition("#sha256=")
            if not sep:
                raise ValueError("A pinned wheel hash is required")
            wheels.append(str(fetch(url, sha, cache)))
        subprocess.run([str(python), "-m", "pip", "--disable-pip-version-check", "install", "--no-compile",
                        "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                        *wheels, "torchcodec==0.10.0", "-r", str(ROOT / "sidecar/requirements.txt")], check=True)
        # pip records cache paths and generates machine-specific script launchers.
        for direct in (root / "python").rglob("direct_url.json"):
            direct.unlink()
        for pycache in (root / "python").rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)
        (root / "release-manifest.sha256").write_text(signature + "\n")
        shutil.copy2(RUNTIME / "README.txt", root / "README.txt")
        smoke(root)
        old = Path(directory) / "previous"
        RUNTIME.rename(old)
        try:
            root.rename(RUNTIME)
        except OSError:
            old.rename(RUNTIME)
            raise


if __name__ == "__main__":
    main()
