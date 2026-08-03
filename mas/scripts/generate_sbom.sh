#!/usr/bin/env sh
set -eu

# The release runner supplies syft. Keeping generation as a small script makes
# local and CI output use the same CycloneDX contract without bundling syft in
# a production image.
if ! command -v syft >/dev/null 2>&1; then
  echo "syft is required to generate the release SBOM" >&2
  exit 1
fi

output="${1:-build/aiat-sbom.cdx.json}"
mkdir -p "$(dirname "$output")"
syft dir:. --source-name aiat --output "cyclonedx-json=${output}"
echo "wrote ${output}"
