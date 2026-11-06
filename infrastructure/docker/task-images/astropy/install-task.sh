#!/bin/sh
set -e
cd /workspace
if [ ! -f astropy/_version.py ]; then
  printf '%s\n' 'version = "0.0.0"' > astropy/_version.py
fi
pip install -i https://mirrors.aliyun.com/pypi/simple --no-build-isolation -e /workspace
