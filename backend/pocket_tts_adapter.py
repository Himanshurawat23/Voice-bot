"""
PocketTTS LiveKit Agents TTS Adapter
=====================================
Integrates Kyutai Labs' PocketTTS (100M params, CPU-only) into the LiveKit
voice agent pipeline. Supports:
  - Pre-built voices (alba, jean, marius, etc.)
  - Zero-shot voice cloning from a reference WAV file
  - Fast voice loading from exported .safetensors embeddings
  - Sentence-level chunked synthesis for low perceived latency
  - 7 languages: english, french, german, spanish, italian, portuguese, dutch

PocketTTS runs ~6x real-time on a modern CPU with ~200ms first-chunk latency,
making it viable for real-time conversational agents without a GPU.

Usage in agent.py:
    from pocket_tts_adapter import PocketTTSLiveKit, is_pocket_tts_available

    if is_pocket_tts_available():
        tts = PocketTTSLiveKit(
            voice="alba",                           # Pre-built voice
            # ref_audio_path="profiles/user_ref.wav",  # Or clone from WAV
            # safetensors_path="profiles/user.safetensors",  # Or fast-load
            language="english",
        )
"""

import io
import re
import os
import time
import asyncio
import logging
import functools
import numpy as np
from pathlib import Path
from typing import Optional
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

logger = logging.getLogger("pocket-tts-adapter")

# Global singleton cache — model is kept in memory across utterances
_CACHED_MODEL = None
_CACHED_VOICE_STATES = {}  # keyed by voice identifier (name, path, or safetensors path)

PROFILES_DIR = Path(__file__).parent / "profiles"

# PocketTTS output sample rate (fixed by the model)
POCKET_TTS_SAMPLE_RATE = 24000


CATALOG_VOICES = [
    "alba", "jean", "marius", "javert", "anna", "vera", "fantine", "charles",
    "paul", "eponine", "azelma", "george", "mary", "jane", "michael", "eve",
    "bill_boerst", "peter_yearsley", "stuart_bell", "caro_davy", "giovanni",
    "lola", "juergen", "rafael", "daan", "estelle", "cosette",
]
DEFAULT_VOICE = "alba"


def is_pocket_tts_available() -> bool:
    """Check if pocket-tts is installed, importable, and safe to run in the current environment."""
    try:
        from pocket_tts import TTSModel  # noqa: F401

        # Render free/starter tiers have strict 512MB RAM limits enforced by cgroups.
        # PocketTTS + PyTorch runtime requires ~750MB RAM, causing container OOM crashes.
        # When running on Render, gracefully yield to EdgeTTS/Deepgram unless explicitly enabled.
        if os.environ.get("RENDER"):
            force_enable = os.environ.get("ENABLE_POCKET_TTS_ON_RENDER", "false").lower() == "true"
            if not force_enable:
                logger.info(
                    "Detected Render cloud container (512MB RAM limit). "
                    "PocketTTS requires ~750MB RAM. Falling back to zero-RAM EdgeTTS/Deepgram to prevent OOM."
                )
                return False

        return True
    except ImportError:
        logger.debug("pocket-tts not installed. Install with: pip install pocket-tts")
        return False


def _get_pocket_tts_model(language: str = "english"):
    """Loads and caches the PocketTTS model. Thread-safe via GIL for the initial load."""
    global _CACHED_MODEL
    cache_key = language
    if isinstance(_CACHED_MODEL, dict) and cache_key in _CACHED_MODEL:
        return _CACHED_MODEL[cache_key]

    from pocket_tts import TTSModel

    logger.info(f"Loading PocketTTS model (language={language})...")
    start = time.perf_counter()
    model = TTSModel.load_model(language=language)
    load_ms = (time.perf_counter() - start) * 1000
    logger.info(f"✅ PocketTTS model loaded in {load_ms:.0f}ms")

    if not isinstance(_CACHED_MODEL, dict):
        _CACHED_MODEL = {}
    _CACHED_MODEL[cache_key] = model
    return model


