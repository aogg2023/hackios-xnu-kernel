import re
import sys

# Bring-up markers inside UEFI. The hardcoded LK splash address may stop being the scanout as soon as
# a display driver configures its own GOP mode, so the after-console marker asks UEFI for the actual
# framebuffer the display driver installed and paints that; the before-console marker keeps the plain
# splash write (the display driver may not exist yet at that point).
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


before = ('\n  /* songyuan uefi marker: BDS reached the boot manager (splash buffer) */\n'
          '  *(volatile UINT32 *)(UINTN)0xFC800000 = 0xFFFF00FF;\n')

after = '''
  /* songyuan uefi marker: console phase done, paint the framebuffer the display driver installed */
  {
    EFI_GRAPHICS_OUTPUT_PROTOCOL *SongyuanGop = NULL;
    if (!EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&SongyuanGop)) &&
        SongyuanGop != NULL && SongyuanGop->Mode != NULL) {
      volatile UINT32 *SongyuanFb = (volatile UINT32 *)(UINTN)SongyuanGop->Mode->FrameBufferBase;
      UINTN SongyuanCount = (UINTN)SongyuanGop->Mode->FrameBufferSize / 4;
      if (SongyuanCount > 0x40000) {
        SongyuanCount = 0x40000;
      }
      for (UINTN SongyuanI = 0; SongyuanI < SongyuanCount; SongyuanI++) {
        SongyuanFb[SongyuanI] = 0xFF00FFFF;
      }
    }
    *(volatile UINT32 *)(UINTN)0xFC800000 = 0xFF00FFFF;
  }
'''

src, ok1 = insert_after_open(src, 'PlatformBootManagerBeforeConsole', before)
src, ok2 = insert_after_open(src, 'PlatformBootManagerAfterConsole', after)
if not ok1:
    sys.exit('BootManager.c: PlatformBootManagerBeforeConsole not found')
if not ok2:
    print('warning: PlatformBootManagerAfterConsole not found')

# the GOP protocol GUID and type
if '#include <Protocol/GraphicsOutput.h>' not in src:
    src = src.replace('#include <Library/PrintLib.h>', '#include <Library/PrintLib.h>\n#include <Protocol/GraphicsOutput.h>', 1)

open(PATH, 'w', encoding='utf-8', newline='\n').write(src)
print('boot manager paint inserted: before=%s after=%s' % (ok1, ok2))
