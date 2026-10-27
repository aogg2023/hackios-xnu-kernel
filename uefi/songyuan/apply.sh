#!/bin/bash
# Clone Mu-Silicium at the ported BASE into the repository root and apply the redwood port
# (uefi/files) plus the songyuan overlay (uefi/songyuan/files).
set -e
cd "$(dirname "$(readlink -f "$0")")/../.."
MU=Mu-Silicium
[ -e $MU ] && { echo "uefi/songyuan/apply.sh: $MU already exists"; exit 1; }
git clone https://github.com/Project-Silicium/Mu-Silicium.git $MU
git -C $MU checkout -q "$(cat uefi/BASE)"
git -C $MU submodule update --init --recursive
git -C $MU apply --whitespace=nowarn "$PWD/uefi/mu-silicium.patch"
cp -r uefi/files/. $MU/
cp -r uefi/songyuan/files/. $MU/
echo "uefi/songyuan/apply.sh: done at $PWD/$MU"
