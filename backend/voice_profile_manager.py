import os
import io
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import numpy as np
import requests
import av
from dotenv import load_dotenv

# Ensure .env is loaded
load_dotenv(override=True)

logger = logging.getLogger("voice-profile-manager")

PROFILES_DIR = Path(__file__).parent / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")


def convert_audio_to_wav(input_bytes: bytes, target_sample_rate: int = 16000) -> bytes:
    """
    Decodes any audio container (WebM/Opus, Ogg, MP3, WAV, etc.) using PyAV
    and re-encodes to standard 16kHz mono 16-bit PCM WAV.
    """
    input_container = av.open(io.BytesIO(input_bytes))
    in_stream = input_container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=target_sample_rate)

    out_buf = io.BytesIO()
    out_container = av.open(out_buf, mode="w", format="wav")
    out_stream = out_container.add_stream("pcm_s16le", rate=target_sample_rate)
    out_stream.layout = "mono"

    for frame in input_container.decode(in_stream):
        for resampled in resampler.resample(frame):
            for packet in out_stream.encode(resampled):
                out_container.mux(packet)

    for packet in out_stream.encode(None):
        out_container.mux(packet)

    out_container.close()
    return out_buf.getvalue()


def analyze_audio_acoustics(wav_bytes: bytes, sample_rate: int = 16000) -> Dict[str, Any]:
    """
    Analyzes raw WAV audio bytes to extract duration, RMS volume, and estimated pitch.
    """
    try:
        # Strip 44-byte WAV header
        raw_pcm = wav_bytes[44:] if len(wav_bytes) > 44 else wav_bytes
        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32)

        if len(samples) == 0:
            return {"duration": 0.0, "pitch_hz": 150.0, "gender_hint": "neutral", "energy": "medium"}

        duration = len(samples) / sample_rate
        rms = np.sqrt(np.mean(samples ** 2))

        # Autocorrelation pitch estimation on central segment
        pitch_hz = 150.0
        if len(samples) >= sample_rate * 0.5:
            start = len(samples) // 2
            segment = samples[start : min(start + sample_rate, len(samples))]
            segment -= np.mean(segment)
            corr = np.correlate(segment, segment, mode="full")
            corr = corr[len(corr) // 2 :]

            min_lag = int(sample_rate / 350)  # max ~350Hz
            max_lag = int(sample_rate / 75)   # min ~75Hz
            if max_lag < len(corr):
                peak_lag = min_lag + np.argmax(corr[min_lag:max_lag])
                if peak_lag > 0:
                    pitch_hz = round(float(sample_rate / peak_lag), 1)

        # Categorize gender / voice register
        # Typical adult male fundamental frequency: 85 - 165 Hz
        # Typical adult female fundamental frequency: 165 - 255 Hz
        gender_hint = "female" if pitch_hz > 165 else "male"
        energy = "high" if rms > 3000 else ("low" if rms < 1000 else "medium")

        return {
            "duration": round(duration, 2),
            "pitch_hz": pitch_hz,
            "gender_hint": gender_hint,
            "energy": energy,
        }
    except Exception as e:
        logger.warning(f"Acoustic analysis error: {e}")
        return {"duration": 5.0, "pitch_hz": 150.0, "gender_hint": "male", "energy": "medium"}


def transcribe_audio(wav_bytes: bytes) -> str:
    """
    Transcribes the user audio using Deepgram Nova-2 STT.
    """
    if not DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY is missing in backend/.env")

    url = "https://api.deepgram.com/v1/listen?model=nova-2-general&smart_format=true&punctuate=true"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/wav",
    }
    response = requests.post(url, headers=headers, data=wav_bytes, timeout=15)
    if response.status_code != 200:
        logger.error(f"Deepgram STT failed ({response.status_code}): {response.text}")
        raise RuntimeError(f"Deepgram STT error: {response.text}")

    data = response.json()
    try:
        transcript = (
            data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        )
        return transcript
    except (KeyError, IndexError):
        return ""


