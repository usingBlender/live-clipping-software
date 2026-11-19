"""faster-whisper with word timestamps. Model loads once and stays on the GPU (~2 GB VRAM)."""
import os
import site
import sys
import threading
from pathlib import Path

_model = None
_lock = threading.Lock()


def _expose_cuda_libs():
    """Make the pip-installed cuBLAS/cuDNN (nvidia-* wheels) loadable by CTranslate2.
    Windows needs their DLL folders on PATH; Linux uses run.sh's LD_LIBRARY_PATH."""
    if sys.platform != "win32":
        _preload_linux()
        return
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for lib in ("cublas", "cudnn", "cuda_runtime"):
            for sub in ("bin", "lib"):
                d = Path(sp) / "nvidia" / lib / sub
                if d.is_dir():
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _preload_linux():
    """Load pip-installed cuBLAS/cuDNN into the process (RTLD_GLOBAL) so CTranslate2 finds them
    without LD_LIBRARY_PATH."""
    import ctypes
    libs = []
    for sp in site.getsitepackages():
        for sub in ("cublas", "cudnn"):
            libs += sorted((Path(sp) / "nvidia" / sub / "lib").glob("lib*.so*"))
    pending = libs
    for _ in range(3):  # a few passes so dependencies load before dependents
        failed = []
        for lib in pending:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                failed.append(lib)
        if not failed or failed == pending:
            break
        pending = failed


def _load(cfg):
    global _model
    if _model is None:
        _expose_cuda_libs()
        from faster_whisper import WhisperModel
        name = cfg["clipper"].get("whisper_model", "large-v3-turbo")
        try:
            _model = WhisperModel(name, device="cuda",
                                  compute_type=cfg["clipper"].get("whisper_compute_type", "int8_float16"))
        except Exception as e:  # noqa: BLE001
            from common.bus import log
            log(f"[whisper] CUDA failed ({e}); using CPU (slow)", "warn")
            _model = WhisperModel(name, device="cpu", compute_type="int8")
    return _model


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _segment_language(model, audio, s, e, sr=16000):
    """Spoken language of one transcript segment (used to tell the streamer from in-game dialogue,
    e.g. a Brazilian streamer vs GTA's English voice acting). Returns (lang, prob) or (None, 0)."""
    if e - s < 0.8:
        return None, 0.0
    chunk = audio[int(s * sr): int(e * sr)]
    try:
        lang, prob, *_ = model.detect_language(audio=chunk)
        return lang, float(prob)
    except Exception:  # noqa: BLE001  (older faster-whisper without detect_language)
        return None, 0.0


def transcribe(cfg, path: str, language: str | None = None) -> dict:
    """Words and segments with timestamps in file seconds. Each segment gets an index and its
    detected language so the editor can caption only the streamer."""
    if not available():
        return {"segments": [], "words": [], "warning": "faster-whisper not installed"}
    language = language or cfg["clipper"].get("language", "pt")
    with _lock:
        model = _load(cfg)
        # Decode with FFmpeg ourselves and pass raw 16 kHz samples. faster-whisper's own decoder uses PyAV,
        # whose newer versions break it ("open() got an unexpected keyword argument 'metadata_errors'").
        from watcher.media import _decode_audio
        audio = _decode_audio(Path(path)).copy()  # writable array
        segments, _info = model.transcribe(
            audio, language=language, word_timestamps=True,
            vad_filter=True, beam_size=5, condition_on_previous_text=False)
        segs, words = [], []
        for i, s in enumerate(segments):
            lang, prob = _segment_language(model, audio, s.start, s.end)
            segs.append({"i": i, "s": round(s.start, 2), "e": round(s.end, 2), "text": s.text.strip(),
                         "lang": lang, "lang_p": round(prob, 2)})
            for w in s.words or []:
                words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "seg": i})
    return {"segments": segs, "words": words, "language": language}


def caption_words(tr: dict, mode: str, streamer_segments, language: str) -> list:
    """Pick which words get captions.
    mode 'off' -> none; 'all' -> everything; 'streamer' -> only the streamer's own speech, using the AI's
    list of streamer segments when available, otherwise dropping segments confidently detected as another
    language (game dialogue)."""
    if mode == "off":
        return []
    words = tr.get("words", [])
    if mode == "all":
        return words
    if isinstance(streamer_segments, list):
        keep = {int(i) for i in streamer_segments if str(i).lstrip("-").isdigit()}
        return [w for w in words if w.get("seg") in keep]
    other = {sg["i"] for sg in tr.get("segments", [])
             if sg.get("lang") and sg["lang"] != language and sg.get("lang_p", 0) >= 0.6}
    return [w for w in words if w.get("seg") not in other]
