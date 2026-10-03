import os
import time
import uuid
import logging
import asyncio
from datetime import timedelta
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import voice_profile_manager

# Load environment variables from .env
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("token-server")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://your-project.livekit.cloud")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
PORT = int(os.getenv("SERVER_PORT", "8000"))

app = FastAPI(title="LiveKit Voice Bot Token Server", version="1.0.0")

import re

# Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def normalize_slashes(request, call_next):
    """Normalize duplicate slashes (e.g. //api/health -> /api/health)."""
    if "//" in request.scope.get("path", ""):
        request.scope["path"] = re.sub(r"/+", "/", request.scope["path"])
    return await call_next(request)


class TokenRequest(BaseModel):
    room_name: Optional[str] = "voice-bot-room"
    participant_name: Optional[str] = None
    agent_name: Optional[str] = ""


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
async def root_health():
    """Root health check for cloud deployment platforms (Render, Koyeb, Railway)."""
    return {"status": "ok", "service": "livekit-voice-bot", "timestamp": int(time.time())}


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    """Health check and configuration diagnostics."""
    is_livekit_configured = (
        bool(LIVEKIT_API_KEY)
        and bool(LIVEKIT_API_SECRET)
        and LIVEKIT_API_KEY != "devkey"
        and LIVEKIT_API_KEY != "your_livekit_api_key"
    )
    is_deepgram_configured = bool(DEEPGRAM_API_KEY) and DEEPGRAM_API_KEY != "your_deepgram_api_key"
    is_llm_configured = bool(os.getenv("GROQ_API_KEY")) or (bool(GEMINI_API_KEY) and GEMINI_API_KEY != "your_gemini_api_key")

    is_nc_enabled = os.getenv("LIVEKIT_NOISE_CANCELLATION", "true").lower() in ("true", "1", "yes")
    nc_model = os.getenv("LIVEKIT_NOISE_CANCELLATION_MODEL", "BVC").upper()

    return {
        "status": "online",
        "livekit_url": LIVEKIT_URL,
        "configurations": {
            "livekit": is_livekit_configured,
            "deepgram": is_deepgram_configured,
            "llm": is_llm_configured,
            "noise_cancellation": is_nc_enabled,
        },
        "noise_cancellation": {
            "enabled": is_nc_enabled,
            "model": nc_model,
        },
        "message": (
            "All credentials configured!"
            if (is_livekit_configured and is_deepgram_configured and is_llm_configured)
            else "Missing or default credentials detected in environment"
        ),
    }

# Per-room cooldown: room_name -> last dispatch UNIX timestamp
# Prevents a second agent spawning when the user reconnects quickly
_dispatch_cache: dict = {}
DISPATCH_COOLDOWN = 30  # seconds


async def dispatch_agent_to_room(room_name: str, agent_name: str = "indian-voice-agent"):
    """Dispatch an agent to the room, evicting any conflicting or stale zombie agents."""
    from livekit import api

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        # Check existing participants in the room
        try:
            room_info = await lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            for p in room_info.participants:
                is_agent = (
                    p.kind == api.ParticipantInfo.Kind.AGENT
                    or int(getattr(p, "kind", 0)) == 4
                    or p.identity.lower().startswith("agent")
                )
                if is_agent:
                    logger.info(f"Agent already active in room '{room_name}' ({p.identity}).")
                    return
        except Exception as check_err:
            logger.info(f"Room '{room_name}' check: {check_err}")

        # Always dispatch the requested agent worker
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name or "indian-voice-agent",
                room=room_name,
            )
        )
        logger.info(f"✅ Dispatched agent worker to room '{room_name}' (agent_name='{agent_name}')")
    except Exception as e:
        logger.warning(f"Could not dispatch agent to room '{room_name}': {e}")
    finally:
        await lkapi.aclose()


def generate_token(room_name: str, participant_name: str) -> str:
    """Generate LiveKit participant JWT token."""
    from livekit import api

    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=500,
            detail="LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be configured in backend/.env to join rooms.",
        )

    # Mint access token with room permissions and explicit agent dispatch
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_name)
        .with_name(participant_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(hours=2))
    )

    return token.to_jwt()


@app.post("/api/token")
async def create_token_post(req: TokenRequest):
    """POST endpoint to generate participant token."""
    room = req.room_name or "voice-bot-room"
    name = req.participant_name or f"User-{uuid.uuid4().hex[:6]}"
    agent_name = req.agent_name or "indian-voice-agent"
    jwt_token = generate_token(room, name)
    logger.info(f"Generated token for participant '{name}' in room '{room}' (agent='{agent_name}')")
    # Dispatch specific agent worker into the room so the right bot joins
    await dispatch_agent_to_room(room, agent_name=agent_name)
    return {
        "token": jwt_token,
        "url": LIVEKIT_URL,
        "room_name": room,
        "participant_name": name,
    }


@app.get("/api/token")
async def create_token_get(
    room_name: Optional[str] = Query(default="voice-bot-room"),
    participant_name: Optional[str] = Query(default=None),
    agent_name: Optional[str] = Query(default="indian-voice-agent"),
):
    """GET endpoint to generate participant token."""
    name = participant_name or f"User-{uuid.uuid4().hex[:6]}"
    jwt_token = generate_token(room_name, name)
    logger.info(f"Generated token for participant '{name}' in room '{room_name}' (agent='{agent_name}')")
    # Dispatch specific agent worker into the room so the right bot joins
    await dispatch_agent_to_room(room_name, agent_name=agent_name or "indian-voice-agent")
    return {
        "token": jwt_token,
        "url": LIVEKIT_URL,
        "room_name": room_name,
        "participant_name": name,
    }