def _get_voice_state(
    model,
    voice_identifier: str,
    safetensors_path: Optional[str] = None,
    fallback_voice: str = DEFAULT_VOICE,
):
    """
    Gets and caches a voice state for the given identifier.
    Voice states are cached to avoid re-processing audio on every utterance.

    Priority:
      1. safetensors_path (fastest — instant load)
      2. voice_identifier as audio file path (if voice cloning model is authenticated)
      3. fallback_voice (pre-built catalog voice: alba, jean, etc.)
    """
    global _CACHED_VOICE_STATES

    cache_key = safetensors_path or voice_identifier or fallback_voice
    if cache_key in _CACHED_VOICE_STATES:
        return _CACHED_VOICE_STATES[cache_key]

    start = time.perf_counter()

    if safetensors_path and os.path.exists(safetensors_path):
        try:
            voice_state = model.get_state_for_safetensors(safetensors_path)
            load_method = "safetensors"
        except Exception as e:
            logger.warning(f"Failed to load safetensors voice from {safetensors_path}: {e}")
            target_voice = fallback_voice if fallback_voice in CATALOG_VOICES else DEFAULT_VOICE
            voice_state = model.get_state_for_audio_prompt(target_voice)
            load_method = f"fallback pre-built ({target_voice})"

    elif voice_identifier and os.path.exists(voice_identifier):
        # Audio file provided for zero-shot voice cloning
        has_cloning = getattr(model, "has_voice_cloning", True)
        if has_cloning:
            try:
                voice_state = model.get_state_for_audio_prompt(voice_identifier)
                load_method = "audio file (voice cloned)"
            except Exception as e:
                target_voice = fallback_voice if fallback_voice in CATALOG_VOICES else DEFAULT_VOICE
                logger.warning(
                    f"⚠️ PocketTTS voice cloning from '{voice_identifier}' failed: {e}. "
                    f"Falling back to pre-built voice '{target_voice}'. "
                    f"To enable cloning: accept terms at https://huggingface.co/kyutai/pocket-tts and set HF_TOKEN in .env"
                )
                voice_state = model.get_state_for_audio_prompt(target_voice)
                load_method = f"fallback pre-built ({target_voice})"
        else:
            target_voice = fallback_voice if fallback_voice in CATALOG_VOICES else DEFAULT_VOICE
            logger.warning(
                f"ℹ️ PocketTTS running in CPU catalog mode (voice cloning weights require HuggingFace auth: "
                f"accept terms at https://huggingface.co/kyutai/pocket-tts and add HF_TOKEN in .env). "
                f"Using pre-built voice '{target_voice}'."
            )
            voice_state = model.get_state_for_audio_prompt(target_voice)
            load_method = f"pre-built ({target_voice})"

    else:
        # Pre-built catalog voice name (e.g. "alba")
        target_voice = voice_identifier if voice_identifier in CATALOG_VOICES else fallback_voice
        if target_voice not in CATALOG_VOICES:
            target_voice = DEFAULT_VOICE
        voice_state = model.get_state_for_audio_prompt(target_voice)
        load_method = f"pre-built ({target_voice})"

    load_ms = (time.perf_counter() - start) * 1000
    logger.info(f"✅ PocketTTS voice loaded via {load_method} in {load_ms:.0f}ms ('{cache_key}')")

    _CACHED_VOICE_STATES[cache_key] = voice_state
    return voice_state


def _split_sentences(text: str) -> list:
    """Split text into sentences for chunked synthesis."""
    # Split on sentence-ending punctuation followed by whitespace
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    # Filter out empty parts
    return [p.strip() for p in parts if p.strip()]


