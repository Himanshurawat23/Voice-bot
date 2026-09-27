import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  useVoiceAssistant,
  useLocalParticipant,
  useSpeakingParticipants,
  useRemoteParticipants,
  useRoomContext,
  useChat,
  RoomAudioRenderer,
} from '@livekit/components-react';
import { AudioVisualizer } from './AudioVisualizer';
import { ChatTranscript } from './ChatTranscript';
import { ControlBar } from './ControlBar';
import { Wifi, User, Bot, AlertTriangle, CheckCircle2, Sparkles } from '../icons';

export function VoiceBot({ onDisconnect, roomName, participantName, initialProfile }) {
  const room = useRoomContext();
  const { state: agentState, audioTrack, agent } = useVoiceAssistant();
  const { localParticipant, isMicrophoneEnabled } = useLocalParticipant();
  const remoteParticipants = useRemoteParticipants();
  const speakingParticipants = useSpeakingParticipants();
  const { chatMessages, send: sendChatMessage } = useChat();

  const [isMuted, setIsMuted] = useState(!isMicrophoneEnabled);
  const [voiceMessages, setVoiceMessages] = useState([]);
  // streamingMessage holds the in-progress agent reply as chunks arrive
  const [streamingMessage, setStreamingMessage] = useState(null);
  const streamingRef = useRef(null);
  // Real-time latency metrics from the backend agent
  const [latestMetrics, setLatestMetrics] = useState(null);
  // Calibrated voice profile
  const [clonedProfile, setClonedProfile] = useState(initialProfile || null);
  // Active pipeline info (e.g. Moshi speech-to-speech)
  const [pipelineInfo, setPipelineInfo] = useState(null);

  // Sync mic state and ensure mic is active on mount
  useEffect(() => {
    setIsMuted(!isMicrophoneEnabled);
  }, [isMicrophoneEnabled]);

  useEffect(() => {
    if (localParticipant && !localParticipant.isMicrophoneEnabled) {
      localParticipant.setMicrophoneEnabled(true).catch((err) => {
        console.warn('Microphone enable warning:', err);
      });
    }
  }, [localParticipant]);

  // Ensure browser audio playback is active and not blocked by autoplay restrictions
  useEffect(() => {
    if (!room) return;
    if (!room.canPlaybackAudio) {
      room.startAudio().catch((err) => console.warn('Audio playback start warning:', err));
    }
    const handleAudioChange = () => {
      if (!room.canPlaybackAudio) {
        room.startAudio().catch((err) => console.warn('Audio playback resume warning:', err));
      }
    };
    room.on('audioPlaybackChanged', handleAudioChange);
    return () => room.off('audioPlaybackChanged', handleAudioChange);
  }, [room]);

  // Listen for user voice transcriptions published by the agent on lk-voice-transcript
  useEffect(() => {
    if (!room) return;
    const handleData = (payload, _participant, _kind, topic) => {
      if (topic !== 'lk-voice-transcript') return;
      try {
        const data = JSON.parse(new TextDecoder().decode(payload));
        if (data.message && data.sender === 'user') {
          setVoiceMessages(prev => {
            const msgId = data.id || `user-voice-${data.timestamp}`;
            // Deduplicate by ID or identical text within recent timestamp
            if (prev.some(m => m.id === msgId || (m.sender === 'user' && m.text === data.message && Math.abs(m.rawTimestamp - data.timestamp) < 2000))) {
              return prev;
            }
            return [...prev, {
              id: msgId,
              sender: 'user',
              text: data.message,
              timestamp: new Date(data.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              rawTimestamp: data.timestamp,
            }];
          });
        }
      } catch (e) {
        console.error('Failed to parse voice transcript data:', e);
      }
    };
    room.on('dataReceived', handleData);
    return () => room.off('dataReceived', handleData);
  }, [room]);

  // Handle real-time LLM streaming chunks & latency metrics from the agent
  useEffect(() => {
    if (!room) return;
    const handleStream = (payload, _participant, _kind, topic) => {
      if (topic === 'lk-agent-metrics') {
        try {
          const data = JSON.parse(new TextDecoder().decode(payload));
          if (data.type === 'turn_metrics') {
            setLatestMetrics(data);
          }
        } catch (e) {
          console.error('Failed to parse metrics:', e);
        }
        return;
      }
      if (topic === 'lk-cloned-profile') {
        try {
          const data = JSON.parse(new TextDecoder().decode(payload));
          setClonedProfile(data);
        } catch (e) {
          console.error('Failed to parse cloned profile:', e);
        }
        return;
      }
      if (topic !== 'lk-agent-stream') return;
      try {
        const data = JSON.parse(new TextDecoder().decode(payload));
        if (data.type === 'pipeline_info') {
          setPipelineInfo(data);
          return;
        }
        if (data.type === 'stream_start') {
          // New stream started — create an empty streaming message
          const newMsg = {
            id: `stream-${data.stream_id}`,
            streamId: data.stream_id,
            sender: 'agent',
            text: '',
            timestamp: new Date(data.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            rawTimestamp: data.timestamp,
            streaming: true,
          };
          streamingRef.current = newMsg;
          setStreamingMessage(newMsg);
        } else if (data.type === 'stream_chunk') {
          // Append delta to the active streaming ref
          if (streamingRef.current && streamingRef.current.streamId === data.stream_id) {
            streamingRef.current = {
              ...streamingRef.current,
              text: streamingRef.current.text + data.delta,
            };
            setStreamingMessage({ ...streamingRef.current });
          }
        } else if (data.type === 'stream_end') {
          // Finalise: move completed message into voiceMessages and clear streaming
          if (streamingRef.current && streamingRef.current.streamId === data.stream_id) {
            const completed = { ...streamingRef.current, streaming: false };
            streamingRef.current = null;
            setStreamingMessage(null);
            setVoiceMessages(msgs => {
              // Strictly avoid adding the same stream_id twice (prevents React StrictMode duplication)
              if (msgs.some(m => m.streamId === data.stream_id || m.id === completed.id)) {
                return msgs;
              }
              return [...msgs, completed];
            });
          }
        }
      } catch (e) {
        console.error('Failed to parse agent stream:', e);
      }
    };
    room.on('dataReceived', handleStream);
    return () => room.off('dataReceived', handleStream);
  }, [room]);

  // Handle mute toggle
  const toggleMute = async () => {
    if (localParticipant) {
      const nextState = isMuted;
      await localParticipant.setMicrophoneEnabled(nextState);
      setIsMuted(!nextState);
    }
  };

  // Check if an AI agent is connected to the room
  const hasAgentConnected = useMemo(() => {
    if (agent || pipelineInfo) return true;
    return remoteParticipants.some(
      (p) =>
        p.isAgent ||
        p.kind === 4 ||
        p.kind === 'agent' ||
        p.identity?.toLowerCase().includes('agent') ||
        p.name?.toLowerCase().includes('agent')
    );
  }, [agent, pipelineInfo, remoteParticipants]);

  // Merge typed chat messages (useChat, user-only) + bot streaming messages (voiceMessages)
  // sorted by timestamp. Bot messages now only come through lk-agent-stream → voiceMessages.
  const formattedMessages = useMemo(() => {
    // Only include messages from the local user (typed messages).
    // Bot messages come from voiceMessages (lk-agent-stream) to avoid duplication.
    const chatMsgs = (chatMessages || [])
      .filter(msg => msg.from?.identity === localParticipant?.identity)
      .map((msg, idx) => ({
        id: msg.id || `chat-${msg.timestamp}-${idx}`,
        sender: 'user',
        text: msg.message,
        timestamp: new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        rawTimestamp: msg.timestamp || 0,
      }));

    // Deduplicate voiceMessages by streamId or unique ID
    const seen = new Set();
    const uniqueVoiceMsgs = [];
    for (const msg of voiceMessages) {
      const key = msg.streamId || msg.id || `${msg.sender}-${msg.text}-${msg.rawTimestamp}`;
      if (!seen.has(key)) {
        seen.add(key);
        uniqueVoiceMsgs.push(msg);
      }
    }

    const all = [...chatMsgs, ...uniqueVoiceMsgs];
    all.sort((a, b) => a.rawTimestamp - b.rawTimestamp);
    // Append the live streaming message at the end (if not already completed)
    if (streamingMessage && !seen.has(streamingMessage.streamId)) {
      all.push(streamingMessage);
    }
    return all;
  }, [chatMessages, voiceMessages, streamingMessage, localParticipant]);

  // Send message via text data channel
  const handleSendMessage = async (text) => {
    if (sendChatMessage) {
      await sendChatMessage(text);
      // useChat automatically updates chatMessages, so we don't manually append here!
    }
  };

  const isUserSpeaking = speakingParticipants.some(
    (p) => p.identity === localParticipant?.identity
  );
  const isBotSpeaking =
    agentState === 'speaking' ||
    speakingParticipants.some(
      (p) =>
        p.identity !== localParticipant?.identity &&
        (p.isAgent ||
          p.kind === 4 ||
          p.kind === 'agent' ||
          p.identity?.toLowerCase().includes('agent') ||
          p.identity?.toLowerCase().includes('moshi'))
    );

  // Manual interrupt / stop bot speaking
  const handleInterrupt = async () => {
    try {
      // 1. Immediately clear any in-progress streaming message on UI
      setStreamingMessage(null);

      // 2. Publish interrupt command to backend agent over WebRTC
      if (room?.localParticipant) {
        const payload = JSON.stringify({ action: 'interrupt', timestamp: Date.now() });
        await room.localParticipant.publishData(new TextEncoder().encode(payload), {
          topic: 'lk-control',
          reliable: true,
        });
      }
    } catch (err) {
      console.warn('Failed to send interrupt command:', err);
    }
  };

  // Keyboard shortcut: Escape key stops bot when speaking
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isBotSpeaking) {
        handleInterrupt();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isBotSpeaking]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, padding: '20px 32px', maxWidth: '1440px', margin: '0 auto', width: '100%' }}>
      {/* LiveKit built-in audio player for agent tracks */}
      <RoomAudioRenderer />

      {/* Top Status Bar inside active call */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <div className="status-pill">
            <span className={`status-dot ${hasAgentConnected ? 'connected' : 'connecting'}`} />
            <span style={{ color: 'var(--text-main)', fontWeight: 500, fontSize: '0.82rem' }}>
              {hasAgentConnected ? 'Agent Connected' : 'Waiting for Agent...'}
            </span>
          </div>
          <span style={{ fontSize: '0.82rem', color: 'var(--text-dim)' }}>Room:</span>
          <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)' }}>{roomName}</span>
          {pipelineInfo ? (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.78rem', background: 'rgba(56, 189, 248, 0.08)', border: '1px solid rgba(56, 189, 248, 0.25)', borderRadius: '9999px', padding: '3px 10px', color: '#7dd3fc' }}>
              <span>Pipeline: <strong>{pipelineInfo.model || 'Indian Neural Voice'}</strong></span>
            </div>
          ) : (
            clonedProfile && (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.78rem', background: 'rgba(16, 185, 129, 0.08)', border: '1px solid rgba(16, 185, 129, 0.25)', borderRadius: '9999px', padding: '3px 10px', color: '#6ee7b7' }}>
                <Sparkles size={13} color="#10b981" strokeWidth={1.8} />
                <span>Voice Tuned: <strong>{clonedProfile.accent || 'Indian English'}</strong></span>
              </div>
            )
          )}
          {latestMetrics && (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.78rem', background: 'rgba(255, 255, 255, 0.04)', border: '1px solid var(--border-subtle)', borderRadius: '9999px', padding: '3px 10px', color: 'var(--text-muted)' }}>
              <span>Latency: <strong style={{ color: 'var(--text-main)' }}>{latestMetrics.e2e_ms ? `${latestMetrics.e2e_ms}ms` : `${latestMetrics.ttft_ms || 0}ms`}</strong></span>
              {latestMetrics.ttft_ms ? <span>• LLM: {latestMetrics.ttft_ms}ms</span> : null}
              {latestMetrics.ttfb_ms ? <span>• TTS: {latestMetrics.ttfb_ms}ms</span> : null}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            <User size={14} color="var(--text-muted)" strokeWidth={1.8} />
            <span>{participantName}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem', color: 'var(--success)' }}>
            <Wifi size={14} strokeWidth={1.8} />
            <span>WebRTC Active</span>
          </div>
        </div>
      </div>

      {/* Agent Offline Notice Banner */}
      {!hasAgentConnected && (
        <div
          style={{
            marginBottom: '20px',
            padding: '12px 18px',
            borderRadius: '12px',
            background: 'rgba(245, 158, 11, 0.08)',
            border: '1px solid rgba(245, 158, 11, 0.25)',
            color: '#fde68a',
            fontSize: '0.86rem',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <AlertTriangle size={17} color="#f59e0b" strokeWidth={1.8} style={{ flexShrink: 0 }} />
            <span>
              <strong>Voice Agent is on standby.</strong> Run{' '}
              <code style={{ background: 'rgba(0,0,0,0.3)', padding: '2px 8px', borderRadius: '6px', color: '#ffffff', fontFamily: 'var(--font-mono)' }}>
                ./run.sh agent
              </code>{' '}
              in your terminal to join.
            </span>
          </div>
        </div>
      )}

      {/* Main Grid: Visualizer on Left, Transcript on Right */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.15fr 1fr', gap: '24px', flex: 1, alignItems: 'stretch' }}>
        {/* Left Card: AI Voice Centerpiece */}
        <div className="glass-panel" style={{ padding: '32px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '460px' }}>
          <div style={{ textAlign: 'center', marginBottom: '16px' }}>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 600, marginBottom: '6px', letterSpacing: '-0.02em', color: '#ffffff' }}>
              Conversational Voice Agent
            </h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', maxWidth: '380px', lineHeight: 1.5 }}>
              {hasAgentConnected
                ? (clonedProfile
                    ? `Speaking in natural ${clonedProfile.accent || 'Indian English'} with ${clonedProfile.tone || 'warm conversational tone'}.`
                    : 'Speak naturally through your microphone or type in the chat.')
                : 'Waiting for agent worker to connect...'}
            </p>
          </div>

          <AudioVisualizer
            state={agentState || (hasAgentConnected ? 'idle' : 'connecting')}
            isBotSpeaking={isBotSpeaking}
            isUserSpeaking={isUserSpeaking}
            onInterrupt={handleInterrupt}
          />
        </div>

        {/* Right Card: Live Transcript and Text Chat */}
        <ChatTranscript
          messages={formattedMessages}
          onSendMessage={handleSendMessage}
          onClear={() => setVoiceMessages([])}
        />
      </div>

      {/* Floating Control Bar at Bottom */}
      <div style={{ marginTop: '24px' }}>
        <ControlBar
          isMuted={isMuted}
          onToggleMute={toggleMute}
          onDisconnect={onDisconnect}
          isSpeaking={isUserSpeaking}
          isBotSpeaking={isBotSpeaking}
          onInterrupt={handleInterrupt}
        />
      </div>
    </div>
  );
}