async def _edge_tts_synth(text: str, voice: str) -> bytes:
    """Synthesizes text using edge-tts for authentic regional voices like Indian English."""
    import edge_tts
    comm = edge_tts.Communicate(text, voice)
    chunks = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def generate_voice_profile(
    transcript: str,
    acoustics: Dict[str, Any],
    participant_name: str,
    preferred_accent: str = "Indian English",
    preferred_voice: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Uses Groq LLM to analyze linguistic patterns, accent nuances, tone,
    and pacing from the user's speech transcript and acoustic profile.
    """
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is missing in backend/.env")

    pitch_hz = acoustics.get("pitch_hz", 150)
    gender_hint = acoustics.get("gender_hint", "male")
    duration = acoustics.get("duration", 5.0)

    # Determine recommended voice based on user preference and gender register
    if preferred_voice:
        default_voice = preferred_voice
    elif "indian" in preferred_accent.lower():
        default_voice = "en-IN-PrabhatNeural" if gender_hint == "male" else "en-IN-NeerjaExpressiveNeural"
    elif gender_hint == "female":
        default_voice = "aura-2-asteria-en"
    else:
        default_voice = "aura-2-orion-en"

    system_prompt = (
        "You are an expert voice, dialect, and phonetics linguistic analyst. "
        "A user recorded a voice sample to calibrate an AI assistant to mirror their conversational style, "
        "accent, pacing, and tone. Analyze their speech thoroughly. "
        "If the user is an Indian English speaker, capture their natural Indian English conversational flow, "
        "syllable timing, friendly colloquial phrasing, and warmth without generic American corporate tropes."
    )

    user_prompt = f"""User Name: {participant_name}
Recorded Audio Duration: {duration} seconds
Acoustic Pitch: {pitch_hz} Hz ({gender_hint} register)
Target Accent: {preferred_accent}
Spoken Transcript: "{transcript}"

Analyze their accent, conversational tone, rhythm, and vocabulary style.
Notice that the user is an Indian speaker who wants an authentic Indian English conversational style.

Return ONLY a valid JSON object with these exact keys:
{{
  "accent": "{preferred_accent} (fluent, conversational)",
  "tone": "<2-4 adjectives describing tone and emotional warmth, e.g. 'Warm, friendly, approachable'>",
  "pacing": "<e.g. 'Brisk and expressive (135 WPM)', 'Moderate, rhythmic'>",
  "vocabulary_style": "<e.g. 'Indian English conversational, friendly and polite'>",
  "mirror_prompt": "<detailed instructions for an AI voice agent on how to talk like this user: speak in natural Indian English, use natural conversational transitions like 'got it', 'definitely', 'tell me', 'let us do one thing', maintain natural syllable-timed Indian cadence, and avoid sounding like an American corporate AI>",
  "preview_greeting": "<a short 1-sentence personalized greeting where the bot speaks in authentic Indian English tone & style>",
  "recommended_voice": "{default_voice}"
}}"""

    try:
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            },
            timeout=20,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Groq API error ({res.status_code}): {res.text}")

        content = res.json()["choices"][0]["message"]["content"].strip()
        # Clean potential markdown wrapping
        if "```" in content:
            parts = content.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{") and p.endswith("}"):
                    content = p
                    break

        profile = json.loads(content)
        profile["recommended_voice"] = default_voice
        return profile
    except Exception as e:
        logger.warning(f"Groq analysis fallback triggered: {e}")
        return {
            "accent": f"{preferred_accent} (fluent, natural)",
            "tone": "Warm, articulate, and friendly",
            "pacing": "Moderate and natural",
            "vocabulary_style": "Friendly Indian English conversational",
            "mirror_prompt": (
                "Speak in a natural, friendly Indian English accent. Use natural cadence and polite, "
                "conversational phrases like 'got it', 'sure thing', 'definitely', 'tell me'. "
                "Keep responses concise and lively, avoiding generic American AI phrasing."
            ),
            "preview_greeting": f"Namaste {participant_name}! I have tuned into your Indian English voice and tone. How can I help you today?",
            "recommended_voice": default_voice,
        }


def synthesize_preview_speech(text: str, voice_model: str) -> bytes:
    """
    Synthesizes preview audio using Edge-TTS (for authentic Indian English neural voices)
    or Deepgram TTS (for Aura models).
    """
    import asyncio

    # If it's an EdgeTTS regional voice (e.g. Indian English)
    if voice_model.startswith("en-IN-") or voice_model.startswith("hi-IN-") or "Neural" in voice_model:
        try:
            return asyncio.run(_edge_tts_synth(text, voice_model))
        except Exception as e:
            logger.error(f"EdgeTTS preview synthesis error: {e}", exc_info=True)
            # fallback to Deepgram
            voice_model = "aura-2-orion-en"

    # Otherwise Deepgram
    if not DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY is missing in backend/.env")

    url = f"https://api.deepgram.com/v1/speak?model={voice_model}"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json={"text": text}, timeout=15)
    if response.status_code != 200:
        logger.error(f"Deepgram TTS preview failed ({response.status_code}): {response.text}")
        fallback_voice = "aura-2-asteria-en"
        if voice_model != fallback_voice:
            return synthesize_preview_speech(text, fallback_voice)
        raise RuntimeError(f"Deepgram TTS error: {response.text}")

    return response.content


def save_voice_profile(
    participant_name: str,
    profile_data: Dict[str, Any],
    wav_bytes: bytes,
    preview_bytes: bytes,
) -> Dict[str, Any]:
    """
    Saves the user's reference WAV, preview MP3, and JSON metadata.
    """
    clean_name = "".join(c for c in participant_name if c.isalnum() or c in ("-", "_")).lower()
    if not clean_name:
        clean_name = "user-default"

    ref_wav_path = PROFILES_DIR / f"{clean_name}_ref.wav"
    preview_mp3_path = PROFILES_DIR / f"{clean_name}_preview.mp3"
    profile_json_path = PROFILES_DIR / f"{clean_name}.json"

    # Write files
    ref_wav_path.write_bytes(wav_bytes)
    preview_mp3_path.write_bytes(preview_bytes)

    full_profile = {
        **profile_data,
        "participant_name": participant_name,
        "clean_name": clean_name,
        "ref_wav_file": str(ref_wav_path),
        "preview_audio_url": f"/api/voice-clone/preview/{clean_name}",
        "created_at": int(time.time()),
    }

    with open(profile_json_path, "w", encoding="utf-8") as f:
        json.dump(full_profile, f, indent=2)

    logger.info(f"Saved calibrated voice profile for '{clean_name}'")
    return full_profile


def get_voice_profile(participant_name: str) -> Optional[Dict[str, Any]]:
    """
    Loads saved voice profile by participant name.
    """
    clean_name = "".join(c for c in participant_name if c.isalnum() or c in ("-", "_")).lower()
    json_path = PROFILES_DIR / f"{clean_name}.json"
    if not json_path.exists():
        # Check if there is a generic recent profile
        profiles = list(PROFILES_DIR.glob("*.json"))
        if profiles:
            # Sort by modification time descending
            profiles.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            try:
                with open(profiles[0], "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading profile for '{clean_name}': {e}")
        return None
