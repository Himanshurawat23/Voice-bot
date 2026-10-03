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


def transcribe_audio_with_words(wav_bytes: bytes) -> tuple[str, list]:
    """
    Transcribes the user audio using Deepgram Nova-2 STT, returning both
    the full transcript and word-level timestamps.
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
        alt = data["results"]["channels"][0]["alternatives"][0]
        transcript = alt.get("transcript", "").strip()
        words = alt.get("words", [])
        return transcript, words
    except (KeyError, IndexError):
        return "", []


def transcribe_audio(wav_bytes: bytes) -> str:
    """Convenience wrapper returning just the transcript."""
    transcript, _ = transcribe_audio_with_words(wav_bytes)
    return transcript


def extract_aligned_reference_clip(
    wav_24k_bytes: bytes,
    words: list,
    full_transcript: str,
) -> tuple[bytes, str]:
    """
    Extracts an optimal reference audio segment (between 3.5s and 6.8s) and the exact
    matching reference text for F5-TTS voice cloning.
    F5-TTS requires that reference text and reference audio match 100%.
    Any mismatch causes severe mumbling, slurring, or unintelligible speech.
    """
    if not words or len(words) == 0:
        return wav_24k_bytes, (full_transcript or "Hello, I am calibrating my voice.").strip()

    total_duration = words[-1].get("end", 0.0)
    # If the recording is already short and optimal (<= 6.5s), keep the full clip and text
    if total_duration <= 6.5:
        ref_text = " ".join(w.get("punctuated_word", w.get("word", "")) for w in words).strip()
        return wav_24k_bytes, ref_text or full_transcript

    # Find the best sentence boundary (. ! ?) between 3.5s and 6.8s
    cand_idx = None
    for i, w in enumerate(words):
        p_word = w.get("punctuated_word", w.get("word", ""))
        end_t = w.get("end", 0.0)
        if 3.5 <= end_t <= 6.8 and any(p_word.endswith(p) for p in (".", "!", "?")):
            cand_idx = i
            break

    # If no sentence boundary in range, search for comma or inter-word pause (>= 200ms)
    if cand_idx is None:
        for i, w in enumerate(words):
            p_word = w.get("punctuated_word", w.get("word", ""))
            end_t = w.get("end", 0.0)
            is_comma = p_word.endswith(",") or p_word.endswith(";")
            has_pause = (i + 1 < len(words) and (words[i + 1].get("start", end_t) - end_t >= 0.2))
            if 3.5 <= end_t <= 6.8 and (is_comma or has_pause):
                cand_idx = i
                break

    # If still none found, pick the word closest to 5.2s
    if cand_idx is None:
        cand_idx = min(range(len(words)), key=lambda i: abs(words[i].get("end", 0.0) - 5.2))

    chosen_word = words[cand_idx]
    cut_time = min(chosen_word.get("end", 5.0) + 0.12, total_duration)
    ref_text = " ".join(w.get("punctuated_word", w.get("word", "")) for w in words[:cand_idx + 1]).strip()

    # Slice the 24kHz audio bytes up to cut_time
    # Primary: Python standard library 'wave' (zero dependencies, 100% reliable in all environments)
    try:
        import wave
        in_buf = io.BytesIO(wav_24k_bytes)
        with wave.open(in_buf, "rb") as r:
            nchannels = r.getnchannels()
            sampwidth = r.getsampwidth()
            framerate = r.getframerate()
            num_frames = min(r.getnframes(), int(cut_time * framerate))
            frames = r.readframes(num_frames)

        out_buf = io.BytesIO()
        with wave.open(out_buf, "wb") as w:
            w.setnchannels(nchannels)
            w.setsampwidth(sampwidth)
            w.setframerate(framerate)
            w.writeframes(frames)

        sliced_wav_bytes = out_buf.getvalue()
        logger.info(f"✂️ Aligned reference clip: {cut_time:.2f}s ({cand_idx+1} words) | '{ref_text}'")
        return sliced_wav_bytes, ref_text
    except Exception as wave_err:
        logger.debug(f"Standard wave slice exception ({wave_err}), trying soundfile fallback...")
        try:
            import soundfile as sf
            in_buf = io.BytesIO(wav_24k_bytes)
            audio_data, sr = sf.read(in_buf)
            max_samples = int(cut_time * sr)
            cut_audio = audio_data[:max_samples]
            out_buf = io.BytesIO()
            sf.write(out_buf, cut_audio, sr, format="wav")
            sliced_wav_bytes = out_buf.getvalue()
            logger.info(f"✂️ Aligned reference clip (soundfile): {cut_time:.2f}s ({cand_idx+1} words) | '{ref_text}'")
            return sliced_wav_bytes, ref_text
        except Exception as sf_err:
            logger.warning(f"Failed to slice audio for reference clip: {sf_err}")
            return wav_24k_bytes, full_transcript


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
    elif "lux" in preferred_accent.lower():
        default_voice = "luxtts"
    elif "clone" in preferred_accent.lower() or "f5" in preferred_accent.lower():
        default_voice = "luxtts"
    elif "indian" in preferred_accent.lower():
        default_voice = "luxtts"
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
            "preview_greeting": f"Namaste {participant_name}! I have tuned into your voice, accent, and style. How can I help you today?",
            "recommended_voice": default_voice,
        }


def synthesize_preview_speech(
    text: str,
    voice_model: str,
    ref_audio_path: Optional[str] = None,
    ref_text: Optional[str] = None,
) -> bytes:
    """
    Synthesizes preview greeting audio for the web calibration modal.
    Prioritizes ultra-fast, zero-RAM cloud EdgeTTS (Indian English/Hindi)
    so calibration returns within 1-2 seconds without server hangs or memory exhaustion.
    """
    import asyncio

    # Determine matching EdgeTTS voice for authentic Indian English/Hindi preview
    edge_voice = "en-IN-PrabhatNeural"
    if voice_model.startswith("en-IN-") or voice_model.startswith("hi-IN-"):
        edge_voice = voice_model
    elif "female" in voice_model.lower() or "neerja" in voice_model.lower():
        edge_voice = "en-IN-NeerjaNeural"

    # 1. Primary for fast web preview: EdgeTTS (runs in ~300ms, 0 MB server RAM, native MP3)
    # This prevents Render's 512MB RAM limit from being exceeded by 400MB PyTorch models during calibration
    try:
        logger.info(f"Synthesizing preview greeting via EdgeTTS ({edge_voice})...")
        edge_mp3 = asyncio.run(_edge_tts_synth(text, edge_voice))
        if edge_mp3 and len(edge_mp3) > 1000:
            logger.info(f"✅ Instant preview greeting synthesized via EdgeTTS ({len(edge_mp3)} bytes)")
            return edge_mp3
    except Exception as edge_err:
        logger.warning(f"EdgeTTS preview note: {edge_err}, trying secondary options...")

    # 2. If F5-TTS cloned voice is requested and local MLX is available (Apple Silicon Mac)
    if voice_model == "f5-tts" or "f5" in voice_model.lower():
        try:
            from f5_tts_adapter import is_f5_tts_available, get_f5_model, load_ref_audio
            if is_f5_tts_available() and ref_audio_path and os.path.exists(ref_audio_path):
                import mlx.core as mx  # type: ignore
                from f5_tts_mlx.generate import estimated_duration, FRAMES_PER_SEC, convert_char_to_pinyin
                
                model = get_f5_model()
                audio = load_ref_audio(ref_audio_path, target_sr=24000)
                ref_t = ref_text or "Hello, I am calibrating my voice."
                dur_frames = int(estimated_duration(audio, ref_t, text, speed=1.0) * FRAMES_PER_SEC)
                formatted_text = convert_char_to_pinyin([ref_t + " " + text])
                
                wave, _ = model.sample(
                    mx.expand_dims(audio, axis=0),
                    text=formatted_text,
                    duration=dur_frames,
                    steps=8,
                    method="euler",
                    cfg_strength=2.0,
                )
                wave = wave[audio.shape[0]:]
                mx.eval(wave)
                
                np_wave = np.array(wave, dtype=np.float32)
                pcm16 = (np.clip(np_wave, -1.0, 1.0) * 32767).astype(np.int16)
                
                # Encode PCM to MP3 using PyAV for web playback
                out_buf = io.BytesIO()
                out_container = av.open(out_buf, mode="w", format="mp3")
                out_stream = out_container.add_stream("mp3", rate=24000)
                frame = av.AudioFrame.from_ndarray(pcm16.reshape(1, -1), format="s16", layout="mono")
                frame.sample_rate = 24000
                for packet in out_stream.encode(frame):
                    out_container.mux(packet)
                for packet in out_stream.encode(None):
                    out_container.mux(packet)
                out_container.close()
                return out_buf.getvalue()
        except Exception as e:
            logger.warning(f"F5-TTS preview synthesis note: {e}")

    # 3. Deepgram fallback (with encoding=mp3 for browser compatibility)
    if DEEPGRAM_API_KEY:
        try:
            dg_voice = voice_model if (voice_model and not voice_model.startswith("en-IN-") and voice_model not in ("pocket-tts", "f5-tts", "luxtts")) else "aura-2-asteria-en"
            url = f"https://api.deepgram.com/v1/speak?model={dg_voice}&encoding=mp3"
            headers = {
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "application/json",
            }
            response = requests.post(url, headers=headers, json={"text": text}, timeout=10)
            if response.status_code == 200 and len(response.content) > 500:
                logger.info(f"✅ Deepgram preview greeting synthesized ({len(response.content)} bytes)")
                return response.content
        except Exception as dg_err:
            logger.warning(f"Deepgram fallback preview note: {dg_err}")

    # 4. Final safety net: Return minimal valid MP3 frame so client never breaks
    logger.warning("Could not synthesize preview audio; proceeding with profile metadata.")
    return b""


def save_voice_profile(
    participant_name: str,
    profile_data: Dict[str, Any],
    wav_bytes: bytes,
    preview_bytes: bytes,
    wav_24k_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Saves the user's reference WAV (16kHz + 24kHz), preview MP3, and JSON metadata.
    """
    clean_name = "".join(c for c in participant_name if c.isalnum() or c in ("-", "_")).lower()
    if not clean_name:
        clean_name = "user-default"

    ref_wav_path = PROFILES_DIR / f"{clean_name}_ref.wav"
    ref_wav_24k_path = PROFILES_DIR / f"{clean_name}_ref_24k.wav"
    preview_mp3_path = PROFILES_DIR / f"{clean_name}_preview.mp3"
    profile_json_path = PROFILES_DIR / f"{clean_name}.json"

    # Write 16kHz reference WAV and preview MP3
    ref_wav_path.write_bytes(wav_bytes)
    preview_mp3_path.write_bytes(preview_bytes)

    # Write 24kHz reference WAV (for F5-TTS)
    if wav_24k_bytes:
        ref_wav_24k_path.write_bytes(wav_24k_bytes)
    else:
        ref_wav_24k_path.write_bytes(convert_audio_to_wav(wav_bytes, target_sample_rate=24000))

    full_profile = {
        **profile_data,
        "participant_name": participant_name,
        "clean_name": clean_name,
        "ref_wav_file": str(ref_wav_path),
        "ref_wav_24k_file": str(ref_wav_24k_path),
        "ref_text": profile_data.get("ref_text") or profile_data.get("transcript", ""),
        "preview_audio_url": f"/api/voice-clone/preview/{clean_name}",
        "created_at": int(time.time()),
    }

    with open(profile_json_path, "w", encoding="utf-8") as f:
        json.dump(full_profile, f, indent=2)

    logger.info(f"Saved calibrated voice profile for '{clean_name}' (F5-TTS reference ready)")
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
