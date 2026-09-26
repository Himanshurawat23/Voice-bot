import React, { useState, useRef, useEffect } from 'react';
import {
  Mic,
  Square,
  Play,
  Pause,
  RotateCcw,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  X,
  Radio,
  ArrowRight,
  Headphones,
  Sliders,
  Globe,
} from '../icons';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

const ACCENT_VOICE_OPTIONS = [
  {
    id: 'in-male',
    label: '🇮🇳 Indian English (Male - Prabhat)',
    accent: 'Indian English',
    voice: 'en-IN-PrabhatNeural',
    description: 'Natural Indian English cadence, warm & authentic conversational rhythm',
  },
  {
    id: 'in-female',
    label: '🇮🇳 Indian English (Female - Neerja)',
    accent: 'Indian English',
    voice: 'en-IN-NeerjaExpressiveNeural',
    description: 'Expressive Indian English, articulate and friendly conversational tone',
  },
  {
    id: 'us-male',
    label: '🇺🇸 American English (Male - Orion)',
    accent: 'American English',
    voice: 'aura-2-orion-en',
    description: 'Standard American English male persona',
  },
  {
    id: 'us-female',
    label: '🇺🇸 American English (Female - Asteria)',
    accent: 'American English',
    voice: 'aura-2-asteria-en',
    description: 'Standard American English female persona',
  },
];

const SAMPLE_SCRIPTS = [
  {
    title: 'Indian English Conversational (Recommended)',
    text: "Namaste! I am recording my voice so this assistant learns my natural Indian English accent. Notice my pacing, cadence, and how I naturally converse. Let's make sure it sounds like me!",
  },
  {
    title: 'Casual Tech & Everyday',
    text: "Hey there! Let's get this calibrated. Just speaking naturally so the bot picks up my tone, rhythm, and everyday Indian English phrasing. Looking forward to our conversation!",
  },
  {
    title: 'Crisp & Articulate',
    text: "Hello! Testing out this voice calibration. I want the bot to understand my pronunciation, conversational speed, and friendly tone without sounding like a robotic American AI.",
  },
];

