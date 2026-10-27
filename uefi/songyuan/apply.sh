#!/bin/bash
# Make Mu-Silicium in the checkout root: a clone of Project-Silicium/Mu-Silicium at the commit the
# port was made on, with the redwood port (uefi/files) plus the songyuan overlay (uefi/songyuan/files).
set -e
cd "$(dirname "$(readlink -f "$0")")/.."
MU=Mu-Silicium
[ -e $MU ] && { echo "uefi/songyuan/apply.sh: $MU already exists"; exit 1; }
git clone https://github.com/Project-Silicium/Mu-Silicium.git $MU
git -C $MU checkout -q "$(cat BASE)"
git -C $MU submodule update --init --recursive
git -C $MU apply --whitespace=nowarn "$PWD/mu-silicium.patch"
cp -r files/. $MU/
cp -r songyuan/files/. $MU/
echo "uefi/songyuan/apply.sh: done"
