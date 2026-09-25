#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec flock -n /tmp/shiptracking-worker.lock .venv/bin/python shipment_update_flagging_workflow.py
