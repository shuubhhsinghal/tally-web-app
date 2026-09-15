#!/usr/bin/env bash

# Quiet mode suppresses some output when called by start-app.command fallback
QUIET=$1

if [ "$QUIET" != "--quiet" ]; then
    echo "======================================"
    echo " Stopping Accounting Web App"
    echo "======================================"
fi

kill_port() {
    local PORT=$1
    local PROCESS_NAME=$2
    local PIDS=$(lsof -t -i :$PORT 2>/dev/null)
    
    if [ -n "$PIDS" ]; then
        for PID in $PIDS; do
            # Check if this PID looks like our app (safety check)
            CMD=$(ps -p $PID -o command=)
            
            # If it's node or python, we consider it safe to kill because it's on our designated port
            if echo "$CMD" | grep -E -i -q "python|uvicorn|node|npm|next"; then
                if [ "$QUIET" != "--quiet" ]; then
                    echo "🛑 Stopping $PROCESS_NAME on port $PORT (PID: $PID)..."
                fi
                # Use kill -9 to forcefully terminate to ensure port is freed
                kill -9 $PID 2>/dev/null
            else
                if [ "$QUIET" != "--quiet" ]; then
                    echo "⚠️  Found PID $PID on port $PORT but it doesn't look like our $PROCESS_NAME. Skipping."
                fi
            fi
        done
    else
        if [ "$QUIET" != "--quiet" ]; then
            echo "✅ $PROCESS_NAME is not running on port $PORT."
        fi
    fi
}

# Kill processes on known ports
kill_port 3000 "Frontend"
kill_port 8000 "Backend"

if [ "$QUIET" != "--quiet" ]; then
    echo "======================================"
    echo " Done!"
    echo "======================================"
    
    # Keep window open slightly if launched by double click, 
    # but not necessary since user can see 'Done!' and terminal closes or stays depending on their setting.
    # We will just sleep 1 so they can read it.
    sleep 1
fi
