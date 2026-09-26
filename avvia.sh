#!/usr/bin/env bash
# Start, stop and check fantascout locally: the API (8441) and the web GUI (8442).
# Usage: ./avvia.sh start|stop|status
#
# Both run detached, with pid files and logs under $XDG_STATE_HOME/fantascout
# (default ~/.local/state/fantascout). Prerequisites are the ones in the README:
# .venv with requirements.txt, and `npm install` in web/.
# The name and the start|stop|status interface are a convention of the Mike
# ecosystem, where a home page starts modules on demand; the script works on its own.
set -u
QUI="$(cd "$(dirname "$0")" && pwd)"
STATO="${XDG_STATE_HOME:-$HOME/.local/state}/fantascout"
API_PORT="${FANTASCOUT_API_PORT:-8441}"
WEB_PORT="${FANTASCOUT_WEB_PORT:-8442}"
mkdir -p "$STATO"

vivo() { [ -f "$STATO/$1.pid" ] && kill -0 "$(cat "$STATO/$1.pid")" 2>/dev/null; }

risponde() { # porta: 0 if something answers on 127.0.0.1
  if command -v nc >/dev/null 2>&1; then nc -z 127.0.0.1 "$1" >/dev/null 2>&1; return; fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

avvia_api() {
  if vivo api; then return 0; fi
  [ -x "$QUI/.venv/bin/python" ] || { echo "fantascout: missing .venv (see README: python -m venv .venv)"; return 1; }
  # exec in the subshell, so $! is the server itself and stop can kill it.
  (cd "$QUI" && exec nohup .venv/bin/python -m advisor.server --host 127.0.0.1 --port "$API_PORT" \
    >>"$STATO/api.log" 2>&1 </dev/null) &
  echo $! > "$STATO/api.pid"
}

avvia_web() {
  if vivo web; then return 0; fi
  [ -x "$QUI/web/node_modules/.bin/vite" ] || { echo "fantascout: missing web/node_modules (see README: cd web && npm install)"; return 1; }
  # vite directly, not `npm run dev`: npm would leave vite running after a stop.
  (cd "$QUI/web" && VITE_LOCAL_API_BASE="http://127.0.0.1:$API_PORT" exec nohup node_modules/.bin/vite \
    --host 127.0.0.1 --port "$WEB_PORT" --strictPort >>"$STATO/web.log" 2>&1 </dev/null) &
  echo $! > "$STATO/web.pid"
}

ferma() {
  if vivo "$1"; then kill "$(cat "$STATO/$1.pid")" 2>/dev/null; fi
  rm -f "$STATO/$1.pid"
}

case "${1:-status}" in
  start)
    avvia_api || exit 1
    avvia_web || { ferma api; exit 1; }
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      risponde "$API_PORT" && risponde "$WEB_PORT" && break
      sleep 1
    done
    if risponde "$API_PORT" && risponde "$WEB_PORT"; then
      echo "fantascout: on at http://127.0.0.1:$WEB_PORT (API $API_PORT)"
    else
      echo "fantascout: did not start, see $STATO/api.log and $STATO/web.log"; exit 1
    fi
    ;;
  stop)
    ferma web; ferma api
    echo "fantascout: off"
    ;;
  status)
    a="off"; w="off"
    vivo api && a="on (pid $(cat "$STATO/api.pid"))"
    vivo web && w="on (pid $(cat "$STATO/web.pid"))"
    echo "fantascout: API $API_PORT $a, GUI $WEB_PORT $w"
    ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
