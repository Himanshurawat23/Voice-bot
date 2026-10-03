import os
import sys
import json
import time
import uuid
import asyncio
import logging
from typing import AsyncIterable, Union
from pathlib import Path
from dotenv import load_dotenv

# Load .env from current directory or parent directory
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("livekit-agent")

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    ModelSettings,
    cli,
    room_io,
)
from livekit.agents.worker import JobExecutorType
from livekit.agents.llm import ChatChunk
from livekit.agents.llm import ChatContext, Tool
from livekit.agents.llm import ChatMessage
from livekit.agents.voice import agent_session
from livekit.plugins import silero, deepgram
from livekit.plugins import openai as lk_openai
try:
    from livekit.plugins import noise_cancellation
    NOISE_CANCELLATION_AVAILABLE = True
except ImportError:
    NOISE_CANCELLATION_AVAILABLE = False
    noise_cancellation = None
from livekit.agents.metrics import LLMMetrics, TTSMetrics, STTMetrics, EOUMetrics

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-2-general")
STT_LANGUAGE = os.getenv("DEEPGRAM_STT_LANGUAGE", "en-IN")
TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "en-IN-PrabhatNeural")
LLM_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "150"))
MAX_CHAT_HISTORY = int(os.getenv("MAX_CHAT_HISTORY", "6"))

ENABLE_NOISE_CANCELLATION = os.getenv("LIVEKIT_NOISE_CANCELLATION", "true").lower() in ("true", "1", "yes")
NOISE_CANCELLATION_MODEL = os.getenv("LIVEKIT_NOISE_CANCELLATION_MODEL", "BVC").upper()


from typing import AsyncIterable, Union, Optional
import voice_profile_manager


