#!/usr/bin/env bash
# Confirm the Ultra serving job will survive disconnects and for how long.
export PATH=/cm/local/apps/slurm/current/bin:$PATH
export SLURM_CONF=/cm/shared/apps/slurm/etc/slurm/slurm.conf
id=${1:?usage: ultra_status.sh <jobid>}
scontrol show job "$id" | grep -oE '(JobState|RunTime|TimeLimit|EndTime|NodeList|Requeue)=[^ ]*'
echo "--- endpoint ---"
srun --jobid="$id" --overlap curl -s -o /dev/null -w 'health=%{http_code}\n' -m 10 \
  http://127.0.0.1:8000/v1/health/ready
