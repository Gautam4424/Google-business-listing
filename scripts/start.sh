#!/usr/bin/env bash
# Starts Local SEO Audit on Mac or Linux. On a Mac, double-click start.command in the main folder.
cd "$(dirname "$0")/.." || exit 1

say()  { printf '%b\n' "$1"; }
fail() { say "\n  \033[31mPROBLEM:\033[0m $1\n"; read -r -p "Press Enter to close this window"; exit 1; }
env_value() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- | tr -d '\r' | xargs; }
new_password() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24; }
set_env() {  # set_env NAME VALUE  (portable: no sed -i)
  awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > .env.tmp && mv .env.tmp .env
}
open_url() { if command -v open >/dev/null; then open "$1"; elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1; fi; }
edit_file() {
  if [ "$(uname)" = "Darwin" ]; then open -e "$1"; else "${EDITOR:-nano}" "$1"; fi
}

say "\n  \033[36mLocal SEO Audit - starting\033[0m\n  ----------------------------"

# 1. Docker installed?
if ! command -v docker >/dev/null 2>&1; then
  open_url "https://www.docker.com/products/docker-desktop/"
  fail "Docker Desktop is not installed. The download page has opened. Install it, open it once, then run this again."
fi

# 2. Docker running?
if ! docker info >/dev/null 2>&1; then
  say "  Starting Docker Desktop (this can take a minute)..."
  [ "$(uname)" = "Darwin" ] && open -a Docker >/dev/null 2>&1
  for _ in $(seq 1 60); do sleep 3; docker info >/dev/null 2>&1 && break; done
  docker info >/dev/null 2>&1 || fail "Docker Desktop is not running. Open Docker Desktop, wait until it shows 'Engine running', then run this again."
fi
say "  \033[32m[ok]\033[0m Docker is running"

# 3. Settings file (.env) with the API keys
if [ ! -f .env ]; then
  [ -f .env.example ] || fail "The file .env.example is missing. Download the app again."
  cp .env.example .env
  set_env POSTGRES_PASSWORD "$(new_password)"
  say "\n  \033[33mA settings file (.env) was created and will open now.\033[0m"
  say "  1) Paste your Google key right after  GOOGLE_API_KEY="
  say "  2) Paste your SerpApi key right after SERPAPI_KEY="
  say "  3) Save the file and close it.\n"
  edit_file .env
  read -r -p "  Press Enter here AFTER you have saved the file... "
fi

[ -n "$(env_value GOOGLE_API_KEY)" ] || { edit_file .env; fail "GOOGLE_API_KEY is empty. Paste your Google key after GOOGLE_API_KEY= in the .env file, save it, then run this again."; }
case "$(env_value POSTGRES_PASSWORD)" in ""|change-me) set_env POSTGRES_PASSWORD "$(new_password)";; esac
[ -n "$(env_value SERPAPI_KEY)" ] || say "  \033[33m[note]\033[0m SERPAPI_KEY is empty: ranking checks and top-10 reviews are unavailable."
say "  \033[32m[ok]\033[0m Settings file found"

# 4. Build and start
say "\n  Starting the app. The FIRST time this downloads and builds everything (5-15 minutes)."
docker compose up -d --build || fail "Docker could not start the app. Read the messages above, or see Troubleshooting in README.md."

# 5. Wait until it answers
say "\n  Waiting for the app to be ready..."
for _ in $(seq 1 90); do
  curl -fs http://localhost:8000/health >/dev/null 2>&1 && ready=1 && break
  sleep 2
done
[ "${ready:-}" = 1 ] || fail "The app did not start in time. Run this again; if it keeps failing, see Troubleshooting in README.md."

open_url "http://localhost:8000"
say "\n  \033[32mDONE.\033[0m The app is open in your browser: http://localhost:8000"
say "  It keeps running in the background. To stop it, run stop.command (Mac) or scripts/stop.sh.\n"
