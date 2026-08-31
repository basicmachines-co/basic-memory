#!/usr/bin/env bash
set -euo pipefail

# BEAM benchmark data (CC BY-SA 4.0) is never vendored into this repo.
# The upstream repo carries the data in-tree, so a sparse blobless clone of
# just the needed tiers is the minimal fetch. Destination is gitignored.

dest="benchmarks/datasets/beam/upstream"

if [ ! -d "$dest/.git" ]; then
  git clone --filter=blob:none --sparse https://github.com/mohammadtavakoli78/BEAM "$dest"
fi
git -C "$dest" sparse-checkout set chats/100K chats/500K

echo "BEAM 100K/500K tiers available under $dest/chats"
echo "Add more tiers with: git -C $dest sparse-checkout add chats/1M"