class VoiceAssistant(Agent):
    def __init__(self, ctx: JobContext, instructions: Optional[str] = None) -> None:
        default_instructions = (
            "You are an engaging, articulate, and friendly AI voice assistant with a natural Indian conversational style. "
            "You converse with the user in real time over WebRTC voice audio and text chat. "
            "You effortlessly understand Indian English, Indian accents, and common Hindi/Hinglish phrasing. "
            "Keep your answers short, crisp, and conversational (1 to 2 sentences maximum, under 25 words) unless asked for more detail. "
            "Never use fluff, filler intros like 'Certainly, I would be happy to help', or long repetitive explanations. "
            "Speak naturally and warmly as if talking on a phone call. "
            "Do not use markdown formatting, bullet points, asterisks, or code blocks in your output."
        )
        super().__init__(instructions=instructions or default_instructions)
        self._ctx = ctx

    async def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list[Tool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[Union[ChatChunk, str]]:
        """Override llm_node with token-saving context truncation and live streaming."""
        # 1. Token Saver: Sliding window truncation prevents context snowballing over long calls
        # Keeps system prompt + only the last N messages
        chat_ctx.truncate(max_items=MAX_CHAT_HISTORY)

        # 2. Token Saver: Skip LLM call on accidental microphone noise / filler sounds
        msgs = chat_ctx.messages() if callable(getattr(chat_ctx, "messages", None)) else getattr(chat_ctx, "items", [])
        last_msg = msgs[-1] if msgs else None
        if last_msg and getattr(last_msg, "role", None) == "user":
            user_text = (getattr(last_msg, "text_content", "") or "").strip().lower()
            # Ignore isolated filler sounds or single character mic taps
            if user_text in {"uh", "um", "ah", "hmm", "er", "mm"} or (len(user_text) <= 1 and user_text != "?"):
                logger.info(f"⚡ [Token Saver] Ignored filler/mic noise: '{user_text}' (0 tokens burned)")
                return

        stream_id = uuid.uuid4().hex[:12]

        # Signal stream start to the frontend
        asyncio.create_task(
            self._ctx.room.local_participant.publish_data(
                json.dumps({"type": "stream_start", "stream_id": stream_id,
                            "timestamp": int(time.time() * 1000)}).encode(),
                topic="lk-agent-stream",
            )
        )

        start_time = time.perf_counter()
        first_token_time = None
        chunk_count = 0

        try:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                chunk_count += 1
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                    ttft_ms = (first_token_time - start_time) * 1000
                    logger.info(f"⚡ [LLM TTFT] Time to first token: {ttft_ms:.1f}ms")

                # Extract text delta from the chunk and stream it to the frontend
                if isinstance(chunk, ChatChunk) and chunk.delta and chunk.delta.content:
                    asyncio.create_task(
                        self._ctx.room.local_participant.publish_data(
                            json.dumps({
                                "type": "stream_chunk",
                                "stream_id": stream_id,
                                "delta": chunk.delta.content,
                                "timestamp": int(time.time() * 1000),
                            }).encode(),
                            topic="lk-agent-stream",
                        )
                    )
                elif isinstance(chunk, str) and chunk:
                    asyncio.create_task(
                        self._ctx.room.local_participant.publish_data(
                            json.dumps({
                                "type": "stream_chunk",
                                "stream_id": stream_id,
                                "delta": chunk,
                                "timestamp": int(time.time() * 1000),
                            }).encode(),
                            topic="lk-agent-stream",
                        )
                    )
                yield chunk
        except Exception as err:
            logger.error(f"❌ Error during LLM generation: {err}", exc_info=True)
            raise
        finally:
            total_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⚡ [LLM Done] Total generation took {total_ms:.0f}ms ({chunk_count} chunks)")

            # Signal stream end
            asyncio.create_task(
                self._ctx.room.local_participant.publish_data(
                    json.dumps({"type": "stream_end", "stream_id": stream_id,
                                "timestamp": int(time.time() * 1000)}).encode(),
                    topic="lk-agent-stream",
                )
            )


# Configure AgentServer optimized for cloud container environments (Render/Koyeb):
# - job_executor_type=THREAD: uses lightweight threads instead of heavy child processes, saving ~300MB RAM
# - num_idle_processes=0: no idle processes to spawn or time out
# - host="127.0.0.1": keeps LiveKit internal server strictly on localhost so Render won't route public traffic to it
# - load_threshold=1.0: prevents worker from marking itself unavailable under brief CPU spikes
server = AgentServer(
    job_executor_type=JobExecutorType.THREAD,
    num_idle_processes=0,
    load_threshold=1.0,
    host="127.0.0.1",
    port=0,
)


@server.rtc_session(agent_name=os.getenv("LIVEKIT_AGENT_NAME", "indian-voice-agent"))
async def voice_agent_session(ctx: JobContext):
    logger.info(f"Starting voice agent session for room '{ctx.room.name}'...")

    # Balanced VAD tuned for conversational responsiveness:
    # - activation_threshold=0.60: reliable speech detection while rejecting faint background noise
    # - min_speech_duration=0.20: avoids false triggers on coughs, clicks, or keyboard taps
    # - min_silence_duration=0.50: allows natural short pauses during thought/breathing (500ms)
    vad = silero.VAD.load(
        activation_threshold=0.60,
        min_speech_duration=0.20,
        min_silence_duration=0.50,
    )
    stt = deepgram.STT(
        model=STT_MODEL,
        language=STT_LANGUAGE,
        smart_format=True,
        punctuate=True,
        api_key=DEEPGRAM_API_KEY,
    )
    llm = lk_openai.LLM(
        model=LLM_MODEL,
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY,
        temperature=TEMPERATURE,
        extra_body={"max_tokens": MAX_TOKENS},  # Hard cap completion tokens to prevent verbose burn
    )
    from edge_tts_adapter import EdgeTTS
    from f5_tts_adapter import F5TTS, is_f5_tts_available
    from luxtts_adapter import LuxTTSLiveKit, is_luxtts_available
    from pocket_tts_adapter import PocketTTSLiveKit, is_pocket_tts_available

    active_profile = None
    for p in ctx.room.remote_participants.values():
        if getattr(p, "kind", None) != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            active_profile = voice_profile_manager.get_voice_profile(p.identity) or voice_profile_manager.get_voice_profile(getattr(p, "name", ""))
            if active_profile:
                break
    if not active_profile:
        active_profile = voice_profile_manager.get_voice_profile("")
    selected_tts_model = active_profile.get("recommended_voice", TTS_MODEL) if active_profile else TTS_MODEL
    logger.info(f"Selected TTS voice model: {selected_tts_model}")

    tts = None
    # 1. Check LuxTTS (Voicebox 48kHz ultra-fast voice cloning)
    if (selected_tts_model == "luxtts" or "lux" in selected_tts_model.lower()) and is_luxtts_available():
        ref_wav = active_profile.get("ref_wav_file") or active_profile.get("ref_wav_24k_file") if active_profile else None
        try:
            tts = LuxTTSLiveKit(ref_audio_path=ref_wav, num_steps=4, speed=1.0)
            logger.info(f"🎙️ Initialized LuxTTS (Voicebox 48kHz) cloned voice adapter (ref: {ref_wav})")
        except Exception as e:
            logger.warning(f"Could not initialize LuxTTS, falling back: {e}")
            tts = None

    # 2. Check PocketTTS (CPU-only, 100M params, ~6x real-time, voice cloning)
    if tts is None and (selected_tts_model == "pocket-tts" or "pocket" in selected_tts_model.lower()) and is_pocket_tts_available():
        ref_wav = active_profile.get("ref_wav_file") or active_profile.get("ref_wav_24k_file") if active_profile else None
        safetensors = active_profile.get("pocket_tts_safetensors") if active_profile else None
        pocket_voice = active_profile.get("pocket_tts_voice", "alba") if active_profile else "alba"
        pocket_lang = active_profile.get("pocket_tts_language", "english") if active_profile else "english"
        try:
            tts = PocketTTSLiveKit(
                voice=pocket_voice,
                ref_audio_path=ref_wav,
                safetensors_path=safetensors,
                language=pocket_lang,
            )
            logger.info(f"🎙️ Initialized PocketTTS adapter (voice={pocket_voice}, ref={ref_wav}, lang={pocket_lang})")
        except Exception as e:
            logger.warning(f"Could not initialize PocketTTS, falling back: {e}")
            tts = None

    # 3. Check F5-TTS cloned voice adapter
    if tts is None and (selected_tts_model == "f5-tts" or "f5" in selected_tts_model.lower()) and is_f5_tts_available():
        ref_24k = active_profile.get("ref_wav_24k_file") if active_profile else None
        ref_text = active_profile.get("ref_text") if active_profile else None
        try:
            tts = F5TTS(ref_audio_path=ref_24k, ref_audio_text=ref_text, steps=6, speed=1.0, cfg_strength=2.0)
            logger.info(f"🎙️ Initialized F5-TTS cloned voice adapter (ref: {ref_24k}, steps=6, cfg=2.0)")
        except Exception as e:
            logger.warning(f"Could not initialize F5-TTS, falling back to EdgeTTS: {e}")
            tts = None

    if tts is None:
        if selected_tts_model.startswith("en-IN-") or selected_tts_model.startswith("hi-IN-") or "Neural" in selected_tts_model or selected_tts_model in ("f5-tts", "luxtts", "pocket-tts"):
            edge_voice = "en-IN-PrabhatNeural" if selected_tts_model in ("f5-tts", "luxtts", "pocket-tts") else selected_tts_model
            tts = EdgeTTS(voice=edge_voice)
        else:
            tts = deepgram.TTS(model=selected_tts_model, api_key=DEEPGRAM_API_KEY)

    # AgentSession configured for smooth conversational flow without false interruptions:
    # - min_endpointing_delay=0.50: waits 500ms after user pauses before responding
    # - Interruption: requires 500ms duration and at least 2 spoken words, preventing speaker echo from cutting off speech
    session = AgentSession(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
        min_endpointing_delay=0.50,
        turn_handling=agent_session.TurnHandlingOptions(
            interruption=agent_session.InterruptionOptions(
                min_duration=0.50,
                min_words=2,
                resume_false_interruption=True,
            )
        ),
    )

    # Publish conversation items to the chat so the UI stays in sync.
    # - "assistant" replies → already streamed via llm_node override (lk-agent-stream)
    # - "user" voice utterances → lk-voice-transcript (shows as user message)
    # AgentHandoff items don't have .role so we guard with isinstance first.
    @session.on("conversation_item_added")
    def on_item_added(event: agent_session.ConversationItemAddedEvent):
        if not isinstance(event.item, ChatMessage):
            return
        role = event.item.role
        text = event.item.text_content
        if not text:
            return
        text = text.strip()
        if not text:
            return

        # "user" messages are NOT re-published here because conversation_item_added
        # fires for BOTH typed chat messages AND voice — re-publishing would create
        # duplicates since typed messages already appear via useChat in the frontend.
        # Spoken voice transcriptions are published in on_user_input_transcribed below.
        if role == "user":
            logger.info(f"User turn added: '{text}'")

    # Forward user voice transcriptions to the UI chat on topic "lk-voice-transcript"
    # This ensures spoken utterances appear as "You: ..." in the chat transcript.
    # Typed messages already appear in the UI via useChat (so this never duplicates them).
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: agent_session.UserInputTranscribedEvent):
        if not event.is_final:
            return
        text = (event.transcript or "").strip()
        if not text:
            return
        logger.info(f"🎤 [User Voice]: '{text}'")
        msg_id = getattr(event, "item_id", None) or f"user-voice-{uuid.uuid4().hex[:8]}-{int(time.time() * 1000)}"
        now_ts = int(time.time() * 1000)
        try:
            asyncio.create_task(
                ctx.room.local_participant.publish_data(
                    json.dumps({
                        "id": msg_id,
                        "message": text,
                        "sender": "user",
                        "timestamp": now_ts,
                    }).encode("utf-8"),
                    topic="lk-voice-transcript",
                )
            )
        except Exception as err:
            logger.debug(f"Failed to publish user voice transcript: {err}")

    # Built-in LiveKit metrics collector: unpacks MetricsCollectedEvent (LLM, TTS, STT, EOU)
    @session.on("metrics_collected")
    def on_metrics_collected(event):
        m = getattr(event, "metrics", event)
        if isinstance(m, LLMMetrics):
            ttft_ms = round(m.ttft * 1000) if getattr(m, "ttft", None) else None
            tps = round(m.tokens_per_second, 1) if getattr(m, "tokens_per_second", None) else None
            duration_ms = round(m.duration * 1000) if getattr(m, "duration", None) else None
            logger.info(f"⚡ [LLM Metric] TTFT: {ttft_ms}ms | Speed: {tps} tok/s | Total: {duration_ms}ms")
            try:
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps({
                            "type": "turn_metrics",
                            "ttft_ms": ttft_ms,
                            "tps": tps,
                        }).encode(),
                        topic="lk-agent-metrics",
                    )
                )
            except Exception as err:
                logger.debug(f"Could not publish LLM metrics: {err}")
        elif isinstance(m, TTSMetrics):
            ttfb_ms = round(m.ttfb * 1000) if getattr(m, "ttfb", None) else None
            logger.info(f"🔊 [TTS Metric] TTFB: {ttfb_ms}ms | Audio: {round(getattr(m, 'audio_duration', 0), 1)}s")
            try:
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps({
                            "type": "turn_metrics",
                            "ttfb_ms": ttfb_ms,
                        }).encode(),
                        topic="lk-agent-metrics",
                    )
                )
            except Exception as err:
                logger.debug(f"Could not publish TTS metrics: {err}")
        elif isinstance(m, EOUMetrics):
            eou_ms = round(m.end_of_utterance_delay * 1000) if getattr(m, "end_of_utterance_delay", None) else None
            transcription_ms = round(m.transcription_delay * 1000) if getattr(m, "transcription_delay", None) else None
            logger.info(f"⏱️ [EOU Metric] Turn delay: {eou_ms}ms | STT delay: {transcription_ms}ms")
            try:
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps({
                            "type": "turn_metrics",
                            "e2e_ms": eou_ms,
                            "stt_ms": transcription_ms,
                        }).encode(),
                        topic="lk-agent-metrics",
                    )
                )
            except Exception as err:
                logger.debug(f"Could not publish EOU metrics: {err}")
        elif isinstance(m, STTMetrics):
            logger.info(f"🎤 [STT Metric] Duration: {round(getattr(m, 'duration', 0) * 1000)}ms")

    # 1. Connect to the room FIRST
    await ctx.connect()
    logger.info(f"Agent successfully joined room '{ctx.room.name}' (identity: {ctx.room.local_participant.identity})!")

    # Listen for control commands from the frontend (e.g. manual "Stop Speaking" button)
    @ctx.room.on("data_received")
    def on_data_received(data_packet: rtc.DataPacket):
        try:
            payload = json.loads(data_packet.data.decode("utf-8"))
            if payload.get("action") == "interrupt":
                logger.info("🛑 [Manual Interruption] User clicked stop speaking! Halting speech immediately...")
                # Interrupt current speech and audio queue (session.interrupt is synchronous returning Future)
                try:
                    session.interrupt(force=True)
                except Exception as int_err:
                    logger.warning(f"session.interrupt call warning: {int_err}")

                # Notify frontend to clear streaming text
                now_ts = int(time.time() * 1000)
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps({"type": "stream_end", "interrupted": True, "timestamp": now_ts}).encode(),
                        topic="lk-agent-stream",
                    )
                )
            elif payload.get("action") == "get_noise_cancellation":
                now_ts = int(time.time() * 1000)
                asyncio.create_task(
                    ctx.room.local_participant.publish_data(
                        json.dumps({
                            "type": "noise_cancellation_status",
                            "backend_enabled": nc_filter is not None,
                            "backend_model": nc_status_str,
                            "timestamp": now_ts,
                        }).encode(),
                        topic="lk-agent-stream",
                    )
                )
        except Exception as err:
            logger.warning(f"Error handling room data packet: {err}")

    # Deterministic single-agent leader election:
    # If multiple agent dispatch jobs were triggered for the same room, ensure ONLY ONE
    # agent session survives and speaks. All other duplicates cleanly disconnect.
    await asyncio.sleep(0.4)
    my_p = ctx.room.local_participant
    my_joined = getattr(my_p, "joined_at", 0) or 0
    if hasattr(my_joined, "timestamp"):
        my_joined = my_joined.timestamp()

    other_agents = []
    for p in ctx.room.remote_participants.values():
        if p.identity != my_p.identity and (
            getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            or int(getattr(p, "kind", 0)) == 4
            or p.identity.lower().startswith("agent")
            or "agent" in p.identity.lower()
        ):
            p_joined = getattr(p, "joined_at", 0) or 0
            if hasattr(p_joined, "timestamp"):
                p_joined = p_joined.timestamp()
            other_agents.append((p_joined, p.identity))

    if other_agents:
        all_agents = sorted([(my_joined, my_p.identity)] + other_agents)
        leader_identity = all_agents[0][1]
        if my_p.identity != leader_identity:
            logger.warning(
                f"⚠️ Duplicate agent detected in room '{ctx.room.name}'. "
                f"Elected leader is '{leader_identity}'. Disconnecting duplicate agent '{my_p.identity}'."
            )
            await ctx.room.disconnect()
            return
        else:
            logger.info(f"👑 Multiple agents detected; elected active room leader is '{my_p.identity}'. Serving room.")

    # 2. Check for calibrated voice profile matching participants
    for p in ctx.room.remote_participants.values():
        if getattr(p, "kind", None) != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            p_profile = voice_profile_manager.get_voice_profile(p.identity) or voice_profile_manager.get_voice_profile(getattr(p, "name", ""))
            if p_profile:
                active_profile = p_profile
                break

    agent_instructions = None
    greeting_text = "Hello! I am ready. How can I help you today?"

    if active_profile:
        p_name = active_profile.get("participant_name", "there")
        accent = active_profile.get("accent", "Natural English")
        tone = active_profile.get("tone", "Warm and conversational")
        mirror_prompt = active_profile.get("mirror_prompt", "")
        logger.info(f"🎙️ Activating Calibrated Profile for '{p_name}': Accent='{accent}', Tone='{tone}'")

        agent_instructions = (
            "You are an engaging, articulate, and friendly AI voice assistant with a natural Indian conversational style. "
            f"The user has calibrated their voice and tone. Mirror their tone, accent style, pacing, and conversational habits: {mirror_prompt}. "
            "You converse with the user in real time over WebRTC voice audio and text chat. "
            "You effortlessly understand Indian English, Indian accents, and common Hindi/Hinglish phrasing. "
            "Keep your answers concise (1 to 2 sentences maximum) unless asked for more detail. "
            "Speak naturally and warmly as if talking on a phone call. "
            "Do not use markdown formatting, bullet points, asterisks, or code blocks in your output."
        )
        greeting_text = active_profile.get("preview_greeting") or f"Hey {p_name}! I have tuned into your voice, tone, and accent. Let's chat!"

    # Configure backend server-side neural noise cancellation
    nc_filter = None
    nc_status_str = "Disabled"
    if ENABLE_NOISE_CANCELLATION and NOISE_CANCELLATION_AVAILABLE:
        try:
            if NOISE_CANCELLATION_MODEL == "NC":
                nc_filter = noise_cancellation.NC()
                nc_status_str = "NC (Neural Noise Cancellation)"
                logger.info("🛡️ [Noise Cancellation] Enabled LiveKit NC (Neural Noise Cancellation) filter")
            else:
                nc_filter = noise_cancellation.BVC()
                nc_status_str = "BVC (Background Voice Cancellation)"
                logger.info("🛡️ [Noise Cancellation] Enabled LiveKit BVC (Background Voice Cancellation) filter")
        except Exception as nc_err:
            logger.warning(f"Could not initialize {NOISE_CANCELLATION_MODEL} noise cancellation: {nc_err}")
            try:
                nc_filter = noise_cancellation.NC()
                nc_status_str = "NC (Fallback Filter)"
                logger.info("🛡️ [Noise Cancellation] Fallback to LiveKit NC filter")
            except Exception as nc_err2:
                logger.warning(f"Failed to initialize fallback NC filter: {nc_err2}")
                nc_filter = None
                nc_status_str = "Unavailable"
    else:
        logger.info("ℹ️ [Noise Cancellation] Backend noise cancellation disabled by config")

    audio_input_opts = room_io.AudioInputOptions(
        noise_cancellation=nc_filter,
    ) if nc_filter is not None else True

    # Start session — RoomIO handles audio and lk.chat text stream automatically
    await session.start(
        agent=VoiceAssistant(ctx, instructions=agent_instructions),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            text_input=True,
            close_on_disconnect=False,
            audio_input=audio_input_opts,
        ),
    )

    # Allow client data channels to complete WebRTC handshake before publishing initial events
    await asyncio.sleep(0.5)

    # Announce pipeline and active profile to frontend
    try:
        await ctx.room.local_participant.publish_data(
            json.dumps({
                "type": "pipeline_info",
                "pipeline": "indian-voice",
                "model": "Indian Neural Voice Assistant",
                "noise_cancellation": {
                    "backend_enabled": nc_filter is not None,
                    "backend_model": nc_status_str,
                },
                "timestamp": int(time.time() * 1000),
            }).encode(),
            topic="lk-agent-stream",
        )
        if active_profile:
            await ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "cloned_profile_active",
                    "accent": active_profile.get("accent", "Indian English"),
                    "tone": active_profile.get("tone", "Warm and friendly"),
                    "pacing": active_profile.get("pacing", "Natural"),
                    "vocabulary_style": active_profile.get("vocabulary_style", "Conversational"),
                    "participant_name": active_profile.get("participant_name", "there"),
                }).encode(),
                topic="lk-cloned-profile",
            )
    except Exception as e:
        logger.debug(f"Could not broadcast session info: {e}")

    # 3. Greet the user with spoken voice + text transcript
    greet_id = f"greet-{uuid.uuid4().hex[:8]}"
    now_ts = int(time.time() * 1000)
    try:
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "stream_start", "stream_id": greet_id, "timestamp": now_ts}).encode(),
            topic="lk-agent-stream",
        )
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "stream_chunk", "stream_id": greet_id, "delta": greeting_text, "timestamp": now_ts}).encode(),
            topic="lk-agent-stream",
        )
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "stream_end", "stream_id": greet_id, "timestamp": now_ts}).encode(),
            topic="lk-agent-stream",
        )
    except Exception as e:
        logger.debug(f"Could not publish greeting text to chat: {e}")

    await session.say(greeting_text)


if __name__ == "__main__":
    cli.run_app(server)
