import io
import re
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
import numpy as np
import av
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

logger = logging.getLogger("luxtts-adapter")

_CACHED_LUX_MODEL = None
_CACHED_PROMPT_KEY = None
_CACHED_PROMPT_DATA = None

PROFILES_DIR = Path(__file__).parent / "profiles"

SAMPLE_RATE = 48000
DEFAULT_NUM_STEPS = 4
DEFAULT_SPEED = 1.0


def is_luxtts_available() -> bool:
    """Check if LuxTTS and PyTorch dependencies are available."""
    try:
        import torch
        from zipvoice.luxvoice import LuxTTS
        return True
    except Exception as e:
        logger.debug(f"LuxTTS not available: {e}")
        return False


def get_lux_model(device: Optional[str] = None):
    """Loads and caches the LuxTTS model singleton."""
    global _CACHED_LUX_MODEL
    if _CACHED_LUX_MODEL is not None:
        return _CACHED_LUX_MODEL

    import torch
    from zipvoice.luxvoice import LuxTTS

    if device is None:
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    logger.info(f"Loading LuxTTS (Voicebox 48kHz) model on device: {device}...")
    start = time.perf_counter()
    _CACHED_LUX_MODEL = LuxTTS(device=device)
    load_time = (time.perf_counter() - start) * 1000
    logger.info(f"✅ LuxTTS model loaded in {load_time:.0f}ms on {_CACHED_LUX_MODEL.device}")
    return _CACHED_LUX_MODEL


def get_encoded_prompt(model, ref_audio_path: str, duration: float = 5.0, rms: float = 0.01):
    """
    Encodes audio prompt using the model and caches it so prompt encoding (which uses Whisper)
    is only run once per reference audio file.
    """
    global _CACHED_PROMPT_KEY, _CACHED_PROMPT_DATA
    cache_key = f"{ref_audio_path}:{duration}:{rms}"
    if _CACHED_PROMPT_DATA is not None and _CACHED_PROMPT_KEY == cache_key:
        return _CACHED_PROMPT_DATA

    start = time.perf_counter()
    prompt_data = model.encode_prompt(ref_audio_path, duration=duration, rms=rms)
    enc_time = (time.perf_counter() - start) * 1000
    logger.info(f"⚡ [LuxTTS] Encoded reference prompt for '{Path(ref_audio_path).name}' in {enc_time:.0f}ms")
    _CACHED_PROMPT_KEY = cache_key
    _CACHED_PROMPT_DATA = prompt_data
    return prompt_data


def _split_into_sentences(text: str) -> List[str]:
    """Splits conversational text into bite-sized sentences for low-latency streaming."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    result = []
    for s in sentences:
        s = s.strip()
        if s:
            result.append(s)
    return result if result else [text.strip()]


def _synthesize_sentence_sync(model, text: str, encode_dict: dict, num_steps: int = 4, speed: float = 1.0) -> bytes:
    """Synchronous LuxTTS inference called via asyncio.to_thread to keep event loop free."""
    wav = model.generate_speech(text, encode_dict, num_steps=num_steps, speed=speed)
    np_wav = wav.numpy().squeeze()
    pcm16 = (np.clip(np_wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return pcm16


def synthesize_preview_mp3(
    text: str,
    ref_audio_path: str,
    num_steps: int = 4,
    speed: float = 1.0,
) -> bytes:
    """
    Synthesizes speech using LuxTTS at 48kHz and encodes to MP3 for web playback.
    """
    model = get_lux_model()
    prompt = get_encoded_prompt(model, ref_audio_path, duration=5.0, rms=0.01)
    wav = model.generate_speech(text, prompt, num_steps=num_steps, speed=speed)
    np_wav = wav.numpy().squeeze()
    pcm16 = (np.clip(np_wav, -1.0, 1.0) * 32767).astype(np.int16)

    out_buf = io.BytesIO()
    out_container = av.open(out_buf, mode="w", format="mp3")
    out_stream = out_container.add_stream("mp3", rate=SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(pcm16.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = SAMPLE_RATE
    for packet in out_stream.encode(frame):
        out_container.mux(packet)
    for packet in out_stream.encode(None):
        out_container.mux(packet)
    out_container.close()
    return out_buf.getvalue()


class LuxTTSLiveKit(tts.TTS):
    """
    LiveKit Agents TTS adapter for LuxTTS (Voicebox 48kHz ultra-fast voice cloning).
    Outputs crystal-clear 48kHz audio.
    """

    def __init__(
        self,
        *,
        ref_audio_path: Optional[str] = None,
        num_steps: int = DEFAULT_NUM_STEPS,
        speed: float = DEFAULT_SPEED,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
        )
        self._ref_audio_path = ref_audio_path
        self._num_steps = num_steps
        self._speed = speed

        # Eagerly pre-load model and encode prompt at init time
        try:
            model = get_lux_model()
            if ref_audio_path and os.path.exists(ref_audio_path):
                get_encoded_prompt(model, ref_audio_path)
                logger.info("⚡ LuxTTS model + reference prompt pre-cached at init")
        except Exception as e:
            logger.warning(f"Pre-load during init failed: {e}")

    @property
    def model(self) -> str:
        return "luxtts-48k"

    @property
    def provider(self) -> str:
        return "LuxTTS"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return LuxChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            ref_audio_path=self._ref_audio_path,
            num_steps=self._num_steps,
            speed=self._speed,
            sample_rate=self.sample_rate,
        )


class LuxChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: tts.TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        ref_audio_path: Optional[str],
        num_steps: int,
        speed: float,
        sample_rate: int,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._ref_audio_path = ref_audio_path
        self._num_steps = num_steps
        self._speed = speed
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

        ref_path = self._ref_audio_path
        if not ref_path or not os.path.exists(ref_path):
            ref_files = list(PROFILES_DIR.glob("*_ref.wav")) or list(PROFILES_DIR.glob("*_ref_24k.wav"))
            if ref_files:
                ref_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                ref_path = str(ref_files[0])
                logger.info(f"Using latest saved profile reference: {ref_path}")

        if not ref_path or not os.path.exists(ref_path):
            logger.warning("No reference audio file found for LuxTTS voice cloning.")
            output_emitter.flush()
            return

        try:
            synth_start = time.perf_counter()
            model = get_lux_model()
            prompt = get_encoded_prompt(model, ref_path)

            sentences = _split_into_sentences(clean_text)
            logger.info(f"🎙️ [LuxTTS] Synthesizing {len(sentences)} sentence(s) at 48kHz (steps={self._num_steps})...")

            for idx, sentence_text in enumerate(sentences):
                st_text = sentence_text.strip()
                if not st_text:
                    continue

                sentence_start = time.perf_counter()
                pcm_bytes = await asyncio.to_thread(
                    _synthesize_sentence_sync,
                    model, st_text, prompt,
                    self._num_steps, self._speed,
                )

                latency_ms = (time.perf_counter() - sentence_start) * 1000
                logger.info(
                    f"⚡ [LuxTTS] Chunk {idx + 1}/{len(sentences)} ({len(pcm_bytes)} bytes) generated in {latency_ms:.0f}ms"
                )
                output_emitter.push(pcm_bytes)

            total_ms = (time.perf_counter() - synth_start) * 1000
            logger.info(f"⚡ [LuxTTS] Total synthesis completed in {total_ms:.0f}ms")
            output_emitter.flush()
        except Exception as e:
            logger.error(f"❌ LuxTTS synthesis error: {e}", exc_info=True)
