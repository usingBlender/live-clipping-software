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


def transcribe(cfg, path: str) -> dict:
    if not available():
        return {"segments": [], "words": [], "warning": "faster-whisper not installed"}
    with _lock:
        model = _load(cfg)
        segments, _info = model.transcribe(
            path, language=cfg["clipper"].get("language", "pt"), word_timestamps=True,
            vad_filter=True, beam_size=5, condition_on_previous_text=False)
        segs, words = [], []
        for s in segments:
            segs.append({"s": round(s.start, 2), "e": round(s.end, 2), "text": s.text.strip()})
            for w in s.words or []:
                words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)})
    return {"segments": segs, "words": words}
