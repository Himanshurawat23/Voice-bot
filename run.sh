#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

echo "=========================================================="
echo "      LiveKit AI Voice Bot (Python + React) Runner        "
echo "=========================================================="

show_help() {
    echo "Usage: ./run.sh [backend|agent|frontend|all]"
    echo ""
    echo "  backend      : Start the FastAPI token server (port 8000)"
    echo "  agent        : Start the LiveKit voice agent worker in dev mode"
    echo "  frontend     : Start the Vite React frontend (port 5173)"
    echo "  all          : Start backend token server and frontend together"
    echo ""
}

case "$1" in
    backend)
        echo "🚀 Starting FastAPI Token Server on http://localhost:8000..."
        cd "$DIR/backend"
        "$DIR/backend/.venv/bin/python" server.py
        ;;
    agent)
        echo "🤖 Starting LiveKit Voice Agent in dev mode..."
        cd "$DIR/backend"
        "$DIR/backend/.venv/bin/python" agent.py dev
        ;;
    frontend)
        echo "💻 Starting React Frontend on http://localhost:5173..."
        cd "$DIR/frontend"
        npm run dev
        ;;
    all|"")
        echo "🚀 Starting Token Server in background..."
        cd "$DIR/backend"
        "$DIR/backend/.venv/bin/python" server.py &
        SERVER_PID=$!

        echo "💻 Starting Frontend..."
        cd "$DIR/frontend"
        npm run dev &
        FRONTEND_PID=$!

        trap "kill $SERVER_PID $FRONTEND_PID 2>/dev/null || true" EXIT
        wait
        ;;
    *)
        show_help
        ;;
esac
