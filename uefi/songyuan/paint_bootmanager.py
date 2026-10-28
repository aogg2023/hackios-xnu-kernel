import re, sys

# BootManager markers that hold forever:
#   magenta  PlatformBootManagerBeforeConsole (BDS reached the boot manager)
#   cyan     PlatformBootManagerAfterConsole  (console phase done; paints the GOP framebuffer too)
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


before = '''
  /* songyuan uefi marker: BDS reached the boot manager; hold magenta forever */
  for (;;) {
    volatile UINT32 *SongyuanBdsFb = (volatile UINT32 *)(UINTN)0xFC800000;
    for (UINTN SongyuanBdsI = 0; SongyuanBdsI < 0x40000; SongyuanBdsI++) {
      SongyuanBdsFb[SongyuanBdsI] = 0xFFFF00FF;
    }
  }
'''

after = '''
  /* songyuan uefi marker: console phase done; hold cyan on the GOP framebuffer forever */
  {
    volatile UINT32 *SongyuanFb = (volatile UINT32 *)(UINTN)0xFC800000;
    UINTN SongyuanCount = 0x40000;
    EFI_GRAPHICS_OUTPUT_PROTOCOL *SongyuanGop = NULL;
    if (!EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&SongyuanGop)) &&
        SongyuanGop != NULL && SongyuanGop->Mode != NULL) {
      SongyuanFb = (volatile UINT32 *)(UINTN)SongyuanGop->Mode->FrameBufferBase;
      SongyuanCount = (UINTN)SongyuanGop->Mode->FrameBufferSize / 4;
      if (SongyuanCount > 0x40000) {
        SongyuanCount = 0x40000;
      }
    }
    for (;;) {
      for (UINTN SongyuanI = 0; SongyuanI < SongyuanCount; SongyuanI++) {
        SongyuanFb[SongyuanI] = 0xFF00FFFF;
      }
    }
  }
'''

src, ok1 = insert_after_open(src, 'PlatformBootManagerBeforeConsole', before)
src, ok2 = insert_after_open(src, 'PlatformBootManagerAfterConsole', after)
if not ok1:
    sys.exit('BootManager.c: PlatformBootManagerBeforeConsole not found')
if not ok2:
    print('warning: PlatformBootManagerAfterConsole not found')
if '#include <Protocol/GraphicsOutput.h>' not in src:
    src = src.replace('#include <Library/PrintLib.h>', '#include <Library/PrintLib.h>\n#include <Protocol/GraphicsOutput.h>', 1)
open(PATH, 'w', encoding='utf-8', newline='\n').write(src)
print('boot manager paint inserted: before=%s after=%s' % (ok1, ok2))
