import io
import re
import logging
import av
import edge_tts
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

logger = logging.getLogger("edge-tts-adapter")


def has_devanagari(text: str) -> bool:
    """Check if text contains Devanagari script (Hindi)."""
    return any("\u0900" <= char <= "\u097f" for char in text)


def resolve_voice_for_text(requested_voice: str, text: str) -> str:
    """
    Route Devanagari Hindi text to authentic Hindi neural voices (hi-IN-Madhur / hi-IN-Swara),
    preventing NoAudioReceived exceptions when an English-only voice (en-IN-Prabhat) is used.
    """
    if has_devanagari(text):
        lower_v = (requested_voice or "").lower()
        if any(female in lower_v for female in ["neerja", "swara", "female", "kavya", "anamika"]):
            return "hi-IN-SwaraNeural"
        return "hi-IN-MadhurNeural"
    return requested_voice or "en-IN-PrabhatNeural"


class EdgeTTS(tts.TTS):
    """
    LiveKit Agents TTS adapter for Microsoft Neural TTS via edge-tts.
    Provides authentic regional voices including Indian English (en-IN-PrabhatNeural,
    en-IN-NeerjaExpressiveNeural), Hindi (hi-IN-MadhurNeural, hi-IN-SwaraNeural),
    with automatic script-aware bilingual switching.
    """

    def __init__(self, *, voice: str = "en-IN-PrabhatNeural", sample_rate: int = 24000):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._voice = voice

    @property
    def model(self) -> str:
        return self._voice

    @property
    def provider(self) -> str:
        return "EdgeTTS"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return EdgeChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            voice=self._voice,
            sample_rate=self.sample_rate,
        )


class EdgeChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: tts.TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        voice: str,
        sample_rate: int,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voice = voice
        self._sample_rate = sample_rate

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        clean_text = self._input_text.strip()
        # EdgeTTS fails with NoAudioReceived if given only punctuation or whitespace
        stripped_alnum = re.sub(r"[^\w\s]", "", clean_text)
        if not clean_text or not stripped_alnum.strip():
            return

        target_voice = resolve_voice_for_text(self._voice, clean_text)
        mp3_bytes = []

        try:
            comm = edge_tts.Communicate(clean_text, target_voice)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    mp3_bytes.append(chunk["data"])
        except edge_tts.exceptions.NoAudioReceived:
            # Fallback attempt with universal Indian voice
            fallback_voice = "hi-IN-MadhurNeural" if has_devanagari(clean_text) else "en-IN-PrabhatNeural"
            if fallback_voice != target_voice:
                try:
                    logger.warning(
                        f"No audio with {target_voice}, falling back to {fallback_voice}"
                    )
                    comm = edge_tts.Communicate(clean_text, fallback_voice)
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            mp3_bytes.append(chunk["data"])
                except Exception as fb_err:
                    logger.error(f"Fallback TTS failed: {fb_err}")
            else:
                logger.warning(
                    f"No audio received from EdgeTTS for text: '{clean_text[:50]}'"
                )
        except Exception as e:
            logger.error(f"EdgeTTS generation error: {e}")

        full_mp3 = b"".join(mp3_bytes)
        if not full_mp3:
            return

        try:
            container = av.open(io.BytesIO(full_mp3))
            in_stream = container.streams.audio[0]
            resampler = av.AudioResampler(
                format="s16", layout="mono", rate=self._sample_rate
            )

            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=self._sample_rate,
                num_channels=1,
                mime_type="audio/pcm",
            )

            for frame in container.decode(in_stream):
                for resampled in resampler.resample(frame):
                    output_emitter.push(resampled.to_ndarray().tobytes())

            output_emitter.flush()
        except Exception as decode_err:
            logger.error(f"Error decoding EdgeTTS audio container: {decode_err}")
