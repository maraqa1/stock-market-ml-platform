#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/massa/stock-market-ml-platform"

cp "${ROOT}/deployment/systemd/stockml-price-history-nightly.service" /etc/systemd/system/stockml-price-history-nightly.service
cp "${ROOT}/deployment/systemd/stockml-price-history-nightly.timer" /etc/systemd/system/stockml-price-history-nightly.timer

systemctl daemon-reload
systemctl enable stockml-price-history-nightly.timer
systemctl restart stockml-price-history-nightly.timer

systemctl list-timers --all | grep stockml-price-history-nightly || true
systemctl status stockml-price-history-nightly.timer --no-pager
