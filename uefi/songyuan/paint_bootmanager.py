import re
import sys

# Bring-up markers inside UEFI itself. The shim paints before the jump; these paint from DXE/BDS so
# the panel tells how far the firmware got even though it has no console driver for our panel:
#   magenta  at PlatformBootManagerBeforeConsole (DXE core + BDS reach BootManager)
#   cyan     at PlatformBootManagerAfterConsole  (the boot manager finished the console phase)
# The framebuffer is the LK splash at 0xFC800000; DXE identity-maps RAM, so a plain store works.
PATH = sys.argv[1]
src = open(PATH, encoding='utf-8', errors='replace').read()

MARK = 'songyuan uefi marker'

if MARK in src:
    print('boot manager paint already inserted')
    sys.exit(0)


def insert_after_open(text, func, body):
    m = re.search(r'\b' + re.escape(func) + r'\s*\(\s*\)\s*\n\s*\{', text)
    if not m:
        return text, False
    return text[:m.end()] + body + text[m.end():], True


body1 = ('\n  /* songyuan uefi marker: BDS reached the boot manager */\n'
         '  *(volatile UINT32 *)(UINTN)0xFC800000 = 0xFFFF00FF;\n')
body2 = ('\n  /* songyuan uefi marker: console phase done */\n'
         '  *(volatile UINT32 *)(UINTN)0xFC800000 = 0xFF00FFFF;\n')

src, ok1 = insert_after_open(src, 'PlatformBootManagerBeforeConsole', body1)
src, ok2 = insert_after_open(src, 'PlatformBootManagerAfterConsole', body2)
if not ok1:
    sys.exit('BootManager.c: PlatformBootManagerBeforeConsole not found')
if not ok2:
    print('warning: PlatformBootManagerAfterConsole not found')
open(PATH, 'w', encoding='utf-8', newline='\n').write(src)
print('boot manager paint inserted: before=%s after=%s' % (ok1, ok2))
