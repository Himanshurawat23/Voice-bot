import React from 'react';
import { Mic, MicOff, PhoneOff, Sparkles, Square } from '../icons';

export function ControlBar({
  isMuted = false,
  onToggleMute,
  onDisconnect,
  connectionState = 'connected',
  isSpeaking = false,
  isBotSpeaking = false,
  onInterrupt,
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

      {/* Stop Speaking / Interrupt Button */}
      {onInterrupt && (
        <button
          onClick={onInterrupt}
          className={`dock-btn ${isBotSpeaking ? 'stop-speaking' : ''}`}
          title={isBotSpeaking ? "Stop bot speaking (or press Esc)" : "Stop / interrupt bot audio"}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '7px',
            padding: '0 16px',
            borderRadius: '9999px',
            background: isBotSpeaking ? 'rgba(244, 63, 94, 0.18)' : 'rgba(255, 255, 255, 0.04)',
            border: isBotSpeaking ? '1px solid rgba(244, 63, 94, 0.5)' : '1px solid var(--border-subtle)',
            color: isBotSpeaking ? '#fda4af' : 'var(--text-muted)',
            cursor: 'pointer',
            height: '44px',
            transition: 'all 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <Square size={14} fill={isBotSpeaking ? '#f43f5e' : 'none'} color={isBotSpeaking ? '#f43f5e' : 'currentColor'} strokeWidth={1.8} />
          <span style={{ fontSize: '0.82rem', fontWeight: 600 }}>
            {isBotSpeaking ? 'Stop Bot' : 'Stop'}
          </span>
        </button>
      )}

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
