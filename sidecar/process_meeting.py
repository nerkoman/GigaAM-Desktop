import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")

import numpy as np

from .echo import suppress_microphone_echo
from .meeting_transcript import Segment, assemble, render_markdown
from .protocol import clean_protocol, generate_protocol

SAMPLE_RATE = 16000


def ffmpeg_binary() -> str:
    if configured := os.environ.get("FFMPEG_BINARY"):
        return configured
    if found := shutil.which("ffmpeg"):
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("ffmpeg не найден; установите его или задайте FFMPEG_BINARY")


def decode_audio(path: Path) -> np.ndarray:
    result = subprocess.run(
        [
            ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def read_tracks(session: Path) -> list[dict]:
    tracks_path = session / "tracks.json"
    if tracks_path.exists():
        tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
        return [
            {
                "kind": "microphone" if Path(item["file"]).stem.split("-", 1)[0] == "microphone" else "system",
                "path": session / item["file"],
                "offset_seconds": float(item.get("offset_seconds", 0.0)),
            }
            for item in tracks
        ]
    tracks = []
    for path in sorted(session.iterdir()):
        kind = path.stem.split("-", 1)[0]
        if path.stem == "mixed" or not path.is_file():
            continue
        if path.suffix.lower() not in {".ogg", ".flac", ".wav", ".mp3"}:
            continue
        if kind not in {"microphone", "system"}:
            if path.suffix.lower() != ".ogg":
                continue
            kind = "system"
        tracks.append({"kind": kind, "path": path, "offset_seconds": 0.0})
    if not tracks:
        raise ValueError(f"В каталоге {session} нет дорожек записи")
    return tracks


def diarize(samples: np.ndarray, pipeline, speaker_count: int | None = None):
    import torch

    audio = {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": SAMPLE_RATE}
    options = {} if speaker_count is None else {"num_speakers": speaker_count}
    result = pipeline(audio, **options)
    turns = []
    for turn, _, speaker in result.exclusive_speaker_diarization.itertracks(yield_label=True):
        start = max(0, int(turn.start * SAMPLE_RATE))
        end = min(len(samples), int(turn.end * SAMPLE_RATE))
        if end - start >= SAMPLE_RATE // 4:
            turns.append((start, end, speaker))
    return turns


def transcribe_turns(samples, turns, kind, offset_seconds, recognizer) -> list[Segment]:
    segments = []
    for start, end, detected_speaker in turns:
        text = recognizer(samples[start:end], sample_rate=SAMPLE_RATE).strip()
        if not text:
            continue
        speaker_key = "microphone" if kind == "microphone" else detected_speaker
        segments.append(Segment(
            speaker_key=speaker_key,
            text=text,
            ts_start_ms=round((offset_seconds + start / SAMPLE_RATE) * 1000),
            ts_end_ms=round((offset_seconds + end / SAMPLE_RATE) * 1000),
        ))
    return segments


def process_session(args) -> tuple[list, str]:
    import onnx_asr
    import torch
    from pyannote.audio import Pipeline

    tracks = read_tracks(args.session)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    diarizer = Pipeline.from_pretrained(str(args.diarization_model))
    diarizer.to(device)
    recognizer_model = onnx_asr.load_model(
        "gigaam-v3-e2e-ctc", path=args.asr_model, quantization="int8"
    )
    recognizer = recognizer_model.recognize

    segments = []
    for track in tracks:
        samples = decode_audio(track["path"])
        speaker_count = 1 if track["kind"] == "microphone" else args.speakers
        turns = diarize(samples, diarizer, speaker_count)
        segments.extend(transcribe_turns(
            samples, turns, track["kind"], track["offset_seconds"], recognizer
        ))

    segments = suppress_microphone_echo(segments)
    names = {"microphone": args.user_name}
    utterances = assemble(segments, resolve_name=names.get)
    transcript = render_markdown(utterances)
    return utterances, transcript


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальная обработка двухдорожечной записи встречи")
    parser.add_argument("session", type=Path)
    parser.add_argument("--asr-model", type=Path, required=True)
    parser.add_argument("--diarization-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--user-name", default="Я")
    parser.add_argument("--speakers", type=int)
    parser.add_argument("--ollama-model", default="qwen3.5:4b")
    parser.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--skip-protocol", action="store_true")
    parser.add_argument("--json-result", action="store_true")
    args = parser.parse_args()

    utterances, transcript = process_session(args)
    if not utterances:
        raise ValueError("На дорожках не найдено распознанной речи")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "transcript.md").write_text(transcript + "\n", encoding="utf-8")
    (args.output / "segments.json").write_text(json.dumps(
        [asdict(item) for item in utterances], ensure_ascii=False, indent=2
    ) + "\n", encoding="utf-8")
    warning = None
    if not args.skip_protocol:
        try:
            protocol = generate_protocol(transcript, args.ollama_model, args.ollama_endpoint)
            if not protocol:
                raise ValueError("Ollama вернула пустой протокол")
            (args.output / "protocol.md").write_text(protocol + "\n", encoding="utf-8")
        except (OSError, http.client.HTTPException, KeyError, ValueError) as error:
            warning = f"Диаризация готова. Не удалось подготовить протокол: {error}"
    if args.json_result:
        print(json.dumps({"protocol_warning": warning}, ensure_ascii=False))
        return
    print(f"Реплик: {len(utterances)}")
    print(f"Транскрипт: {args.output / 'transcript.md'}")
    if warning:
        print(warning, file=sys.stderr)
    elif not args.skip_protocol:
        print(f"Протокол: {args.output / 'protocol.md'}")


if __name__ == "__main__":
    main()
