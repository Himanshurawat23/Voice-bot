import io
import re
import os
import time
import asyncio
import logging
import functools
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

logger = logging.getLogger("f5-tts-adapter")

# Global singleton cache for the loaded MLX F5-TTS model
_CACHED_MODEL = None
_MODEL_LOCK = None

# Cache loaded reference audio to avoid re-reading from disk on every utterance
_CACHED_REF_AUDIO = None
_CACHED_REF_PATH = None

PROFILES_DIR = Path(__file__).parent / "profiles"

# F5-TTS performance tuning constants
# Reference audio longer than this is trimmed — directly controls generation frame count
# and is the single biggest latency lever. 3-5s is the sweet spot for voice cloning quality.
MAX_REF_AUDIO_SECONDS = 4.0
# Diffusion sampling steps — 6 is the quality/speed sweet spot (4=fast but mumbled, 8=clear but slow)
DEFAULT_STEPS = 6
# Classifier-free guidance strength — controls how closely output follows text conditioning.
# 2.0 is required for clear, intelligible speech. Lower values cause mumbling.
DEFAULT_CFG_STRENGTH = 2.0
# Speech speed multiplier
DEFAULT_SPEED = 1.0


def is_f5_tts_available() -> bool:
    """Check if MLX and f5_tts_mlx are installed and running on Apple Silicon."""
    try:
        import mlx.core as mx  # type: ignore
        import f5_tts_mlx
        return True
    except Exception as e:
        logger.debug(f"F5-TTS MLX not available: {e}")
        return False


def get_f5_model(model_name: str = "lucasnewman/f5-tts-mlx"):
    """Loads and caches the F5-TTS model in unified memory."""
    global _CACHED_MODEL
    if _CACHED_MODEL is not None:
        return _CACHED_MODEL

    from f5_tts_mlx.cfm import F5TTS as F5Model
    logger.info(f"Loading F5-TTS MLX model '{model_name}' into Apple Silicon unified memory...")
    start = time.perf_counter()
    _CACHED_MODEL = F5Model.from_pretrained(model_name)
    load_time = (time.perf_counter() - start) * 1000
    logger.info(f"✅ F5-TTS model loaded in {load_time:.0f}ms")
    return _CACHED_MODEL


def load_ref_audio(ref_path: str, target_sr: int = 24000):
    """
    Loads reference audio and ensures it's 24kHz mono float32 array normalized.
    The audio file saved during calibration is already trimmed to the optimal
    duration (4-6s) and strictly matched to ref_text.
    Never trim audio here without updating ref_text, as any audio-text mismatch
    causes F5-TTS to produce mumbled, unintelligible output.
    """
    global _CACHED_REF_AUDIO, _CACHED_REF_PATH

    # Return cached audio if same file (avoids disk I/O on every utterance)
    if _CACHED_REF_AUDIO is not None and _CACHED_REF_PATH == ref_path:
        return _CACHED_REF_AUDIO

    import soundfile as sf
    import mlx.core as mx  # type: ignore

    audio, sr = sf.read(ref_path)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    if sr != target_sr:
        import av
        # Resample to 24kHz
        in_buf = io.BytesIO()
        sf.write(in_buf, audio, sr, format="wav")
        in_buf.seek(0)
        
        container = av.open(in_buf)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=target_sr)
        resampled_chunks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                resampled_chunks.append(resampled.to_ndarray()[0])
        audio = np.concatenate(resampled_chunks) if resampled_chunks else audio

    audio = mx.array(audio, dtype=mx.float32)
    # Target RMS normalization
    rms = mx.sqrt(mx.mean(mx.square(audio)))
    target_rms = 0.1
    if rms < target_rms and rms > 1e-6:
        audio = audio * target_rms / rms

    # Cache for subsequent calls
    _CACHED_REF_AUDIO = audio
    _CACHED_REF_PATH = ref_path

    return audio


