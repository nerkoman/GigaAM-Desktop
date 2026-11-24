"""Stage the offline runtime using the dedicated, pinned arm64 CPython 3.12 environment."""
import argparse
import base64
import hashlib
import json
import shutil
import sys
import sysconfig
from importlib.metadata import distributions
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ("__init__.py", "echo.py", "meeting_transcript.py", "process_meeting.py", "protocol.py")
ASR = {
    "v3_e2e_ctc.int8.onnx": "2e3fcb7a7b66030336fd10c2fcfb033bd1dc7e1bf238fe5cfd83b1d0cfc9d28e",
    "v3_e2e_ctc_vocab.txt": "142de7570b3de5b3035ce111a89c228e80e6085273731d944093ddf24fa539cd",
    "silero_vad.onnx": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
}
COMMUNITY = {
    "embedding/pytorch_model.bin": "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    "segmentation/pytorch_model.bin": "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
    "plda/plda.npz": "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255",
    "plda/xvec_transform.npz": "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
}
SPEAKER_SHA = "9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1"


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def checked_copy(source, destination, expected):
    with source.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {source.name}")
    copy(source, destination)


def stage_python(destination):
    if sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix:
        raise SystemExit("Run with the dedicated CPython 3.12 virtual environment")
    base = Path(sys.base_prefix)
    copy(base / "bin/python3.12", destination / "bin/python3.12")
    for library in (base / "lib").glob("*.dylib"):
        copy(library, destination / "lib" / library.name)
    stdlib = destination / "lib/python3.12"
    shutil.copytree(base / "lib/python3.12", stdlib,
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", ".DS_Store"))
    site = Path(sysconfig.get_path("purelib")).resolve()
    target = stdlib / "site-packages"
    inventory = []
    for dist in distributions(path=[str(site)]):
        if dist.read_text("direct_url.json"):
            raise ValueError(f"Expected an indexed wheel: {dist.name}")
        for item in dist.files or ():
            source = Path(dist.locate_file(item)).resolve()
            if not source.is_relative_to(site) or "__pycache__" in item.parts or item.suffix == ".pyc":
                continue
            if item.name in {"uv_cache.json", "uv_build.json", "INSTALLER", "REQUESTED"}:
                continue
            if item.hash:
                with source.open("rb") as f:
                    digest = hashlib.file_digest(f, item.hash.mode).digest()
                if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != item.hash.value:
                    raise ValueError(f"Wheel checksum mismatch: {item}")
            elif item.name != "RECORD":
                continue
            copy(source, target / source.relative_to(site))
        inventory.append({"name": dist.name, "version": dist.version})
    return sorted(inventory, key=lambda d: d["name"].lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for Contents/Resources/meeting-runtime")
    parser.add_argument("--asr", required=True, type=Path)
    parser.add_argument("--community", required=True, type=Path)
    parser.add_argument("--wespeaker", required=True, type=Path)
    parser.add_argument("--ffmpeg-prefix", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for name in SIDECAR:
        copy(ROOT / "sidecar" / name, args.output / "sidecar" / name)
    packages = stage_python(args.output / "python")
    (args.output / "python-packages.json").write_text(json.dumps(packages, indent=2) + "\n")
    for name, sha in ASR.items():
        checked_copy(args.asr / name, args.output / "models/runtime" / name, sha)
    (args.output / "models/runtime/.verified").write_text("verified\n")
    for name, sha in COMMUNITY.items():
        checked_copy(args.community / name, args.output / "models/pyannote/community-1" / name, sha)
    config = args.community / "config.yaml"
    content = config.read_bytes()
    git_object = b"blob %d\0" % len(content) + content
    if hashlib.sha1(git_object).hexdigest() != "4022db43960736338378fdb6b5a85cfdae198910":
        raise ValueError("Unexpected Community-1 config.yaml revision")
    copy(config, args.output / "models/pyannote/community-1/config.yaml")
    checked_copy(args.wespeaker, args.output / "models/wespeaker-resnet34.onnx", SPEAKER_SHA)
    copy(args.ffmpeg_prefix / "bin/ffmpeg", args.output / "bin/ffmpeg")
    for lib in (args.ffmpeg_prefix / "lib").glob("lib*.[0-9]*.dylib"):
        if len(lib.name.split(".")) == 3:
            copy(lib, args.output / "lib" / lib.name)


if __name__ == "__main__":
    main()
