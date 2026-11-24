# -*- coding: utf-8 -*-
"""Сборка диаризованного транскрипта встречи из ASR+diarization сегментов.

Вход — сегменты (whisperx/GigaAM+diarization): {speaker_key, text, ts_start_ms, ts_end_ms,
confidence}. Выход — упорядоченные реплики со склейкой соседних одного спикера, пометкой
OVERLAP (пересечение по времени с другим спикером) и резолвом имени (known|unknown).
Неизвестный голос и пересечение реплик сохраняются как отдельные состояния.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Segment:
    speaker_key: str
    text: str
    ts_start_ms: int
    ts_end_ms: int
    confidence: Optional[float] = None


@dataclass
class Utterance:
    speaker_key: str
    speaker_name: str
    kind: str                 # known | unknown | overlap
    text: str
    ts_start_ms: int
    ts_end_ms: int


def _overlaps(a: Segment, b: Segment) -> bool:
    return a.ts_start_ms < b.ts_end_ms and b.ts_start_ms < a.ts_end_ms


def assemble(segments: list[Segment],
             resolve_name: Callable[[str], Optional[str]] = lambda k: None,
             *, merge_gap_ms: int = 1500) -> list[Utterance]:
    """Собрать транскрипт. resolve_name(speaker_key)->имя или None (тогда unknown)."""
    if not segments:
        return []
    segs = sorted(segments, key=lambda s: (s.ts_start_ms, s.ts_end_ms))

    overlap = [False] * len(segs)
    for i, a in enumerate(segs):
        for j in range(i + 1, len(segs)):
            b = segs[j]
            if b.ts_start_ms >= a.ts_end_ms:
                break
            if b.speaker_key != a.speaker_key and _overlaps(a, b):
                overlap[i] = overlap[j] = True

    unknown_no: dict[str, int] = {}

    def name_for(key: str) -> tuple[str, str]:
        nm = resolve_name(key)
        if nm:
            return nm, "known"
        if key not in unknown_no:
            unknown_no[key] = len(unknown_no) + 1
        return f"Участник {unknown_no[key]}", "unknown"

    out: list[Utterance] = []
    cur: Optional[Utterance] = None
    cur_ov = False
    for i, s in enumerate(segs):
        nm, base_kind = name_for(s.speaker_key)
        ov = overlap[i]
        kind = "overlap" if ov else base_kind
        text = (s.text or "").strip()
        if not text:
            continue
        can_merge = (cur is not None and cur.speaker_key == s.speaker_key
                     and not ov and not cur_ov
                     and s.ts_start_ms - cur.ts_end_ms <= merge_gap_ms)
        if can_merge:
            cur.text += " " + text
            cur.ts_end_ms = s.ts_end_ms
        else:
            cur = Utterance(speaker_key=s.speaker_key, speaker_name=nm, kind=kind,
                            text=text, ts_start_ms=s.ts_start_ms, ts_end_ms=s.ts_end_ms)
            cur_ov = ov
            out.append(cur)
    return out


def render_markdown(utts: list[Utterance]) -> str:
    def mmss(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"
    lines = []
    for u in utts:
        tag = " ⚠︎" if u.kind == "overlap" else ""
        lines.append(f"[{mmss(u.ts_start_ms)}] **{u.speaker_name}**{tag}: {u.text}")
    return "\n".join(lines)
