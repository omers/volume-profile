#!/usr/bin/env bash
# Builds deploy/build/package: executor.py, swing_frvp.py and their Linux arm64 wheels for Lambda (Python 3.12).
# Run it before `terraform apply`; Terraform zips the folder.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build/package
mkdir -p build/package
uv pip install --quiet --target build/package --python-version 3.12 \
  --python-platform aarch64-manylinux_2_28 --only-binary :all: -r requirements.txt
cp executor.py ../swing_frvp.py build/package/
find build/package -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf build/package/*.dist-info/RECORD
du -sh build/package
