"""Speech-to-text. faster-whisper runs locally (CPU, int8); audio never leaves the host."""

from __future__ import annotations

import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class TranscriptionError(Exception):
    pass


@dataclass(slots=True)
class TranscriptResult:
    text: str
    language: str | None
    language_probability: float | None
    duration: float | None
    segments: list[dict[str, Any]] = field(default_factory=list)


class Transcriber(Protocol):
    provider: str
    model: str

    def transcribe(self, audio_path: Path) -> TranscriptResult: ...


def load_wav_16k_mono(path: Path):
    """Read the 16 kHz mono PCM WAV produced by ffmpeg.extract_audio into float32 samples.

    Passing samples (not a path) keeps faster-whisper from decoding with PyAV, whose API has
    changed incompatibly between releases.
    """
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise TranscriptionError("Expected 16 kHz mono 16-bit WAV (use extract_audio)")
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


class FasterWhisperTranscriber:
    provider = "faster-whisper"

    def __init__(self, model: str = "small", device: str = "cpu",
                 compute_type: str = "int8") -> None:
        self.model = model
        self._device, self._compute_type = device, compute_type
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise TranscriptionError("faster-whisper is not installed") from exc
                self._model = WhisperModel(self.model, device=self._device,
                                           compute_type=self._compute_type)
            return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        model = self._load()
        try:
            # VAD skips music-only / silent stretches, a common case in Reels.
            audio = load_wav_16k_mono(audio_path)
            if audio.size == 0:
                return TranscriptResult(text="", language=None, language_probability=None,
                                        duration=0.0)
            segments, info = model.transcribe(audio, vad_filter=True,
                                              beam_size=5, condition_on_previous_text=False)
            segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                    for s in segments if s.text.strip()]
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc
        return TranscriptResult(
            text=" ".join(s["text"] for s in segs),
            language=info.language,
            language_probability=round(info.language_probability, 3),
            duration=round(info.duration, 2) if info.duration else None,
            segments=segs,
        )
