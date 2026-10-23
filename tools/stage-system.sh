#!/usr/bin/env bash
# 变体 C 的暂存步：把 userland/system.apfs 写到 userdata 尾部（loader 的 rdfile tail 从
# 分区末端往回读，与这里算法一致：start = total - size）。userdata 头部是 f2fs，不动，
# 用户 Android 的 /data 不受影响。注意：f2fs 之后可能把新数据分配到尾部块——每次试
# variant C 前重跑本脚本即可（约 1-2 分钟）。
# 前置：设备在 Android（slot b），KernelSU 已授权 adb shell su。
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
# 持久化门（2026-10-23 评审+用户指示）：这是对 userdata 的唯一写入，事先不校验
# 目标块是否被 f2fs 占用，且备份不含 userdata。默认拒跑，确认过才放行。
if [ "${1:-}" != "--yes" ]; then
  echo "⚠ 将向 userdata 尾部写 ~2.4G（唯一持久化点，未校验块占用，备份不含 userdata）。"
  echo "  当前策略=调试限 RAM。确认要写请带 --yes 重跑。" >&2
  exit 1
fi
SZ=$(stat -c %s userland/system.apfs 2>/dev/null || stat -f %z userland/system.apfs)
TOTAL=247904317440   # userdata，annibale GPT（annibale-backup/20261022-2355/partitions.txt）；HyperOS 更新后先 diff
START=$(( TOTAL - SZ ))                    # size 是 4096 对齐的，无需再取整
SEEK=$(( START / 4096 ))
[ $(( SZ % 4096 )) -eq 0 ] || { echo "system.apfs not 4096-aligned"; exit 1; }
adb -s ${ANDROID_SERIAL} wait-for-device
adb -s ${ANDROID_SERIAL} push userland/system.apfs /data/local/tmp/system.apfs
adb -s ${ANDROID_SERIAL} shell su -c "dd if=/data/local/tmp/system.apfs of=/dev/block/by-name/userdata bs=4096 seek=$SEEK conv=notrunc status=none"
adb -s ${ANDROID_SERIAL} shell su -c "rm -f /data/local/tmp/system.apfs"
echo "staged $SZ bytes at userdata+$START (loader: rdfile=userdata:$SZ:tail)"
