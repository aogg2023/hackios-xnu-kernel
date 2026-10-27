import sys

# Diagnostic paint in Mu-Silicium's BootShim.S, applied after uefi/mu-silicium.patch.
# The shim runs with the MMU off, and LK left the splash framebuffer at a fixed physical address,
# so plain stores work. A green area on the panel proves the shim ran; if the UEFI image then hangs
# (no USB device appears), the fault is inside UEFI, not in the boot chain.
path = sys.argv[1]
src = open(path, encoding='utf-8', errors='replace').read()

MARK = 'songyuan diagnostic'
block = '''  /* songyuan diagnostic: paint the LK splash so the screen shows the shim ran */
_ShimPaint:
  ldr   x9, =0xFC800000
  movz  w10, #0xFF00
  movk  w10, #0xFF00, lsl #16
  add   x11, x9, #0x100000
1:
  str   w10, [x9], #4
  cmp   x9, x11
  b.ne  1b
  b     _Entry

'''

if MARK in src:
    print('paint already inserted')
    sys.exit(0)

needle = '_Entry:\n  br    x5'
if needle not in src:
    sys.exit('BootShim.S: entry block not found')
src = src.replace(needle, block + '_Entry:\n  br    x5', 1)

src = src.replace('''  cmp   x4, x5
  beq   _Entry''', '''  cmp   x4, x5
  beq   _ShimPaint''', 1)
src = src.replace('''  ldr   x5, =FD_BASE

_Entry:''', '''  ldr   x5, =FD_BASE
  b     _ShimPaint

_Entry:''', 1)

if '.ltorg' not in src:
    src += '\n.ltorg\n'
open(path, 'w', encoding='utf-8', newline='\n').write(src)
print('paint inserted')
