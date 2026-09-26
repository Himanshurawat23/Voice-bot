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
from livekit.agents.metrics import LLMMetrics, TTSMetrics, STTMetrics, EOUMetrics

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-2-general")
STT_LANGUAGE = os.getenv("DEEPGRAM_STT_LANGUAGE", "en-IN")
TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "en-IN-PrabhatNeural")
LLM_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.7"))


from typing import AsyncIterable, Union, Optional
import voice_profile_manager


class VoiceAssistant(Agent):
    def __init__(self, ctx: JobContext, instructions: Optional[str] = None) -> None:
        default_instructions = (
            "You are an engaging, articulate, and friendly AI voice assistant with a natural Indian conversational style. "
            "You converse with the user in real time over WebRTC voice audio and text chat. "
            "You effortlessly understand Indian English, Indian accents, and common Hindi/Hinglish phrasing. "
            "Keep your answers concise (1 to 2 sentences maximum) unless asked for more detail. "
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
        """Override llm_node to stream LLM chunks to the frontend chat in real time."""
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

    # Noise-resilient VAD: higher activation threshold and min speech duration
    # filter out breathing, keyboard clicks, and background room noise
    vad = silero.VAD.load(
        activation_threshold=0.6,    # Default 0.5; 0.6 requires clearer speech and ignores room noise
        min_speech_duration=0.1,     # Ignore short clicks/taps under 100ms
        min_silence_duration=0.45,   # Clean end-of-turn detection
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
    )
    from edge_tts_adapter import EdgeTTS

    active_profile = voice_profile_manager.get_voice_profile("")
    selected_tts_model = active_profile.get("recommended_voice", TTS_MODEL) if active_profile else TTS_MODEL
    logger.info(f"Selected TTS voice model: {selected_tts_model}")
    if selected_tts_model.startswith("en-IN-") or selected_tts_model.startswith("hi-IN-") or "Neural" in selected_tts_model:
        tts = EdgeTTS(voice=selected_tts_model)
    else:
        tts = deepgram.TTS(model=selected_tts_model, api_key=DEEPGRAM_API_KEY)

    # AgentSession with noise & false-interruption guards:
    # Requires user to speak for at least 500ms or 2 words before interrupting the bot,
    # and automatically resumes speaking if a momentary sound cuts in with no actual speech.
    session = AgentSession(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
        turn_handling=agent_session.TurnHandlingOptions(
            interruption=agent_session.InterruptionOptions(
                min_duration=0.5,
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

    # Start session — RoomIO handles audio and lk.chat text stream automatically
    await session.start(
        agent=VoiceAssistant(ctx, instructions=agent_instructions),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            text_input=True,
            close_on_disconnect=False,
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
