import React from 'react';
import { Volume2, Mic, Sparkles, Radio } from '../icons';

export function AudioVisualizer({ state = 'idle', isBotSpeaking = false, isUserSpeaking = false }) {
  // Determine dominant state for visualizer
  let activeState = 'idle';
  let stateLabel = 'Ready';
  let stateColor = 'var(--text-dim)';

  if (state === 'speaking' || isBotSpeaking) {
    activeState = 'speaking';
    stateLabel = 'Agent speaking...';
    stateColor = 'var(--text-main)';
  } else if (state === 'listening' || isUserSpeaking) {
    activeState = 'listening';
    stateLabel = 'Listening to you...';
    stateColor = 'var(--accent-cyan)';
  } else if (state === 'thinking') {
    activeState = 'thinking';
    stateLabel = 'Synthesizing response...';
    stateColor = 'var(--warning)';
  }

  // Frequency wave representation
  const bars = [14, 26, 20, 36, 48, 32, 44, 24, 38, 52, 26, 40, 18, 30, 14];

  return (
    <div className="visualizer-container">
      {/* Dynamic Acoustic Rings */}
      <div className="orb-wrapper">
        <div className={`orb-ring orb-ring-1 ${activeState}`} />
        <div className={`orb-ring orb-ring-2 ${activeState}`} />

        {/* Central Acoustic Core */}
        <div className={`orb-core ${activeState}`}>
          {activeState === 'speaking' && (
            <Volume2 size={36} color="#ffffff" strokeWidth={1.75} />
          )}
          {activeState === 'listening' && (
            <Mic size={34} color="#38bdf8" strokeWidth={1.8} />
          )}
          {activeState === 'thinking' && (
            <Sparkles size={34} color="#fbbf24" strokeWidth={1.8} />
          )}
          {activeState === 'idle' && (
            <Radio size={32} color="rgba(255,255,255,0.6)" strokeWidth={1.6} />
          )}
        </div>
      </div>

      {/* State Label */}
      <div style={{ marginTop: '22px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span className={`status-dot ${activeState}`} />
        <span style={{ fontSize: '0.9rem', fontWeight: 500, color: stateColor, letterSpacing: '-0.01em' }}>
          {stateLabel}
        </span>
      </div>

      {/* Acoustic Frequency Bars */}
      <div className="audio-bars-container">
        {bars.map((height, index) => {
          const isAnimating = activeState === 'speaking' || activeState === 'listening';
          const calculatedHeight = isAnimating ? `${height}px` : '6px';
          const animationDelay = `${(index * 0.07).toFixed(2)}s`;
          return (
            <div
              key={index}
              className={`audio-bar ${isAnimating ? 'active' : ''}`}
              style={{
                height: calculatedHeight,
                animationDelay: animationDelay,
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
