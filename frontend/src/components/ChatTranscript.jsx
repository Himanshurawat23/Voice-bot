import React, { useRef, useEffect, useState } from 'react';
import { Send, Bot, User, Trash2 } from '../icons';

export function ChatTranscript({ messages = [], onSendMessage, onClear }) {
  const [inputText, setInputText] = useState('');
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    onSendMessage?.(inputText.trim());
    setInputText('');
  };

  return (
    <div className="glass-panel transcript-card">
      <div className="transcript-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Bot size={16} color="var(--text-secondary)" strokeWidth={1.8} />
          <span style={{ fontWeight: 600, fontSize: '0.9rem', color: '#ffffff', letterSpacing: '-0.01em' }}>
            Live Conversation
          </span>
          <span className="tag-chip">
            {messages.length} messages
          </span>
        </div>
        {messages.length > 0 && (
          <button
            onClick={onClear}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-dim)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '0.78rem',
              transition: 'color 0.2s',
            }}
            title="Clear transcripts"
          >
            <Trash2 size={13} strokeWidth={1.8} /> Clear
          </button>
        )}
      </div>

      {/* Messages List */}
      <div className="transcript-messages">
        {messages.length === 0 ? (
          <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--text-dim)', maxWidth: '280px' }}>
            <div style={{ width: '44px', height: '44px', margin: '0 auto 12px', borderRadius: '50%', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Bot size={20} color="var(--text-dim)" strokeWidth={1.7} />
            </div>
            <p style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--text-secondary)' }}>Ready for conversation</p>
            <p style={{ fontSize: '0.78rem', marginTop: '4px', color: 'var(--text-muted)' }}>
              Speak naturally through your microphone or type below.
            </p>
          </div>
        ) : (
          messages.map((msg, index) => {
            const isUser = msg.sender === 'user';
            return (
              <div key={index} className={`transcript-bubble ${isUser ? 'user' : 'agent'}`}>
                <div className={`bubble-sender ${isUser ? 'user' : 'agent'}`}>
                  {isUser ? <User size={12} strokeWidth={1.8} /> : <Bot size={12} strokeWidth={1.8} />}
                  <span>{isUser ? 'You' : 'Voice Agent'}</span>
                  {msg.timestamp && (
                    <span style={{ fontSize: '0.65rem', color: 'var(--text-dim)', marginLeft: 'auto', fontWeight: 400 }}>
                      {msg.timestamp}
                    </span>
                  )}
                </div>
                <div style={{ whiteSpace: 'pre-wrap' }}>
                  {msg.text}
                  {msg.streaming && (
                    <span style={{
                      display: 'inline-block',
                      width: '2px',
                      height: '1em',
                      background: 'var(--text-main)',
                      marginLeft: '2px',
                      verticalAlign: 'text-bottom',
                      animation: 'blink 1s step-end infinite',
                    }} />
                  )}
                </div>
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Text message input fallback */}
      <form onSubmit={handleSubmit} style={{ padding: '12px 16px', borderTop: '1px solid var(--border-subtle)', display: 'flex', gap: '8px', background: 'rgba(10, 13, 20, 0.5)' }}>
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="Type a message..."
          className="input-field"
          style={{ padding: '9px 13px', fontSize: '0.88rem' }}
        />
        <button
          type="submit"
          className="btn-primary"
          style={{ padding: '0 16px', borderRadius: '10px' }}
          disabled={!inputText.trim()}
        >
          <Send size={15} strokeWidth={1.8} />
        </button>
      </form>
    </div>
  );
}
