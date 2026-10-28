import re, sys

# Stage markers: fill 1 MiB of the LK splash with a colour and hold it for a while so a human can
# read the sequence on the panel (the phone has no serial). Colours: red=PEI, yellow=DXE core.
stage, path = sys.argv[1], sys.argv[2]
src = open(path, encoding='utf-8', errors='replace').read()
MARK = 'songyuan stage marker'
if MARK in src:
    print('stage marker already present')
    sys.exit(0)

COLOURS = {'pei': '0xFFFF0000', 'dxe': '0xFFFFFF00'}
if stage not in COLOURS:
    sys.exit('unknown stage ' + stage)

m = re.search(r'PlatformPeim\s*\(', src) if stage == 'pei' else re.search(r'DxeMain\s*\(', src)
if not m:
    sys.exit(path + ': entry not found for stage ' + stage)
brace = src.find('{', m.end())
if brace < 0:
    sys.exit(path + ': body brace not found for stage ' + stage)

body = '''
  /* songyuan stage marker: %s entry; fill 1 MiB and hold it so the colour is visible */
  {
    volatile UINT32 *SongyuanStageFb = (volatile UINT32 *)(UINTN)0xFC800000;
    for (UINTN SongyuanStageI = 0; SongyuanStageI < 0x40000; SongyuanStageI++) {
      SongyuanStageFb[SongyuanStageI] = %s;
    }
    for (volatile UINTN SongyuanSpin = 0; SongyuanSpin < 0x06000000; SongyuanSpin++) { }
  }
''' % (stage, COLOURS[stage])
src = src[:brace + 1] + body + src[brace + 1:]
open(path, 'w', encoding='utf-8', newline='\n').write(src)
print(stage + ' marker inserted at ' + path + ':' + str(brace + 1))
