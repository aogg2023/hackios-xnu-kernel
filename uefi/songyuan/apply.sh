#!/bin/bash
# Make ../Mu-Silicium: a clone of Project-Silicium/Mu-Silicium at the commit the port was made on,
# with the redwood port (uefi/files) plus the songyuan overlay (uefi/songyuan/files) put into it.
set -e
cd "$(dirname "$(readlink -f "$0")")/.."
MU=../Mu-Silicium
[ -e $MU ] && { echo "uefi/songyuan/apply.sh: $MU already exists"; exit 1; }
git clone https://github.com/Project-Silicium/Mu-Silicium.git $MU
git -C $MU checkout -q "$(cat uefi/BASE)"
git -C $MU submodule update --init --recursive
git -C $MU apply --whitespace=nowarn "$PWD/uefi/mu-silicium.patch"
cp -r uefi/files/. $MU/
cp -r uefi/songyuan/files/. $MU/
echo "uefi/songyuan/apply.sh: done"
