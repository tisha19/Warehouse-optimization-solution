#!/usr/bin/env bash
# Pull the verdict out of the Ultra TP=4 test log, minus the loader noise.
log=${1:?usage: ultra_tp4_result.sh <logfile>}
grep -vE 'routed_experts|Loading safetensors|AutoTuner|Capturing CUDA|Pull complete|Download complete|Verifying Checksum|Waiting|Downloading' "$log" \
  | sed -n '/=== 3. waiting/,$p'
