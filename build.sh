#!/bin/bash

# Stop build script on error
set -e

# syntax checking (pyflakes3 must be installed to build)
pyflakes3 src/rpi_wifi_setup/*.py

# code style checking (pycodestyle must be installed to build)
pycodestyle --max-line-length=300 src/rpi_wifi_setup/*.py

# Build
poetry -vvv build

