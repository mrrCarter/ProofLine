#!/usr/bin/env bash
set -euo pipefail

SESSION_ID="${SESSION_ID:-36d95ac5-3074-40b4-8995-8961a5187523}"
AGENT_ID="${AGENT_ID:-infra-gpt5.5}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-20}"
RESTART_BACKOFF_SECONDS="${RESTART_BACKOFF_SECONDS:-5}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SEED_FILE="${SEED_FILE:-$HOME/.config/proofline/demo-ed25519-seed.b64}"
PUBLIC_KEY_ID="${PROOFLINE_PUBLIC_KEY_ID:-proofline-demo-2026-06}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$HOME/.local/bin/cloudflared}"
TESSERACT_ROOT="${TESSERACT_ROOT:-$HOME/.local/proofline-tesseract}"

UVICORN_PID=""
CLOUDFLARED_PID=""
CLOUDFLARED_LOG=""
PUBLIC_URL=""

say() {
  sl session say "$SESSION_ID" --agent "$AGENT_ID" "$1" >/dev/null || true
}

cleanup() {
  if [[ -n "${CLOUDFLARED_PID}" ]] && kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
    kill "$CLOUDFLARED_PID" 2>/dev/null || true
  fi
  if [[ -n "${UVICORN_PID}" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
  fi
  [[ -n "${CLOUDFLARED_LOG}" ]] && rm -f "$CLOUDFLARED_LOG"
}
trap cleanup EXIT INT TERM

ensure_seed() {
  umask 077
  mkdir -p "$(dirname "$SEED_FILE")"
  if [[ ! -s "$SEED_FILE" ]]; then
    python3 -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())' > "$SEED_FILE"
  fi
  chmod 600 "$SEED_FILE"
}

start_uvicorn() {
  export PATH="$TESSERACT_ROOT/usr/bin:$HOME/.local/bin:$PATH"
  export LD_LIBRARY_PATH="$TESSERACT_ROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
  export TESSDATA_PREFIX="$TESSERACT_ROOT/usr/share/tesseract-ocr/5/tessdata"
  export PROOFLINE_ENV=production
  unset PROOFLINE_ALLOW_EPHEMERAL_RECEIPTS
  export PROOFLINE_PUBLIC_KEY_ID="$PUBLIC_KEY_ID"
  export PROOFLINE_ED25519_SEED_B64
  PROOFLINE_ED25519_SEED_B64="$(cat "$SEED_FILE")"
  export VISION_PROVIDER=local
  export UI_STATIC_DIR=ui/dist
  export BUILD_SHA
  BUILD_SHA="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"

  (
    cd "$PROJECT_ROOT"
    exec python3 -m uvicorn main:app --host "$APP_HOST" --port "$APP_PORT"
  ) &
  UVICORN_PID="$!"

  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "http://$APP_HOST:$APP_PORT/healthz" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  say "INFRA-GPT5.5 watchdog: uvicorn failed to become healthy on $APP_HOST:$APP_PORT for buildSha=$BUILD_SHA."
  return 1
}

start_tunnel() {
  CLOUDFLARED_LOG="$(mktemp)"
  "$CLOUDFLARED_BIN" tunnel --url "http://$APP_HOST:$APP_PORT" >"$CLOUDFLARED_LOG" 2>&1 &
  CLOUDFLARED_PID="$!"

  for _ in $(seq 1 60); do
    PUBLIC_URL="$(grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | tail -n 1 || true)"
    if [[ -n "$PUBLIC_URL" ]]; then
      return 0
    fi
    if ! kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
      say "INFRA-GPT5.5 watchdog: cloudflared exited before publishing a URL. Log: $(tail -n 20 "$CLOUDFLARED_LOG" | tr '\n' ' ')"
      return 1
    fi
    sleep 1
  done

  say "INFRA-GPT5.5 watchdog: cloudflared did not publish a trycloudflare URL within 60s."
  return 1
}

verify_public_url() {
  local health
  health="$(curl -fsS --max-time 10 "$PUBLIC_URL/healthz")"
  local pubkey
  pubkey="$(curl -fsS --max-time 10 "$PUBLIC_URL/api/receipts/pubkey")"
  say "INFRA-GPT5.5 watchdog: public quick tunnel live: $PUBLIC_URL . health=$health pubkey=$pubkey . Backing buildSha=$BUILD_SHA, uvicorn_pid=$UVICORN_PID, cloudflared_pid=$CLOUDFLARED_PID. This is a fallback; named tunnel/AWS remains the durable target."
}

stop_children() {
  if [[ -n "${CLOUDFLARED_PID}" ]] && kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
    kill "$CLOUDFLARED_PID" 2>/dev/null || true
    wait "$CLOUDFLARED_PID" 2>/dev/null || true
  fi
  if [[ -n "${UVICORN_PID}" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
  [[ -n "${CLOUDFLARED_LOG}" ]] && rm -f "$CLOUDFLARED_LOG"
  UVICORN_PID=""
  CLOUDFLARED_PID=""
  CLOUDFLARED_LOG=""
  PUBLIC_URL=""
}

ensure_seed
say "INFRA-GPT5.5 watchdog starting from $PROJECT_ROOT. It will relaunch uvicorn+quick-tunnel and rebroadcast replacement URLs on failure."

while true; do
  stop_children
  if start_uvicorn && start_tunnel && verify_public_url; then
    while true; do
      sleep "$HEALTH_INTERVAL_SECONDS"
      if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
        say "INFRA-GPT5.5 watchdog alert: uvicorn exited for $PUBLIC_URL; relaunching after ${RESTART_BACKOFF_SECONDS}s."
        break
      fi
      if ! kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
        say "INFRA-GPT5.5 watchdog alert: cloudflared exited for $PUBLIC_URL; relaunching after ${RESTART_BACKOFF_SECONDS}s."
        break
      fi
      if ! curl -fsS --max-time 10 "$PUBLIC_URL/healthz" >/dev/null; then
        say "INFRA-GPT5.5 watchdog alert: health check failed for $PUBLIC_URL; relaunching after ${RESTART_BACKOFF_SECONDS}s."
        break
      fi
    done
  fi
  sleep "$RESTART_BACKOFF_SECONDS"
done