def _synthesize_sync(model, voice_state, text: str):
    """
    Runs PocketTTS synthesis synchronously.
    Designed to be called via asyncio.to_thread() to avoid blocking the event loop.
    Returns: PCM int16 bytes at POCKET_TTS_SAMPLE_RATE Hz mono.
    """
    audio_tensor = model.generate_audio(voice_state, text)
    # audio_tensor is a 1D torch tensor of float32 PCM
    np_wave = audio_tensor.numpy()
    # Convert float32 [-1, 1] to int16 PCM bytes
    pcm_bytes = (np.clip(np_wave, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return pcm_bytes


def synthesize_preview_mp3(
    text: str,
    ref_audio_path: Optional[str] = None,
    safetensors_path: Optional[str] = None,
    voice: str = DEFAULT_VOICE,
    language: str = "english",
) -> bytes:
    """
    Synthesizes a short preview utterance using PocketTTS and encodes to MP3.
    Used for frontend calibration preview playback.
    """
    import av

    model = _get_pocket_tts_model(language)
    voice_state = _get_voice_state(
        model,
        voice_identifier=ref_audio_path or voice,
        safetensors_path=safetensors_path,
        fallback_voice=voice,
    )
    audio_tensor = model.generate_audio(voice_state, text)
    np_wave = audio_tensor.numpy()
    pcm16 = (np.clip(np_wave, -1.0, 1.0) * 32767).astype(np.int16)

    out_buf = io.BytesIO()
    out_container = av.open(out_buf, mode="w", format="mp3")
    out_stream = out_container.add_stream("mp3", rate=POCKET_TTS_SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(pcm16.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = POCKET_TTS_SAMPLE_RATE
    for packet in out_stream.encode(frame):
        out_container.mux(packet)
    for packet in out_stream.encode(None):
        out_container.mux(packet)
    out_container.close()

    logger.info(f"✅ PocketTTS preview synthesized ({len(out_buf.getvalue())} bytes)")
    return out_buf.getvalue()


class PocketTTSLiveKit(tts.TTS):
    """
    LiveKit Agents TTS adapter for PocketTTS by Kyutai Labs.

    CPU-only, 100M params, ~6x real-time, ~200ms first-chunk latency.
    Supports pre-built voices, voice cloning from audio, and fast loading
    from exported .safetensors voice embeddings.

    Args:
        voice:            Pre-built voice name (e.g. "alba", "jean") or path to a .wav file.
        ref_audio_path:   Path to a reference audio file for voice cloning.
                          Overrides `voice` if provided.
        safetensors_path: Path to an exported .safetensors voice embedding.
                          Fastest loading method (~0.01s). Overrides both `voice` and `ref_audio_path`.
        language:         Language model to use. One of: english, french, german,
                          spanish, italian, portuguese, dutch.
                          Also supports 24-layer variants: french_24l, german_24l, etc.
        sample_rate:      Output sample rate (default 24000, matching PocketTTS native rate).
    """

    def __init__(
        self,
        *,
        voice: str = DEFAULT_VOICE,
        ref_audio_path: Optional[str] = None,
        safetensors_path: Optional[str] = None,
        language: str = "english",
        sample_rate: int = POCKET_TTS_SAMPLE_RATE,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._voice = voice
        self._ref_audio_path = ref_audio_path
        self._safetensors_path = safetensors_path
        self._language = language

        # Determine the effective voice identifier
        # Priority: safetensors > ref_audio_path > voice name
        if safetensors_path and os.path.exists(safetensors_path):
            self._voice_identifier = safetensors_path
        elif ref_audio_path and os.path.exists(ref_audio_path):
            self._voice_identifier = ref_audio_path
        else:
            self._voice_identifier = voice or DEFAULT_VOICE

        # Pre-load model and voice at init time for fast first utterance
        try:
            model = _get_pocket_tts_model(language)
            _get_voice_state(model, self._voice_identifier, safetensors_path, fallback_voice=voice)
            logger.info(
                f"⚡ PocketTTS pre-cached: voice='{self._voice_identifier}', "
                f"language='{language}'"
            )
        except Exception as e:
            logger.warning(
                f"PocketTTS pre-load warning (will retry on first utterance): {e}"
            )

    @property
    def model(self) -> str:
        return "pocket-tts"

    @property
    def provider(self) -> str:
        return "PocketTTS"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return PocketTTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            voice_identifier=self._voice_identifier,
            safetensors_path=self._safetensors_path,
            fallback_voice=self._voice,
            language=self._language,
            sample_rate=self.sample_rate,
        )


class PocketTTSChunkedStream(tts.ChunkedStream):
    """
    Chunked stream that splits text into sentences and synthesizes each one
    individually, pushing PCM audio chunks as they're generated for lower
    perceived latency.
    """

    def __init__(
        self,
        *,
        tts: tts.TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        voice_identifier: str,
        safetensors_path: Optional[str],
        fallback_voice: str = DEFAULT_VOICE,
        language: str = "english",
        sample_rate: int,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voice_identifier = voice_identifier
        self._safetensors_path = safetensors_path
        self._fallback_voice = fallback_voice
        self._language = language
        self._sample_rate = sample_rate

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # Crucial: AudioEmitter must ALWAYS be initialized immediately.
        # Otherwise if _run returns early or raises an exception, the framework's
        # finally block calls output_emitter.end_input() which raises:
        # "RuntimeError: AudioEmitter isn't started"
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

        try:
            synth_start = time.perf_counter()

            # Load model and voice state (cached after first call)
            model = _get_pocket_tts_model(self._language)
            voice_state = _get_voice_state(
                model,
                self._voice_identifier,
                self._safetensors_path,
                fallback_voice=self._fallback_voice,
            )

            # Split into sentences for chunked delivery
            sentences = _split_sentences(clean_text)
            if not sentences:
                sentences = [clean_text]

            logger.info(
                f"🎙️ [PocketTTS] Synthesizing {len(sentences)} sentence(s) "
                f"(voice='{self._voice_identifier}', lang='{self._language}')"
            )

            for idx, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue

                sentence_start = time.perf_counter()

                # Run synthesis in a thread to avoid blocking the async event loop.
                # This is critical: without this, the synchronous PyTorch inference
                # blocks VAD/STT processing on incoming audio.
                pcm_bytes = await asyncio.to_thread(
                    _synthesize_sync, model, voice_state, sentence
                )

                latency_ms = (time.perf_counter() - sentence_start) * 1000
                audio_duration = len(pcm_bytes) / (self._sample_rate * 2)  # 2 bytes per int16 sample
                rtf = audio_duration / (latency_ms / 1000) if latency_ms > 0 else 0

                logger.info(
                    f"⚡ [PocketTTS] Chunk {idx + 1}/{len(sentences)} "
                    f"({audio_duration:.1f}s audio) generated in {latency_ms:.0f}ms "
                    f"({rtf:.1f}x real-time)"
                )

                output_emitter.push(pcm_bytes)

            total_ms = (time.perf_counter() - synth_start) * 1000
            logger.info(f"⚡ [PocketTTS] Total synthesis completed in {total_ms:.0f}ms")
            output_emitter.flush()

        except Exception as e:
            logger.error(f"❌ PocketTTS synthesis error: {e}", exc_info=True)
            try:
                output_emitter.flush()
            except Exception:
                pass

