import re
import sys

# Stage markers telling how far the UEFI image gets. Each writes a distinct colour into the LK splash
# (the only output channel before a display driver is up):
#   red     PEI entry      (PlatformPeim in Silicon/Qualcomm/QcomPkg/Library/PlatformPeiLib/PlatformPei.c)
#   yellow  DXE core entry (DxeMain in Mu_Basecore/MdeModulePkg/Core/Dxe/DxeMain/DxeMain.c)
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

if stage == 'pei':
    m = re.search(r'PlatformPeim\s*\(', src)
else:
    m = re.search(r'DxeMain\s*\(', src)
if not m:
    sys.exit(f'{path}: entry not found for stage {stage}')

brace = src.find('{', m.end())
if brace < 0:
    sys.exit(f'{path}: body brace not found for stage {stage}')

body = (f'\n  /* songyuan stage marker: {stage} entry (fill 1 MiB of the splash) */\n'
        f'  {{ volatile UINT32 *SongyuanStageFb = (volatile UINT32 *)(UINTN)0xFC800000;\n'
        f'    for (UINTN SongyuanStageI = 0; SongyuanStageI < 0x40000; SongyuanStageI++) {{\n'
        f'      SongyuanStageFb[SongyuanStageI] = {COLOURS[stage]}; }} }}\n')
src = src[:brace + 1] + body + src[brace + 1:]
open(path, 'w', encoding='utf-8', newline='\n').write(src)
print(f'{stage} marker inserted at {path}:{brace + 1}')
