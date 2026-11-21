#!/bin/sh
# Coreshell service: start | stop | restart | status
# Hooked into /media/fat/linux/user-startup.sh between "# coreshell: BEGIN/END".
# Every game starts through Coreshell (the frontend has no other way), so it's supervised: if it stops, it's
# started again at once, and the frontend waits for it (phosphord's coreshell.c).
# Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).

DIR=/media/fat/coreshell
PID=/tmp/coreshell.pid          # the supervisor; Coreshell itself is its child
LOG=/tmp/coreshell.log
SOCK=/tmp/coreshell.sock

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-start}" in
  start|boot)
    running && exit 0
    cd "$DIR" || exit 1
    : > "$LOG"
    setsid sh "$DIR/scripts/core-shell-supervisor" "$SOCK" "$LOG" </dev/null >/dev/null 2>&1 &
    echo $! > "$PID"
    ;;
  stop)
    running && kill "$(cat "$PID")"
    rm -f "$PID" "$SOCK"
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start
    ;;
  status)
    if running; then echo "running (pid $(cat "$PID"))"; else echo "stopped"; exit 1; fi
    ;;
esac
