import React from 'react';
import { Mic, MicOff, PhoneOff, Sparkles } from '../icons';

export function ControlBar({
  isMuted = false,
  onToggleMute,
  onDisconnect,
  connectionState = 'connected',
  isSpeaking = false,
}) {
  return (
    <div className="control-dock">
      {/* Microphone Mute/Unmute */}
      <button
        onClick={onToggleMute}
        className={`dock-btn ${!isMuted ? 'active-primary' : ''}`}
        title={isMuted ? 'Unmute microphone' : 'Mute microphone'}
      >
        {isMuted ? (
          <MicOff size={19} color="#fb7185" strokeWidth={1.8} />
        ) : (
          <Mic size={19} color="#ffffff" strokeWidth={1.8} />
        )}
      </button>

      {/* Voice Status Pill / Indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 14px', borderRadius: '9999px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-subtle)' }}>
        <Sparkles size={14} color={isSpeaking ? '#38bdf8' : 'var(--text-muted)'} strokeWidth={1.8} />
        <span style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--text-secondary)' }}>
          {isMuted ? 'Microphone Muted' : isSpeaking ? 'Transmitting Audio' : 'Microphone Ready'}
        </span>
      </div>

      {/* End Call / Leave Room */}
      <button
        onClick={onDisconnect}
        className="dock-btn danger"
        title="Leave conversation"
      >
        <PhoneOff size={18} strokeWidth={1.8} />
      </button>
    </div>
  );
}
