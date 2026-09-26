import React, { useState, useEffect } from 'react';
import { LiveKitRoom } from '@livekit/components-react';
import '@livekit/components-styles';
import { VoiceBot } from './components/VoiceBot';
import { VoiceCalibrationModal } from './components/VoiceCalibrationModal';
import {
  Mic,
  Radio,
  Server,
  Sparkles,
  Zap,
  CheckCircle2,
  AlertCircle,
  Key,
  ShieldCheck,
  ChevronDown,
  ChevronUp,
} from './icons';

const BACKEND_API_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

export default function App() {
  const [token, setToken] = useState('');
  const [serverUrl, setServerUrl] = useState('');
  const [roomName, setRoomName] = useState('voice-bot-room');
  const [participantName, setParticipantName] = useState(`User-${Math.floor(1000 + Math.random() * 9000)}`);
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // Voice Calibration Modal & Active Cloned Profile
  const [isCalibrationModalOpen, setIsCalibrationModalOpen] = useState(false);
  const [calibratedProfile, setCalibratedProfile] = useState(null);

  // Backend Health Diagnostics
  const [backendHealth, setBackendHealth] = useState({
    checked: false,
    online: false,
    configurations: { livekit: false, deepgram: false, gemini: false },
    message: '',
  });

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [manualToken, setManualToken] = useState('');
  const [selectedPipeline, setSelectedPipeline] = useState('indian-voice');

  // Check backend health & fetch existing profile on mount
  useEffect(() => {
    checkHealth();
    fetchExistingProfile();
  }, []);

  const fetchExistingProfile = async (name) => {
    try {
      const res = await fetch(`${BACKEND_API_URL}/api/voice-clone/profile/${name || participantName}`);
      if (res.ok) {
        const data = await res.json();
        setCalibratedProfile(data);
      }
    } catch {
      // No saved profile yet, perfectly fine
    }
  };

  const checkHealth = async () => {
    try {
      const res = await fetch(`${BACKEND_API_URL}/api/health`);
      if (res.ok) {
        const data = await res.json();
        setBackendHealth({
          checked: true,
          online: true,
          configurations: data.configurations || {},
          message: data.message || 'Connected to backend server',
        });
        if (data.livekit_url) {
          setServerUrl(data.livekit_url);
        }
      } else {
        throw new Error('Backend returned non-200 status');
      }
    } catch (err) {
      setBackendHealth({
        checked: true,
        online: false,
        configurations: { livekit: false, deepgram: false, gemini: false },
        message: 'Backend server not detected on http://localhost:8000',
      });
    }
  };

  // Connect to the room
  const handleConnect = async (e) => {
    e?.preventDefault();
    setIsLoading(true);
    setErrorMessage('');

    try {
      // If manual token provided, use that
      if (manualToken.trim()) {
        setToken(manualToken.trim());
        setIsConnected(true);
        setIsLoading(false);
        return;
      }

      // Request indian-voice-agent
      const targetAgent = 'indian-voice-agent';

      // Otherwise fetch token from backend server
      const res = await fetch(`${BACKEND_API_URL}/api/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          room_name: roomName,
          participant_name: participantName,
          agent_name: targetAgent,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || 'Failed to generate token from backend server.');
      }

      setToken(data.token);
      if (data.url) setServerUrl(data.url);
      setIsConnected(true);
    } catch (err) {
      console.error('Connection error:', err);
      setErrorMessage(
        err.message || 'Failed to connect. Please ensure your backend server and LiveKit credentials are active.'
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleDisconnect = () => {
    setIsConnected(false);
    setToken('');
  };

  return (
    <div className="app-container">
      {/* Top Navigation / Brand Header */}
      <header className="app-header">
        <div className="brand-badge">
          <div className="brand-logo-icon">
            <Radio size={20} color="var(--text-main)" strokeWidth={1.8} />
          </div>
          <div>
            <div className="brand-title">LiveKit Studio</div>
            <div className="brand-subtitle">Natural Voice • Indian Accent Edition</div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          {/* Backend Status Badge */}
          <div
            className="status-pill"
            style={{
              borderColor: backendHealth.online ? 'rgba(16, 185, 129, 0.25)' : 'rgba(239, 68, 68, 0.25)',
              background: backendHealth.online ? 'rgba(16, 185, 129, 0.06)' : 'rgba(239, 68, 68, 0.06)',
            }}
          >
            <span
              className={`status-dot ${backendHealth.online ? 'connected' : 'idle'}`}
              style={{ background: backendHealth.online ? 'var(--success)' : 'var(--danger)' }}
            />
            <span style={{ fontSize: '0.8rem', color: backendHealth.online ? '#6ee7b7' : '#fca5a5' }}>
              {backendHealth.online ? 'Backend Online' : 'Backend Offline'}
            </span>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      {isConnected && token && serverUrl ? (
        <LiveKitRoom
          serverUrl={serverUrl}
          token={token}
          connect={true}
          audio={{
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          }}
          video={false}
          onDisconnected={handleDisconnect}
          data-lk-theme="default"
          style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        >
          <VoiceBot
            onDisconnect={handleDisconnect}
            roomName={roomName}
            participantName={participantName}
            initialProfile={calibratedProfile}
            selectedPipeline={selectedPipeline}
          />
        </LiveKitRoom>
      ) : (
        /* Welcome / Connection Lobby */
        <main
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '36px 24px',
            maxWidth: '880px',
            margin: '0 auto',
            width: '100%',
          }}
        >
          {/* Hero Pitch */}
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '5px 14px',
                borderRadius: '9999px',
                background: 'rgba(255, 255, 255, 0.04)',
                border: '1px solid var(--border-medium)',
                color: 'var(--text-secondary)',
                fontSize: '0.8rem',
                fontWeight: 500,
                letterSpacing: '-0.01em',
                marginBottom: '18px',
              }}
            >
              <span className="status-dot connected" style={{ width: '6px', height: '6px' }} />
              <span>Indian English Voice Assistant</span>
            </div>
            <h1
              style={{
                fontSize: '2.4rem',
                fontWeight: 700,
                letterSpacing: '-0.035em',
                lineHeight: 1.18,
                marginBottom: '14px',
                color: '#ffffff',
              }}
            >
              Natural voice conversation,<br />
              tuned to your cadence.
            </h1>
            <p
              style={{
                fontSize: '0.98rem',
                color: 'var(--text-muted)',
                maxWidth: '560px',
                margin: '0 auto',
                lineHeight: 1.6,
                letterSpacing: '-0.01em',
              }}
            >
              A conversational companion that speaks natural Indian English, mirrors your vocal warmth, and answers with sub-second latency.
            </p>
          </div>

          {/* Configuration Cards Grid */}
          <div
            style={{
              width: '100%',
              display: 'grid',
              gridTemplateColumns: '1fr',
              gap: '24px',
            }}
          >
            {/* Connection Form Card */}
            <div className="glass-panel" style={{ padding: '36px' }}>
              <form onSubmit={handleConnect} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>
                      Room Name
                    </label>
                    <input
                      type="text"
                      className="input-field"
                      value={roomName}
                      onChange={(e) => setRoomName(e.target.value)}
                      required
                    />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>
                      Participant Identity
                    </label>
                    <input
                      type="text"
                      className="input-field"
                      value={participantName}
                      onChange={(e) => setParticipantName(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '8px' }}>
                    LiveKit Server URL
                  </label>
                  <input
                    type="text"
                    className="input-field"
                    value={serverUrl}
                    onChange={(e) => setServerUrl(e.target.value)}
                    placeholder="wss://your-project.livekit.cloud"
                    required
                  />
                </div>

                {/* Error Banner */}
                {errorMessage && (
                  <div
                    style={{
                      padding: '14px 18px',
                      borderRadius: '12px',
                      background: 'rgba(239, 68, 68, 0.1)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      color: '#fca5a5',
                      fontSize: '0.88rem',
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '10px',
                    }}
                  >
                    <AlertCircle size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
                    <div>{errorMessage}</div>
                  </div>
                )}

                {/* Voice & Tone Tuning Card */}
                <div
                  style={{
                    background: calibratedProfile
                      ? 'rgba(16, 185, 129, 0.04)'
                      : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${
                      calibratedProfile ? 'rgba(16, 185, 129, 0.25)' : 'var(--border-subtle)'
                    }`,
                    borderRadius: '14px',
                    padding: '16px 18px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '14px',
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                    <div
                      style={{
                        width: '40px',
                        height: '40px',
                        borderRadius: '10px',
                        background: calibratedProfile
                          ? 'rgba(16, 185, 129, 0.12)'
                          : 'rgba(255, 255, 255, 0.05)',
                        border: `1px solid ${calibratedProfile ? 'rgba(16, 185, 129, 0.25)' : 'var(--border-subtle)'}`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      <Sparkles size={18} color={calibratedProfile ? '#10b981' : 'var(--text-secondary)'} strokeWidth={1.8} />
                    </div>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 600, fontSize: '0.92rem', color: '#ffffff', letterSpacing: '-0.01em' }}>
                          {calibratedProfile ? 'Voice Calibration Active' : 'Calibrate Voice & Accent'}
                        </span>
                        {calibratedProfile && (
                          <span
                            style={{
                              fontSize: '0.7rem',
                              background: 'rgba(16, 185, 129, 0.12)',
                              color: '#6ee7b7',
                              border: '1px solid rgba(16, 185, 129, 0.25)',
                              padding: '2px 8px',
                              borderRadius: '9999px',
                              fontWeight: 600,
                              letterSpacing: '0.02em',
                            }}
                          >
                            INDIAN ACCENT
                          </span>
                        )}
                      </div>
                      <p style={{ margin: '3px 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                        {calibratedProfile
                          ? `${calibratedProfile.accent || 'Indian English'} • ${calibratedProfile.tone || 'Mirrored tone'}`
                          : 'Record a brief sample so the bot adapts to your natural accent and tone.'}
                      </p>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => setIsCalibrationModalOpen(true)}
                    className="btn-secondary"
                    style={{ padding: '8px 14px', fontSize: '0.82rem' }}
                  >
                    <Mic size={14} strokeWidth={1.8} />
                    <span>{calibratedProfile ? 'Re-calibrate' : 'Calibrate Voice'}</span>
                  </button>
                </div>

                {/* Voice Pipeline / Engine Status */}
                <div
                  style={{
                    padding: '14px 16px',
                    borderRadius: '12px',
                    border: '1px solid rgba(16, 185, 129, 0.35)',
                    background: 'rgba(16, 185, 129, 0.05)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '1.2rem' }}>🇮🇳</span>
                      <span style={{ fontWeight: 600, fontSize: '0.92rem', color: '#6ee7b7' }}>
                        Indian Accent & Neural Voice Engine
                      </span>
                    </div>
                    <span
                      style={{
                        fontSize: '0.65rem',
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: '9999px',
                        background: 'rgba(16, 185, 129, 0.15)',
                        color: '#6ee7b7',
                        border: '1px solid rgba(16, 185, 129, 0.3)',
                      }}
                    >
                      READY
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                    Deepgram Nova-2 (en-IN) + Microsoft Neural Indian Voice + Groq Fast LLM.
                  </p>
                </div>

                {/* Submit Connect Button */}
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={isLoading}
                  style={{ width: '100%', padding: '14px 20px', fontSize: '0.96rem', marginTop: '6px' }}
                >
                  {isLoading ? (
                    <>Connecting to Voice Room...</>
                  ) : (
                    <>
                      <Mic size={18} strokeWidth={2} /> Start Voice Conversation
                    </>
                  )}
                </button>

                {/* Advanced: Manual Token Input */}
                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '16px', marginTop: '4px' }}>
                  <button
                    type="button"
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--text-dim)',
                      cursor: 'pointer',
                      fontSize: '0.82rem',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      margin: '0 auto',
                    }}
                  >
                    <Key size={14} strokeWidth={1.8} />
                    <span>{showAdvanced ? 'Hide manual token override' : 'Have a custom LiveKit token? Click here'}</span>
                    {showAdvanced ? <ChevronUp size={14} strokeWidth={1.8} /> : <ChevronDown size={14} strokeWidth={1.8} />}
                  </button>

                  {showAdvanced && (
                    <div style={{ marginTop: '14px' }}>
                      <label style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '6px' }}>
                        LiveKit Participant Token (JWT)
                      </label>
                      <textarea
                        rows={3}
                        className="input-field"
                        style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}
                        placeholder="Paste JWT token here to bypass backend token generation..."
                        value={manualToken}
                        onChange={(e) => setManualToken(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              </form>
            </div>

            {/* Hardware & Pipeline Status Bar */}
            <div
              className="glass-panel"
              style={{
                padding: '14px 22px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '14px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  <CheckCircle2 size={15} color="var(--success)" strokeWidth={1.8} />
                  <span>Deepgram Nova-2 (en-IN)</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  <CheckCircle2 size={15} color="var(--success)" strokeWidth={1.8} />
                  <span>Groq Ultra-Fast LLM</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  <CheckCircle2 size={15} color="var(--success)" strokeWidth={1.8} />
                  <span>Indian Neural Voice</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  <CheckCircle2 size={15} color="var(--success)" strokeWidth={1.8} />
                  <span>Silero VAD (Local)</span>
                </div>
              </div>

              <button
                onClick={checkHealth}
                className="btn-secondary"
                style={{ padding: '6px 12px', fontSize: '0.78rem' }}
              >
                Refresh Diagnostics
              </button>
            </div>
          </div>
        </main>
      )}
      {/* Voice & Tone Calibration Studio Modal */}
      <VoiceCalibrationModal
        isOpen={isCalibrationModalOpen}
        onClose={() => setIsCalibrationModalOpen(false)}
        participantName={participantName}
        onCalibrationComplete={(profile) => {
          setCalibratedProfile(profile);
          setIsCalibrationModalOpen(false);
        }}
      />
    </div>
  );
}
