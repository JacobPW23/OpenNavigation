#!/usr/bin/env bash
set -euo pipefail
kafka-topics.sh --bootstrap-server localhost:9092 --list