def _synthesize_sentence_sync(
    model, audio, ref_text: str, sentence_text: str, steps: int,
    method: str, speed: float, cfg_strength: float, sample_rate: int,
):
    """
    Runs the heavy MLX diffusion sampling synchronously.
    This function is designed to be called via asyncio.to_thread() so that it
    does NOT block the async event loop (which handles VAD, STT, and audio I/O).
    """
    import mlx.core as mx  # type: ignore
    from f5_tts_mlx.generate import (
        estimated_duration,
        FRAMES_PER_SEC,
        convert_char_to_pinyin,
    )

    audio_prefix_len = audio.shape[0]

    dur_frames = int(
        estimated_duration(audio, ref_text, sentence_text, speed)
        * FRAMES_PER_SEC
    )
    formatted_text = convert_char_to_pinyin([ref_text + " " + sentence_text])

    wave, _ = model.sample(
        mx.expand_dims(audio, axis=0),
        text=formatted_text,
        duration=dur_frames,
        steps=steps,
        method=method,
        speed=speed,
        cfg_strength=cfg_strength,
    )

    # Trim out reference audio prefix
    wave = wave[audio_prefix_len:]
    mx.eval(wave)

    # Convert float32 wave in [-1.0, 1.0] to 16-bit PCM bytes
    np_wave = np.array(wave, dtype=np.float32)
    pcm_bytes = (np.clip(np_wave, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return pcm_bytes


class F5TTS(tts.TTS):
    """
    LiveKit Agents TTS adapter for F5-TTS running on Apple Silicon via MLX.
    Performs fast zero-shot voice cloning from a 3-5 second reference audio sample.
    """

    def __init__(
        self,
        *,
        ref_audio_path: Optional[str] = None,
        ref_audio_text: Optional[str] = None,
        model_name: str = "lucasnewman/f5-tts-mlx",
        steps: int = DEFAULT_STEPS,
        method: str = "euler",
        speed: float = DEFAULT_SPEED,
        cfg_strength: float = DEFAULT_CFG_STRENGTH,
        sample_rate: int = 24000,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._ref_audio_path = ref_audio_path
        self._ref_audio_text = ref_audio_text or "Hello, this is my calibrated voice."
        self._model_name = model_name
        self._steps = steps
        self._method = method
        self._speed = speed
        self._cfg_strength = cfg_strength

        # Eagerly pre-load model and ref audio at init time (not first utterance)
        try:
            get_f5_model(model_name)
            if ref_audio_path and os.path.exists(ref_audio_path):
                load_ref_audio(ref_audio_path, target_sr=sample_rate)
                logger.info("⚡ F5-TTS model + reference audio pre-cached at init")
        except Exception as e:
            logger.warning(f"Pre-load during init failed (will retry on first utterance): {e}")

    @property
    def model(self) -> str:
        return "f5-tts-mlx"

    @property
    def provider(self) -> str:
        return "F5TTS"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return F5ChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            ref_audio_path=self._ref_audio_path,
            ref_audio_text=self._ref_audio_text,
            model_name=self._model_name,
            steps=self._steps,
            method=self._method,
            speed=self._speed,
            cfg_strength=self._cfg_strength,
            sample_rate=self.sample_rate,
        )


class F5ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: tts.TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        ref_audio_path: Optional[str],
        ref_audio_text: str,
        model_name: str,
        steps: int,
        method: str,
        speed: float,
        cfg_strength: float,
        sample_rate: int,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._ref_audio_path = ref_audio_path
        self._ref_audio_text = ref_audio_text
        self._model_name = model_name
        self._steps = steps
        self._method = method
        self._speed = speed
        self._cfg_strength = cfg_strength
        self._sample_rate = sample_rate

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )

        clean_text = self._input_text.strip()
        stripped_alnum = re.sub(r"[^\w\s]", "", clean_text)
        if not clean_text or not stripped_alnum.strip():
            output_emitter.flush()
            return

        from f5_tts_mlx.generate import split_sentences

        # Resolve reference audio file
        ref_path = self._ref_audio_path
        if not ref_path or not os.path.exists(ref_path):
            # Check for latest profile reference WAV
            ref_files = list(PROFILES_DIR.glob("*_ref_24k.wav")) or list(PROFILES_DIR.glob("*_ref.wav"))
            if ref_files:
                ref_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                ref_path = str(ref_files[0])
                logger.info(f"Using latest saved profile reference: {ref_path}")

        if not ref_path or not os.path.exists(ref_path):
            logger.warning("No reference audio file found for F5-TTS voice cloning.")
            output_emitter.flush()
            return

        ref_text = (self._ref_audio_text or "").strip()
        if not ref_text or ref_text == "Hello, this is my calibrated voice.":
            # Attempt to read matched ref_text from corresponding profile JSON
            stem = Path(ref_path).stem.replace("_ref_24k", "").replace("_ref", "")
            json_path = PROFILES_DIR / f"{stem}.json"
            if json_path.exists():
                try:
                    import json as _json
                    with open(json_path, "r", encoding="utf-8") as jf:
                        pdata = _json.load(jf)
                        ref_text = (pdata.get("ref_text") or pdata.get("transcript", "")).strip()
                except Exception:
                    pass
        if not ref_text:
            ref_text = "Namaste. I am recording my voice so this assistant learns my accent."

        try:
            synth_start = time.perf_counter()
            model = get_f5_model(self._model_name)
            audio = load_ref_audio(ref_path, target_sr=self._sample_rate)

            # Sentence chunking: generate each sentence and push immediately for lower perceived latency
            sentences = split_sentences(clean_text)
            if not sentences:
                sentences = [clean_text]

            logger.info(f"🎙️ [F5-TTS] Synthesizing {len(sentences)} sentence(s) (steps={self._steps}, cfg={self._cfg_strength}, ref={audio.shape[0]/self._sample_rate:.1f}s)")

            for idx, sentence_text in enumerate(sentences):
                st_text = sentence_text.strip()
                if not st_text:
                    continue

                sentence_start = time.perf_counter()

                # Run the heavy MLX diffusion in a thread to avoid blocking the event loop.
                # This is critical: without this, the synchronous mx.eval() call blocks
                # the async loop for 2-5 seconds, preventing VAD/STT from processing
                # incoming audio — which is why the agent appeared "deaf" while synthesizing.
                pcm_bytes = await asyncio.to_thread(
                    _synthesize_sentence_sync,
                    model, audio, ref_text, st_text,
                    self._steps, self._method, self._speed, self._cfg_strength,
                    self._sample_rate,
                )

                latency_ms = (time.perf_counter() - sentence_start) * 1000
                logger.info(
                    f"⚡ [F5-TTS] Chunk {idx + 1}/{len(sentences)} ({len(pcm_bytes)} bytes) generated in {latency_ms:.0f}ms"
                )

                # Emit PCM chunk directly to WebRTC audio track
                output_emitter.push(pcm_bytes)

            total_ms = (time.perf_counter() - synth_start) * 1000
            logger.info(f"⚡ [F5-TTS] Total synthesis completed in {total_ms:.0f}ms")
            output_emitter.flush()
        except Exception as e:
            logger.error(f"❌ F5-TTS synthesis error: {e}", exc_info=True)
