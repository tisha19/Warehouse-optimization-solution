#!/usr/bin/env bash
# Cancel the stack job (containers are removed by the job's EXIT trap).
ID=$(squeue -u "$USER" -h -n warehouse-stack -o "%i" | head -1)
[ -n "$ID" ] && { scancel "$ID"; echo "cancelled job $ID"; } || echo "no warehouse-stack job running"
