# Indian Neural Voice Bot 🎙️

A real-time, low-latency conversational AI voice agent built with LiveKit WebRTC, Groq LLMs (Qwen/Llama 3), Deepgram STT, and neural TTS (PocketTTS CPU cloning & EdgeTTS Indian English/Hindi models).

---

## 🛡️ Dual-Layer Noise Cancellation Architecture

Noise cancellation operates across **both the Frontend (client-side) and Backend (agent-side)** to ensure crystal-clear speech recognition and turn detection:

### 1. Frontend (Browser / Client)
* **Krisp AI Neural Filter**: Integrated using `@livekit/krisp-noise-filter` and `useKrispNoiseFilter()` from `@livekit/components-react/krisp`.
* **One-Click UI Toggle**: Dedicated Shield button in the control dock to enable/disable AI noise filtering dynamically with visual glowing state indicators.
* **WebRTC Hardware & DSP Isolation**: Built-in browser-level audio constraints in `LiveKitRoom`:
  * `echoCancellation: true`
  * `noiseSuppression: true`
  * `voiceIsolation: true`
  * `googHighpassFilter: true` (removes mic pops, breathing, low-frequency hums)
  * `googTypingNoiseDetection: true` (suppresses keyboard clatter)

### 2. Backend (LiveKit Agent Worker)
* **Neural Audio Pre-Processor**: Powered by the official `livekit-plugins-noise-cancellation` package.
* **Models Supported**:
  * `BVC` (Background Voice Cancellation) – eliminates background chatter and non-primary speaker voices to prevent STT hallucinations.
  * `NC` (Enhanced Neural Noise Cancellation) – suppresses ambient environmental noise (fans, traffic, street sounds).
* **Configuration** in `backend/.env`:
  ```env
  LIVEKIT_NOISE_CANCELLATION=true
  LIVEKIT_NOISE_CANCELLATION_MODEL=BVC  # BVC or NC
  ```
* **Real-time Status Sync**: Agent communicates noise filter status to the UI via LiveKit data channels (`lk-agent-stream`), showing active status on the UI dashboard.

---

## 🚀 Quick Start

### 1. Start Token Server
```bash
cd backend
.venv/bin/python server.py
```

### 2. Start Voice Agent Worker
```bash
cd backend
.venv/bin/python agent.py start
```

### 3. Start Frontend UI
```bash
cd frontend
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) in your browser.
