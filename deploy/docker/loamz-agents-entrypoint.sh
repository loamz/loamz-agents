#!/bin/sh
# Start the Loamz agent server.
# If LOAMZ_ROLES is set, mounts only those feature slices (role-split mode).
# Otherwise mounts all roles.
set -eu

ROLES_ARG=""
if [ -n "${LOAMZ_ROLES:-}" ]; then
  ROLES_ARG="--roles ${LOAMZ_ROLES}"
fi

# The nsjail code sandbox creates a cgroup per run; a container sees /sys/fs/cgroup read-only unless it is remounted
# (needs the SYS_ADMIN the compose file grants). Best effort: without it the server just does not offer the code interpreter.
if [ "$(id -u)" = "0" ] && [ "${LOAMZ_AGENTS_SANDBOX__RUNTIME:-nsjail}" = "nsjail" ]; then
  mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true
fi

exec loamz-server agents serve ${ROLES_ARG} --host 0.0.0.0 --port "${PORT:-8000}"

