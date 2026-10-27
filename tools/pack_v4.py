"""Repack the mkxnuimg v1/gzip boot image as a header-v4 boot image with a raw arm64 Image.

Our device's stock boot_a/b are header v4 (Android 13+ layout, kernel is a raw arm64 Image), and our
own working loader image was packed that way too. A v1 image with a gzip kernel is what mkxnuimg.py
produces for redwood; on this phone `fastboot boot` accepts it but nothing of ours ever executes
(no paint from BootShim, no USB), so the ABL does not run the decompressed shim the way v1 assumes.

Layout written here, same as the Image our Loader already booted from:
  0x000  64-byte arm64 Image header: first instruction `b +0x800`, text_offset 0,
         image_size = 0x800 + blob, flags 0xA, "ARM\\x64" at 0x38
  0x800  blob = BootShim.bin + UEFI FD + XNUP payload + DTB (mkxnuimg's decompressed kernel)
then the v4 boot image header (1584 bytes, 4096-byte pages, kernel only).

usage: pack_v4.py <mkxnuimg-boot.img> <out-v4.img>
"""
import struct
import sys
import zlib

PAGE = 4096
HEADER_OFF = 0x800


def main():
    src = open(sys.argv[1], 'rb').read()
    if src[:8] != b'ANDROID!':
        raise SystemExit('not an Android boot image')
    ks, = struct.unpack_from('<I', src, 8)
    page, hver = struct.unpack_from('<I', src, 36)[0], struct.unpack_from('<I', src, 40)[0]
    if hver != 1:
        raise SystemExit(f'unexpected header version {hver} (expected the v1 image mkxnuimg writes)')
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    blob = dec.decompress(src[page:page + ks]) + dec.flush() + dec.unused_data
    if blob[:4] != b'\x81\x0b\x00\x10':
        print('warning: blob does not start with the BootShim prologue', file=sys.stderr)

    image_size = HEADER_OFF + len(blob)
    hdr = bytearray(HEADER_OFF)
    struct.pack_into('<I', hdr, 0x00, 0x14000000 | (HEADER_OFF // 4))  # b +0x800
    struct.pack_into('<Q', hdr, 0x08, 0)                              # text_offset
    struct.pack_into('<Q', hdr, 0x10, image_size)                     # image_size
    struct.pack_into('<Q', hdr, 0x18, 0xA)                            # flags: 4K pages, any placement
    hdr[0x38:0x3C] = b'ARM\x64'
    image = bytes(hdr) + blob

    boot = struct.pack('<8sIIII4II1536sI', b'ANDROID!', len(image), 0, 0, 1584,
                       0, 0, 0, 0, 4, b'', 0)
    assert len(boot) == 1584
    pad = lambda b: b + b'\0' * (-len(b) % PAGE)
    open(sys.argv[2], 'wb').write(pad(boot) + pad(image))
    print('v4 image: kernel %d bytes (blob %d), -> %s' % (len(image), len(blob), sys.argv[2]))


if __name__ == '__main__':
    main()
