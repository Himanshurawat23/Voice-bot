#!/bin/bash
set -e

echo "=========================================================="
echo " Starting LiveKit Voice Bot Production Services           "
echo "=========================================================="

PORT=${PORT:-8000}
SERVER_PORT=${SERVER_PORT:-$PORT}
export SERVER_PORT

# Start FastAPI Token Server in the background
echo "🚀 Starting FastAPI Token Server on port $SERVER_PORT..."
python server.py &
SERVER_PID=$!

# Wait a second for token server to initialize
sleep 2

# Start LiveKit Agent Worker with auto-reconnect supervisor
echo "🤖 Starting LiveKit Agent Worker..."
(
    while true; do
        python agent.py start || true
        echo "Agent worker stopped, restarting in 3s..."
        sleep 3
    done
) &
AGENT_PID=$!

# Trap signals and terminate cleanly
trap "echo 'Shutting down services...'; kill -TERM $SERVER_PID $AGENT_PID 2>/dev/null; wait" SIGINT SIGTERM EXIT

# Keep running as long as the primary FastAPI token server is alive
wait $SERVER_PID
