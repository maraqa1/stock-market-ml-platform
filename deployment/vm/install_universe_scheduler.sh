#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/massa/stock-market-ml-platform"
SERVICE_SRC="${ROOT}/deployment/systemd/stockml-universe-nightly.service"
TIMER_SRC="${ROOT}/deployment/systemd/stockml-universe-nightly.timer"

test -f "$SERVICE_SRC"
test -f "$TIMER_SRC"

cp "$SERVICE_SRC" /etc/systemd/system/stockml-universe-nightly.service
cp "$TIMER_SRC" /etc/systemd/system/stockml-universe-nightly.timer

systemctl daemon-reload
systemctl enable stockml-universe-nightly.timer
systemctl restart stockml-universe-nightly.timer

systemctl list-timers --all | grep stockml-universe-nightly || true
systemctl status stockml-universe-nightly.timer --no-pager
