import sys

# Stage markers that HANG the firmware at the deepest stage it reached, so the panel colour can be
# read at leisure (no serial, no timing window):
#   red     PEI entry      (PlatformPeim)
#   yellow  DXE core entry (DxeMain)
#   magenta/cyan  BootManager before/after console (paint_bootmanager.py)
# usage: paint_stages.py <pei|dxe> <file>
stage, path = sys.argv[1], sys.argv[2]
src = open(path, encoding='utf-8', errors='replace').read()
MARK = 'songyuan stage marker'
if MARK in src:
    print('stage marker already present')
    sys.exit(0)

COLOURS = {'pei': '0xFFFF0000', 'dxe': '0xFFFFFF00'}
if stage not in COLOURS:
    sys.exit('unknown stage ' + stage)

import re
m = re.search(r'PlatformPeim\s*\(', src) if stage == 'pei' else re.search(r'DxeMain\s*\(', src)
if not m:
    sys.exit(path + ': entry not found for stage ' + stage)
brace = src.find('{', m.end())
if brace < 0:
    sys.exit(path + ': body brace not found for stage ' + stage)

body = '''
  /* songyuan stage marker: %s entry; hold this colour forever so the panel shows how far we got */
  for (;;) {
    volatile UINT32 *SongyuanStageFb = (volatile UINT32 *)(UINTN)0xFC800000;
    for (UINTN SongyuanStageI = 0; SongyuanStageI < 0x40000; SongyuanStageI++) {
      SongyuanStageFb[SongyuanStageI] = %s;
    }
  }
''' % (stage, COLOURS[stage])
src = src[:brace + 1] + body + src[brace + 1:]
open(path, 'w', encoding='utf-8', newline='\n').write(src)
print(stage + ' marker inserted at ' + path + ':' + str(brace + 1))
