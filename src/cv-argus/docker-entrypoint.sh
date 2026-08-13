#!/usr/bin/env sh
# Hands off to whatever CMD was requested (currently `python -m cv_argus.main`).
#
# Open question, deliberately not decided here yet: once model/ (the gdown-based
# downloader) exists, should fetching/refreshing the trained model from Google Drive happen
# in this script before `exec`'ing CMD — so a new trained model only needs a container
# restart, not an image rebuild — or somewhere else (e.g. inside main() itself)? Revisit
# once model/ is built.
set -e

exec "$@"