export function VoiceCalibrationModal({ isOpen, onClose, participantName, onCalibrationComplete }) {
  const [selectedVoiceOpt, setSelectedVoiceOpt] = useState(ACCENT_VOICE_OPTIONS[0]);
  const [selectedScriptIdx, setSelectedScriptIdx] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [audioBlob, setAudioBlob] = useState(null);
  const [audioUrl, setAudioUrl] = useState(null);
  const [isPlayingRecorded, setIsPlayingRecorded] = useState(false);
  const [volumeLevel, setVolumeLevel] = useState(0);

  // Calibration API States
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisStep, setAnalysisStep] = useState(0);
  const [calibratedProfile, setCalibratedProfile] = useState(null);
  const [isPlayingPreview, setIsPlayingPreview] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // Refs for media recording & audio playback
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const timerIntervalRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const animFrameRef = useRef(null);
  const recordedAudioPlayerRef = useRef(null);
  const previewAudioPlayerRef = useRef(null);

  // Reset states when opening
  useEffect(() => {
    if (isOpen) {
      setErrorMessage('');
      setAudioBlob(null);
      setAudioUrl(null);
      setRecordingSeconds(0);
      setIsRecording(false);
    } else {
      cleanupAudioContext();
    }
  }, [isOpen]);

  const cleanupAudioContext = () => {
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {});
    }
    audioContextRef.current = null;
  };

  // Start Voice Recording
  const startRecording = async () => {
    setErrorMessage('');
    setAudioBlob(null);
    setAudioUrl(null);
    setRecordingSeconds(0);
    audioChunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // Setup audio analyzer for live volume bars
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      audioContextRef.current = audioCtx;
      analyserRef.current = analyser;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateVolume = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);
        const avg = dataArray.reduce((acc, val) => acc + val, 0) / dataArray.length;
        setVolumeLevel(Math.min(100, Math.round((avg / 128) * 100)));
        animFrameRef.current = requestAnimationFrame(updateVolume);
      };
      updateVolume();

      // Setup MediaRecorder
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      const recorder = new MediaRecorder(stream, { mimeType });

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        setAudioBlob(blob);
        const url = URL.createObjectURL(blob);
        setAudioUrl(url);

        // Stop all media tracks
        stream.getTracks().forEach((track) => track.stop());
        cleanupAudioContext();
      };

      mediaRecorderRef.current = recorder;
      recorder.start(200);
      setIsRecording(true);

      // 10-second limit countdown
      timerIntervalRef.current = setInterval(() => {
        setRecordingSeconds((prev) => {
          if (prev >= 9) {
            stopRecording();
            return 10;
          }
          return prev + 1;
        });
      }, 1000);
    } catch (err) {
      console.error('Microphone error:', err);
      setErrorMessage('Could not access your microphone. Please check browser permissions.');
      setIsRecording(false);
    }
  };

  // Stop Voice Recording
  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    setIsRecording(false);
    if (timerIntervalRef.current) clearInterval(timerIntervalRef.current);
    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    setVolumeLevel(0);
  };

  // Play / Pause Recorded Audio
  const togglePlayRecorded = () => {
    if (!audioUrl) return;
    if (!recordedAudioPlayerRef.current) {
      const audio = new Audio(audioUrl);
      audio.onended = () => setIsPlayingRecorded(false);
      recordedAudioPlayerRef.current = audio;
    }
    if (isPlayingRecorded) {
      recordedAudioPlayerRef.current.pause();
      setIsPlayingRecorded(false);
    } else {
      recordedAudioPlayerRef.current.play();
      setIsPlayingRecorded(true);
    }
  };

  // Submit calibration audio to backend
  const handleAnalyzeAndClone = async () => {
    if (!audioBlob) {
      setErrorMessage('Please record at least 4 to 6 seconds of audio first.');
      return;
    }
    if (recordingSeconds < 3) {
      setErrorMessage('Audio sample is too short. Please speak for at least 4 to 6 seconds.');
      return;
    }

    setIsAnalyzing(true);
    setErrorMessage('');
    setAnalysisStep(1);

    const stepInterval = setInterval(() => {
      setAnalysisStep((prev) => (prev < 4 ? prev + 1 : prev));
    }, 1200);

    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'calibration.webm');
      formData.append('participant_name', participantName || 'User');
      formData.append('preferred_accent', selectedVoiceOpt.accent);
      formData.append('preferred_voice', selectedVoiceOpt.voice);

      const res = await fetch(`${BACKEND_URL}/api/voice-clone/calibrate`, {
        method: 'POST',
        body: formData,
      });

      clearInterval(stepInterval);

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Voice calibration failed on the server.');
      }

      const profile = await res.json();
      setCalibratedProfile(profile);
      setAnalysisStep(4);
    } catch (err) {
      console.error('Calibration error:', err);
      clearInterval(stepInterval);
      setErrorMessage(err.message || 'Error communicating with voice analysis backend.');
    } finally {
      setIsAnalyzing(false);
    }
  };

  // Play / Pause synthesized preview audio
  const togglePlayPreview = () => {
    if (!calibratedProfile) return;
    const previewUrl = `${BACKEND_URL}${calibratedProfile.preview_audio_url}?t=${Date.now()}`;

    if (!previewAudioPlayerRef.current) {
      const audio = new Audio(previewUrl);
      audio.onended = () => setIsPlayingPreview(false);
      previewAudioPlayerRef.current = audio;
    }

    if (isPlayingPreview) {
      previewAudioPlayerRef.current.pause();
      setIsPlayingPreview(false);
    } else {
      previewAudioPlayerRef.current.src = previewUrl;
      previewAudioPlayerRef.current.play();
      setIsPlayingPreview(true);
    }
  };

  const handleApplyAndContinue = () => {
    if (onCalibrationComplete && calibratedProfile) {
      onCalibrationComplete(calibratedProfile);
    }
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(5, 7, 15, 0.88)',
        backdropFilter: 'blur(10px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px',
      }}
    >
      <div
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '680px',
          maxHeight: '92vh',
          overflowY: 'auto',
          borderRadius: '18px',
          border: '1px solid var(--border-medium)',
          background: 'rgba(18, 21, 29, 0.98)',
          boxShadow: '0 24px 64px -12px rgba(0, 0, 0, 0.8), inset 0 1px 0 rgba(255, 255, 255, 0.08)',
          padding: '28px 32px',
          position: 'relative',
        }}
      >
        {/* Close Button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '20px',
            right: '20px',
            background: 'rgba(255, 255, 255, 0.04)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '50%',
            width: '32px',
            height: '32px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            transition: 'all 0.18s ease',
          }}
        >
          <X size={16} strokeWidth={1.8} />
        </button>

        {/* Modal Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '22px' }}>
          <div
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '11px',
              background: 'rgba(255, 255, 255, 0.06)',
              border: '1px solid var(--border-medium)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Mic size={20} color="var(--text-main)" strokeWidth={1.8} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.28rem', fontWeight: 600, margin: 0, letterSpacing: '-0.02em', color: '#ffffff' }}>
              Voice & Accent Calibration
            </h2>
            <p style={{ margin: '3px 0 0', fontSize: '0.84rem', color: 'var(--text-muted)' }}>
              Record a brief sample in Indian English to tune the assistant's cadence and tone.
            </p>
          </div>
        </div>

        {/* Error Alert */}
        {errorMessage && (
          <div
            style={{
              marginBottom: '20px',
              padding: '12px 16px',
              borderRadius: '10px',
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.25)',
              color: '#fca5a5',
              fontSize: '0.86rem',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
            }}
          >
            <AlertCircle size={17} strokeWidth={1.8} style={{ flexShrink: 0 }} />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* STEP 1 & 2: Recording Phase */}
        {!calibratedProfile && !isAnalyzing && (
          <div>
            {/* Target Accent & Persona Voice Selector */}
            <div style={{ marginBottom: '18px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                <Globe size={13} color="var(--text-muted)" strokeWidth={1.8} />
                <span>Select Target Voice Accent:</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                {ACCENT_VOICE_OPTIONS.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setSelectedVoiceOpt(opt)}
                    style={{
                      background: selectedVoiceOpt.id === opt.id ? 'rgba(255, 255, 255, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                      border: `1px solid ${selectedVoiceOpt.id === opt.id ? 'rgba(255, 255, 255, 0.25)' : 'var(--border-subtle)'}`,
                      borderRadius: '11px',
                      padding: '10px 14px',
                      textAlign: 'left',
                      cursor: 'pointer',
                      transition: 'all 0.18s ease',
                    }}
                  >
                    <div style={{ fontSize: '0.86rem', fontWeight: 600, color: selectedVoiceOpt.id === opt.id ? '#ffffff' : 'var(--text-secondary)' }}>
                      {opt.label}
                    </div>
                    <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '2px', lineHeight: '1.3' }}>
                      {opt.description}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Script Selector Tabs */}
            <div style={{ marginBottom: '14px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <span style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Choose a Reading Prompt:
                </span>
              </div>
              <div style={{ display: 'flex', gap: '8px', overflowX: 'auto', paddingBottom: '4px' }}>
                {SAMPLE_SCRIPTS.map((script, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => setSelectedScriptIdx(idx)}
                    style={{
                      background: selectedScriptIdx === idx ? 'rgba(255, 255, 255, 0.1)' : 'rgba(255, 255, 255, 0.03)',
                      border: `1px solid ${selectedScriptIdx === idx ? 'rgba(255, 255, 255, 0.22)' : 'var(--border-subtle)'}`,
                      borderRadius: '8px',
                      padding: '7px 12px',
                      color: selectedScriptIdx === idx ? '#ffffff' : 'var(--text-muted)',
                      fontSize: '0.8rem',
                      fontWeight: 500,
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                      transition: 'all 0.18s ease',
                    }}
                  >
                    {script.title}
                  </button>
                ))}
              </div>
            </div>

            {/* Reading Card */}
            <div
              style={{
                background: 'rgba(14, 17, 24, 0.7)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '12px',
                padding: '14px 18px',
                marginBottom: '18px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '6px', color: 'var(--text-muted)', fontSize: '0.74rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                <Radio size={13} strokeWidth={1.8} />
                <span>Read in your natural Indian English pace:</span>
              </div>
              <p style={{ margin: 0, fontSize: '0.96rem', lineHeight: '1.55', color: '#f1f5f9' }}>
                "{SAMPLE_SCRIPTS[selectedScriptIdx].text}"
              </p>
            </div>
            {/* Recording Controls & Visualizer */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '24px 0',
                background: 'rgba(255, 255, 255, 0.02)',
                borderRadius: '14px',
                border: '1px dashed var(--border-medium)',
                marginBottom: '20px',
              }}
            >
              {/* Dynamic Mic Button / Timer */}
              <div style={{ position: 'relative', marginBottom: '14px' }}>
                {isRecording && (
                  <div
                    style={{
                      position: 'absolute',
                      inset: '-10px',
                      borderRadius: '50%',
                      background: 'rgba(239, 68, 68, 0.2)',
                      animation: 'pulse 1.5s infinite ease-out',
                      zIndex: 0,
                    }}
                  />
                )}
                <button
                  type="button"
                  onClick={isRecording ? stopRecording : startRecording}
                  style={{
                    position: 'relative',
                    zIndex: 1,
                    width: '68px',
                    height: '68px',
                    borderRadius: '50%',
                    background: isRecording ? '#dc2626' : '#1a1e28',
                    border: `2px solid ${isRecording ? '#ef4444' : 'rgba(255, 255, 255, 0.16)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    boxShadow: isRecording
                      ? '0 0 24px rgba(239, 68, 68, 0.45)'
                      : '0 4px 16px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.12)',
                    transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
                  }}
                >
                  {isRecording ? <Square size={22} color="#ffffff" strokeWidth={2} /> : <Mic size={26} color="#ffffff" strokeWidth={1.8} />}
                </button>
              </div>

              {/* Status / Countdown */}
              {isRecording ? (
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '1.15rem', fontWeight: 600, color: '#fca5a5', fontFamily: 'var(--font-mono)' }}>
                    Recording: 00:{recordingSeconds < 10 ? `0${recordingSeconds}` : recordingSeconds} / 00:10
                  </div>
                  <p style={{ margin: '4px 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    Speak naturally. Tap the square button or wait for 10s auto-stop.
                  </p>
                  {/* Live Volume Meter */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '3px', marginTop: '10px', height: '16px' }}>
                    {[...Array(16)].map((_, i) => (
                      <div
                        key={i}
                        style={{
                          width: '4px',
                          height: `${Math.max(4, (volumeLevel / 100) * 20 * (1 - Math.abs(i - 8) / 8))}px`,
                          backgroundColor: i > 12 ? '#ef4444' : 'var(--text-secondary)',
                          borderRadius: '2px',
                          transition: 'height 0.05s ease',
                        }}
                      />
                    ))}
                  </div>
                </div>
              ) : audioUrl ? (
                <div style={{ textAlign: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6ee7b7', fontSize: '0.88rem', fontWeight: 600 }}>
                    <CheckCircle2 size={16} strokeWidth={1.8} />
                    <span>Captured {recordingSeconds}s Indian Voice Sample</span>
                  </div>
                  <div style={{ display: 'flex', gap: '10px', marginTop: '12px' }}>
                    <button
                      type="button"
                      onClick={togglePlayRecorded}
                      className="btn-secondary"
                      style={{ padding: '7px 14px', fontSize: '0.82rem' }}
                    >
                      {isPlayingRecorded ? <Pause size={13} strokeWidth={1.8} /> : <Play size={13} strokeWidth={1.8} />}
                      <span>{isPlayingRecorded ? 'Pause' : 'Listen Back'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={startRecording}
                      className="btn-secondary"
                      style={{ padding: '7px 12px', fontSize: '0.82rem' }}
                    >
                      <RotateCcw size={13} strokeWidth={1.8} />
                      <span>Re-record</span>
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ textAlign: 'center' }}>
                  <span style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-main)' }}>
                    Tap microphone to record sample
                  </span>
                  <p style={{ margin: '4px 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    Speak for 5 to 10 seconds for the most natural cadence matching.
                  </p>
                </div>
              )}
            </div>

            {/* Action Button */}
            {audioUrl && !isRecording && (
              <button
                type="button"
                className="btn-primary"
                onClick={handleAnalyzeAndClone}
                style={{
                  width: '100%',
                  padding: '14px',
                  fontSize: '0.96rem',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                }}
              >
                <Sparkles size={16} strokeWidth={1.8} />
                <span>Calibrate Voice with {selectedVoiceOpt.accent}</span>
                <ArrowRight size={15} strokeWidth={1.8} />
              </button>
            )}
          </div>
        )}

        {/* STEP 3: Analysis in Progress */}
        {isAnalyzing && (
          <div style={{ padding: '36px 0', textAlign: 'center' }}>
            <div
              style={{
                width: '60px',
                height: '60px',
                borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid var(--border-medium)',
                margin: '0 auto 20px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Sliders size={24} color="var(--text-main)" strokeWidth={1.8} />
            </div>

            <h3 style={{ fontSize: '1.18rem', fontWeight: 600, marginBottom: '6px', color: '#ffffff' }}>
              Calibrating Voice Persona...
            </h3>
            <p style={{ fontSize: '0.84rem', color: 'var(--text-muted)', maxWidth: '440px', margin: '0 auto 24px', lineHeight: 1.5 }}>
              Analyzing phonetic rhythm, conversational transitions, vocal timbre, and Indian English cadence.
            </p>

            {/* Step Checkpoints */}
            <div style={{ maxWidth: '400px', margin: '0 auto', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.82rem', color: analysisStep >= 1 ? '#6ee7b7' : 'var(--text-dim)' }}>
                <CheckCircle2 size={15} color={analysisStep >= 1 ? '#10b981' : '#64748b'} strokeWidth={1.8} />
                <span>Transcribing Indian speech phonetics with Deepgram</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.82rem', color: analysisStep >= 2 ? '#6ee7b7' : 'var(--text-dim)' }}>
                <CheckCircle2 size={15} color={analysisStep >= 2 ? '#10b981' : '#64748b'} strokeWidth={1.8} />
                <span>Extracting conversational flow & phrasing (Groq)</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.82rem', color: analysisStep >= 3 ? '#6ee7b7' : 'var(--text-dim)' }}>
                <CheckCircle2 size={15} color={analysisStep >= 3 ? '#10b981' : '#64748b'} strokeWidth={1.8} />
                <span>Tuning conversational persona instructions</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.82rem', color: analysisStep >= 4 ? '#6ee7b7' : 'var(--text-dim)' }}>
                <CheckCircle2 size={15} color={analysisStep >= 4 ? '#10b981' : '#64748b'} strokeWidth={1.8} />
                <span>Synthesizing natural Indian English voice preview</span>
              </div>
            </div>
          </div>
        )}

        {/* STEP 4: Calibrated Results & Indian Voice Preview */}
        {calibratedProfile && !isAnalyzing && (
          <div>
            <div
              style={{
                background: 'rgba(16, 185, 129, 0.05)',
                border: '1px solid rgba(16, 185, 129, 0.25)',
                borderRadius: '14px',
                padding: '14px 18px',
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                marginBottom: '18px',
              }}
            >
              <div
                style={{
                  width: '34px',
                  height: '34px',
                  borderRadius: '50%',
                  background: 'rgba(16, 185, 129, 0.15)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <CheckCircle2 size={18} color="#10b981" strokeWidth={1.8} />
              </div>
              <div>
                <div style={{ fontWeight: 600, color: '#6ee7b7', fontSize: '0.94rem' }}>
                  Voice Profile Calibrated Successfully
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                  Active Accent: <strong style={{ color: '#ffffff' }}>{calibratedProfile.accent}</strong>
                </div>
              </div>
            </div>

            {/* Profile Insights Grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '18px' }}>
              <div className="glass-panel" style={{ padding: '12px 14px', borderRadius: '12px' }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Target Accent
                </span>
                <div style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-main)', marginTop: '3px' }}>
                  {calibratedProfile.accent || 'Indian English'}
                </div>
              </div>

              <div className="glass-panel" style={{ padding: '12px 14px', borderRadius: '12px' }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Tone & Warmth
                </span>
                <div style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-main)', marginTop: '3px' }}>
                  {calibratedProfile.tone || 'Warm & Conversational'}
                </div>
              </div>

              <div className="glass-panel" style={{ padding: '12px 14px', borderRadius: '12px' }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Speech Pacing
                </span>
                <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--text-secondary)', marginTop: '3px' }}>
                  {calibratedProfile.pacing || 'Moderate & Rhythmic'}
                </div>
              </div>

              <div className="glass-panel" style={{ padding: '12px 14px', borderRadius: '12px' }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  Voice Model
                </span>
                <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--text-secondary)', marginTop: '3px' }}>
                  {calibratedProfile.recommended_voice || selectedVoiceOpt.voice}
                </div>
              </div>
            </div>

            {/* Cloned Audio Preview Card */}
            <div
              style={{
                background: 'rgba(255, 255, 255, 0.02)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '14px',
                padding: '16px 18px',
                marginBottom: '20px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', color: 'var(--text-secondary)', fontWeight: 500, fontSize: '0.8rem' }}>
                  <Headphones size={15} strokeWidth={1.8} />
                  <span>Audio Preview Sample:</span>
                </div>
                <span className="tag-chip" style={{ color: '#34d399', borderColor: 'rgba(52, 211, 153, 0.25)', background: 'rgba(52, 211, 153, 0.08)' }}>
                  Indian English
                </span>
              </div>

              <p style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#f1f5f9', fontStyle: 'italic', lineHeight: '1.5' }}>
                "{calibratedProfile.preview_greeting || `Namaste ${participantName}! I have calibrated to your voice and tone. Let's chat!`}"
              </p>

              <button
                type="button"
                onClick={togglePlayPreview}
                className="btn-secondary"
                style={{ padding: '7px 14px', fontSize: '0.82rem' }}
              >
                {isPlayingPreview ? <Pause size={14} strokeWidth={1.8} /> : <Play size={14} strokeWidth={1.8} />}
                <span>{isPlayingPreview ? 'Pause Audio' : 'Play Voice Preview'}</span>
              </button>
            </div>

            {/* Bottom Modal Actions */}
            <div style={{ display: 'flex', gap: '10px' }}>
              <button
                type="button"
                onClick={() => {
                  setCalibratedProfile(null);
                  setAudioBlob(null);
                  setAudioUrl(null);
                }}
                className="btn-secondary"
                style={{ flex: 1, padding: '12px', fontSize: '0.88rem' }}
              >
                Re-calibrate
              </button>

              <button
                type="button"
                className="btn-primary"
                onClick={handleApplyAndContinue}
                style={{ flex: 2, padding: '12px', fontSize: '0.92rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}
              >
                <CheckCircle2 size={16} strokeWidth={1.8} />
                <span>Apply & Start Conversation</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