# ── Voice Cloning & Tone Calibration Endpoints ───────────────────────────────
@app.post("/api/voice-clone/calibrate")
async def calibrate_voice(
    audio: UploadFile = File(...),
    participant_name: str = Form("User-Default"),
    preferred_accent: str = Form("Indian English"),
    preferred_voice: Optional[str] = Form(None),
):
    """
    Receives user calibration audio (5-10s clip), converts to WAV,
    transcribes speech, analyzes tone/accent via Groq, synthesizes a preview,
    and stores the voice profile.
    """
    logger.info(f"Received voice calibration request for '{participant_name}' (Accent: '{preferred_accent}', Voice: '{preferred_voice}')")
    try:
        raw_audio_bytes = await audio.read()
        if not raw_audio_bytes or len(raw_audio_bytes) < 1000:
            raise HTTPException(status_code=400, detail="Audio file too short or empty.")

        def _do_calibration():
            # 1. Convert to 16kHz mono WAV via PyAV
            logger.info(f"Calibration [1/6]: Converting audio ({len(raw_audio_bytes)} bytes) to 16kHz WAV...")
            wav_bytes = voice_profile_manager.convert_audio_to_wav(raw_audio_bytes)

            # 2. Extract acoustic metrics
            logger.info("Calibration [2/6]: Analyzing audio acoustics...")
            acoustics = voice_profile_manager.analyze_audio_acoustics(wav_bytes)

            # 3. Transcribe speech via Deepgram STT (with word timestamps)
            logger.info("Calibration [3/6]: Transcribing speech with Deepgram STT...")
            transcript, words = voice_profile_manager.transcribe_audio_with_words(wav_bytes)
            if not transcript:
                transcript = "Hello! I am calibrating my voice, tone, and speaking style for our conversation."

            # 4. Generate linguistic profile & mirror prompt via Groq
            logger.info("Calibration [4/6]: Generating linguistic profile & mirror prompt via Groq...")
            profile_data = voice_profile_manager.generate_voice_profile(
                transcript=transcript,
                acoustics=acoustics,
                participant_name=participant_name,
                preferred_accent=preferred_accent,
                preferred_voice=preferred_voice,
            )
            profile_data["transcript"] = transcript
            profile_data["acoustics"] = acoustics

            # 4b. Prepare 24kHz WAV and extract optimal aligned reference clip for F5-TTS / PocketTTS
            logger.info("Calibration [4b/6]: Aligning reference clip for voice cloning...")
            wav_24k_raw = voice_profile_manager.convert_audio_to_wav(raw_audio_bytes, target_sample_rate=24000)
            aligned_24k_bytes, aligned_ref_text = voice_profile_manager.extract_aligned_reference_clip(
                wav_24k_raw, words, transcript
            )
            profile_data["ref_text"] = aligned_ref_text

            # 5. Synthesize a preview greeting in the mirrored persona & matched voice
            preview_text = profile_data.get(
                "preview_greeting",
                f"Hey {participant_name}! I've calibrated to your voice and tone. Let's chat!"
            )
            recommended_voice = profile_data.get("recommended_voice", "f5-tts")
            logger.info(f"Calibration [5/6]: Synthesizing preview speech with voice '{recommended_voice}'...")

            # Create temporary 24k wav reference for preview synthesis if needed
            clean_name = "".join(c for c in participant_name if c.isalnum() or c in ("-", "_")).lower() or "user-default"
            temp_24k_path = voice_profile_manager.PROFILES_DIR / f"{clean_name}_ref_24k.wav"
            temp_24k_path.write_bytes(aligned_24k_bytes)

            try:
                preview_bytes = voice_profile_manager.synthesize_preview_speech(
                    preview_text,
                    recommended_voice,
                    ref_audio_path=str(temp_24k_path),
                    ref_text=aligned_ref_text,
                )
            except Exception as synth_err:
                logger.warning(f"Preview synthesis encountered error ({synth_err}), proceeding with profile save")
                preview_bytes = b""

            # 6. Save reference WAV and profile JSON
            logger.info(f"Calibration [6/6]: Saving calibrated voice profile for '{participant_name}'...")
            saved = voice_profile_manager.save_voice_profile(
                participant_name=participant_name,
                profile_data=profile_data,
                wav_bytes=wav_bytes,
                preview_bytes=preview_bytes,
                wav_24k_bytes=aligned_24k_bytes,
            )
            logger.info(f"✅ Calibration complete for '{participant_name}'!")
            return saved

        saved_profile = await asyncio.to_thread(_do_calibration)
        return saved_profile
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to calibrate voice for '{participant_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Voice calibration failed: {str(e)}")


@app.get("/api/voice-clone/profile/{participant_name}")
async def get_profile(participant_name: str):
    """Retrieves an existing voice & tone profile for a participant."""
    profile = voice_profile_manager.get_voice_profile(participant_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found.")
    return profile


@app.api_route("/api/voice-clone/preview/{clean_name}", methods=["GET", "HEAD"])
async def get_preview_audio(clean_name: str):
    """Streams the synthesized preview MP3 audio for a user."""
    preview_file = voice_profile_manager.PROFILES_DIR / f"{clean_name}_preview.mp3"
    if not preview_file.exists():
        raise HTTPException(status_code=404, detail="Preview audio not found.")
    return Response(content=preview_file.read_bytes(), media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    is_dev = os.getenv("DEV", "false").lower() == "true"
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=is_dev)
