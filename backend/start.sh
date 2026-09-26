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

# Start LiveKit Agent Worker in production mode ('start')
echo "🤖 Starting LiveKit Agent Worker..."
python agent.py start &
AGENT_PID=$!

# Trap signals and terminate both child processes cleanly
trap "echo 'Shutting down services...'; kill -TERM $SERVER_PID $AGENT_PID 2>/dev/null; wait" SIGINT SIGTERM EXIT

# Wait on both processes
wait -n $SERVER_PID $AGENT_PID
