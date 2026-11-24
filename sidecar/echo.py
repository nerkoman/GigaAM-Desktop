from collections import Counter

from .meeting_transcript import Segment

TIMING_TOLERANCE_MS = 750


def normalized_words(text: str) -> list[str]:
    return [word.lower() for word in "".join(
        character if character.isalnum() else " " for character in text
    ).split()]


def echo_text_matches(microphone: str, system: str) -> bool:
    mic_words = normalized_words(microphone)
    system_words = normalized_words(system)
    if not mic_words or not system_words:
        return False
    if mic_words == system_words:
        return True
    if len(mic_words) < 3 or len(system_words) < 3:
        return False
    overlap = sum((Counter(mic_words) & Counter(system_words)).values())
    containment = overlap / min(len(mic_words), len(system_words))
    dice = 2 * overlap / (len(mic_words) + len(system_words))
    return containment >= 0.8 and dice >= 0.72


def suppress_microphone_echo(segments: list[Segment]) -> list[Segment]:
    system = [segment for segment in segments if segment.speaker_key != "microphone"]
    if not system:
        return segments
    filtered = []
    for segment in segments:
        if segment.speaker_key != "microphone":
            filtered.append(segment)
            continue
        candidates = [
            candidate for candidate in system
            if segment.ts_start_ms <= candidate.ts_end_ms + TIMING_TOLERANCE_MS
            and candidate.ts_start_ms <= segment.ts_end_ms + TIMING_TOLERANCE_MS
        ]
        if any(echo_text_matches(segment.text, candidate.text) for candidate in candidates):
            continue
        combined = " ".join(candidate.text for candidate in candidates)
        if not echo_text_matches(segment.text, combined):
            filtered.append(segment)
    return filtered
