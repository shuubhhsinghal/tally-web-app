#!/usr/bin/env bash

# Find the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "======================================"
echo " Starting Accounting Web App"
echo "======================================"

# Check if ports are already in use
BACKEND_IN_USE=$(lsof -i :8000 -t)
FRONTEND_IN_USE=$(lsof -i :3000 -t)

if [ -n "$BACKEND_IN_USE" ] || [ -n "$FRONTEND_IN_USE" ]; then
    echo "⚠️  Ports 8000 or 3000 are already in use."
    echo "This means the app is likely already running."
    echo "If you want to restart it, please run 'stop-app.command' first."
    echo ""
    echo "Opening browser anyway..."
    open http://localhost:3000
    
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s -r
    exit 0
fi

# Ensure python venv exists
if [ ! -d ".venv" ]; then
    echo "❌ Python virtual environment (.venv) not found!"
    echo "Please set it up according to the README before running this script."
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s -r
    exit 1
fi

# Ensure node_modules exists
if [ ! -d "frontend/node_modules" ]; then
    echo "❌ Frontend node_modules not found!"
    echo "Please run 'npm install' inside the 'frontend' directory."
    echo ""
    echo "Press any key to exit..."
    read -n 1 -s -r
    exit 1
fi

# Cleanup function when user presses Ctrl+C or closes terminal
cleanup() {
    echo -e "\n🛑 Shutting down servers..."
    
    if [ -n "$FRONTEND_PID" ]; then 
        # Kill the entire process group started by this script for frontend
        kill -TERM -$FRONTEND_PID 2>/dev/null || true
    fi
    
    if [ -n "$BACKEND_PID" ]; then 
        # Kill the entire process group started by this script for backend
        kill -TERM -$BACKEND_PID 2>/dev/null || true
    fi
    
    # Fallback cleanup just in case
    "$DIR/stop-app.command" --quiet
    
    echo "✅ Shutdown complete."
    exit 0
}

# Trap signals for graceful exit
trap cleanup EXIT INT TERM

echo "🚀 Starting Backend (FastAPI)..."
# We run in a new process group so we can kill all children easily
set -m
(
    source .venv/bin/activate
    uvicorn backend.main:app --reload --port 8000 --ws-max-size 52428800 2>&1 | sed -l 's/^/[BACKEND]  /'
) &
BACKEND_PID=$!

echo "🚀 Starting Frontend (Next.js)..."
(
    cd frontend
    npm run dev 2>&1 | sed -l 's/^/[FRONTEND] /'
) &
FRONTEND_PID=$!
set +m

echo "⏳ Waiting for servers to start (3 seconds)..."
sleep 3

echo "🌐 Opening browser to http://localhost:3000 ..."
open http://localhost:3000

echo ""
echo "======================================"
echo "✅ App is ready!"
echo "   Close this window or press Ctrl+C to stop."
echo "======================================"
echo ""

# Keep the script running to stream logs and wait for user to exit
wait $BACKEND_PID $FRONTEND_PID
