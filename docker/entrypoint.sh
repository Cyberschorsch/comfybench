#!/bin/sh
set -e

DATA="${COMFYBENCH_HOME:-/data}"
mkdir -p "$DATA"

# On first start, create the example config, prompts, workflows and input images.
# Existing files are never overwritten.
for item in config.yaml prompts.yaml README.md workflows inputs; do
  if [ ! -e "$DATA/$item" ]; then
    cp -R "/app/defaults/$item" "$DATA/$item"
    echo "comfybench: created $item in $DATA"
  fi
done

exec python /app/bench.py "$@"
