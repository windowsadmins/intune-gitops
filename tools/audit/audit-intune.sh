#!/usr/bin/env bash
# Run the read-only Intune audit suite as the identity `az` is signed in as.
#
#   ./audit-intune.sh                         # all domains
#   ./audit-intune.sh assignments             # one domain
#   ./audit-intune.sh apps compliance --json
#
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! az account show >/dev/null 2>&1; then
  echo "Not signed in. Run: az login --tenant <your-tenant-id>" >&2
  exit 1
fi

cd "$here"
exec python3 -m intune_audit "$@"
