import re
import sys

# Hook the XNU loader into the upstream BootManagerLib (evidence: uefi/mu-silicium.patch adds
# "#include \"XnuLoader.h\"" and calls XnuLoaderMain() at the start of
# PlatformBootManagerUnableToBoot, and lists XnuLoader.c in BootManagerLib.inf [Sources]).
bm, inf = sys.argv[1], sys.argv[2]

src = open(bm, encoding='utf-8', errors='replace').read()
if 'XnuLoader.h' not in src:
    if '#include "BootManager.h"' not in src:
        sys.exit(bm + ': #include "BootManager.h" not found')
    src = src.replace('#include "BootManager.h"', '#include "BootManager.h"\n#include "XnuLoader.h"', 1)

if 'XnuLoaderMain' not in src:
    m = re.search(r'PlatformBootManagerUnableToBoot\s*\(\s*\)\s*\n\s*\{', src)
    if not m:
        sys.exit(bm + ': PlatformBootManagerUnableToBoot not found')
    src = (src[:m.end()]
           + "\n  // Nothing else to boot: hand over to the XNU loader (does not return)\n  XnuLoaderMain ();\n"
           + src[m.end():])

open(bm, 'w', encoding='utf-8', newline='\n').write(src)

i = open(inf, encoding='utf-8', errors='replace').read()
if 'XnuLoader.c' not in i:
    if '  BootManager.c' not in i:
        sys.exit(inf + ': [Sources] BootManager.c not found')
    i = i.replace('  BootManager.c', '  BootManager.c\n  XnuLoader.c', 1)
    open(inf, 'w', encoding='utf-8', newline='\n').write(i)

print('XnuLoader hook inserted into', bm, 'and', inf)
