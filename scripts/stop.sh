#!/usr/bin/env bash
# Stops Local SEO Audit on Mac or Linux. Your data is kept.
cd "$(dirname "$0")/.." || exit 1
printf '\n  Stopping Local SEO Audit...\n'
if docker compose down; then
  printf '\n  Stopped. Your projects and results are kept; run start again to restart.\n\n'
else
  printf '\n  Could not stop the app. Is Docker Desktop running?\n\n'
fi
