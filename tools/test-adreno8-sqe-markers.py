#!/usr/bin/env python3
"""Check production SQE RAM marker order with STR/DSB executed by the host MMIO model."""
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

fixture = runpy.run_path(str(Path(__file__).with_name("test-adreno8-gpu.py")))
dev, args = fixture["DEV"], fixture["args"]
common = (dev / "adreno8.c").read_text()
raw = re.search(r'void __attribute__\(\(noinline\)\)\nadreno8_wr\(.*?\n}', common, re.S)[0]
put = re.search(r'void\nadreno8_put\(.*?\n}', common, re.S)[0]
hold = re.search(r'void\nadreno8_hold\(.*?\n}', common, re.S)[0]
store = '__asm__ volatile ("str %w0, [%1]" : : "r" (value), "r" (address) : "memory");'
assert raw.count(store) == 1
raw = raw.replace(store, "host_store(address, value);")
production = (dev / "adreno8_gpu.c").read_text()


def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after, 1)


source = fixture["source"].split("int main(int argc, char **argv)", 1)[0]
source = replace_once(source, "#include <assert.h>", "#include <assert.h>\n#include <setjmp.h>")
spies = r'''
static unsigned marker_count, marker_stall, marker_last_action, marker_pause_count;
static bool marker_scope_only, marker_expect_pause;
static jmp_buf marker_jump;
static unsigned marker_values[6];
static uint32_t adreno8_fine;
static bool adreno8_quiet;
static char adreno8_last_write[96];
static void marker_push(unsigned value) {
    assert(marker_count < 6 && value == marker_count + 1);
    marker_values[marker_count++] = value;
}
void adreno8_sleep_ms(uint32_t ms) {
    assert(ms == adreno8_pause_ms);
    marker_pause_count++; marker_last_action = 1;
}
static void adreno8_need(struct adreno8_block *block) { assert(block->base); }
'''
source = replace_once(source, "int adreno8_say(const char *fmt, ...) {", spies + r'''
int adreno8_say(const char *fmt, ...) {
    assert(!marker_count || marker_count >= 5);
    marker_last_action = 0;
''')
source = replace_once(source, "void loader_log_line(const char *line) { (void)line; }", "void loader_log_line(const char *line);")
source = replace_once(source, "void adreno8_hold(bool single_access) { (void)single_access; }", hold)
source = replace_once(source, "void adreno8_settle(void) { last_mmio = MMIO_BARRIER; barriers++; }", r'''
void adreno8_settle(void) {
    if (marker_count == 3) {
        if (marker_stall == 2) longjmp(marker_jump, 1);
        marker_push(4);
    }
    last_mmio = MMIO_BARRIER; barriers++;
}''')
source = replace_once(source, "void adreno8_wr(vm_offset_t address, uint32_t value)\n{", r'''
static void host_store(vm_offset_t address, uint32_t value)
{
    if (marker_scope_only) { assert(!marker_count); return; }
    if (adreno8_direct_ring && address == adreno8_gpu.base + 0x2054) {
        marker_push(2);
        if (marker_stall == 1) longjmp(marker_jump, 1);
    }
    marker_last_action = 0;
''')
old_put = re.search(r'void adreno8_put\(struct adreno8_block \*block,.*?\n}', source, re.S)[0]
source = replace_once(source, old_put, raw + '\n#define printf adreno8_say\n' + put + '\n#undef printf')
source = replace_once(source, "    if (reg == GEN8_CP_APERTURE_CNTL_HOST) return current_pipe << 12;", r'''
    if (adreno8_direct_ring && reg == 0x815 && marker_count) marker_push(6);
    if (reg == GEN8_CP_APERTURE_CNTL_HOST) return current_pipe << 12;''')
source += r'''
void loader_log_line(const char *line) {
    assert(adreno8_direct_ring && adreno8_gpu.base && !marker_scope_only);
    if (!strcmp(line, "adreno8: ordinary SQE raw STR enter\n")) {
        if (marker_expect_pause) assert(marker_last_action == 1 && marker_pause_count);
        marker_push(1);
    } else if (!strcmp(line, "adreno8: ordinary SQE raw STR returned\n")) marker_push(3);
    else if (!strcmp(line, "adreno8: ordinary SQE HALT DSB returned\n")) {
        assert(raw_nop_asked == 2 && last_mmio == MMIO_BARRIER); marker_push(5);
    } else if (!strcmp(line, "adreno8: ordinary SQE start DSB returned\n")) {
        assert(raw_nop_asked == 0 && last_mmio == MMIO_BARRIER); marker_push(5);
    } else assert(false);
}
static void prepare_markers(unsigned mode, unsigned pause, unsigned fine, bool quiet) {
    marker_count = marker_stall = marker_last_action = marker_pause_count = 0;
    marker_scope_only = marker_expect_pause = false;
    prepare_ring(); raw_nop_asked = mode;
    adreno8_pause_ms = pause; adreno8_fine = fine; adreno8_quiet = quiet;
    marker_expect_pause = pause && fine && !quiet;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    for (unsigned i = 0; i < 2; i++) {
        FILE *f = fopen(argv[i + 1], "rb"); assert(f);
        assert(!fseek(f, 0, SEEK_END)); file_sizes[i] = ftell(f); rewind(f);
        files[i] = malloc(file_sizes[i]); assert(files[i]);
        assert(fread(files[i], 1, file_sizes[i], f) == file_sizes[i]); fclose(f);
    }
    for (unsigned mode = 0; mode <= 2; mode += 2)
        for (unsigned pause = 0; pause <= 100; pause += 100)
            for (unsigned fine = 0; fine < 2; fine++) for (unsigned quiet = 0; quiet < 2; quiet++) {
                prepare_markers(mode, pause, fine, quiet);
                assert(!adreno8_gpu_cp_init()); ring_retained(mode == 0);
                assert(marker_count == 6 && ring_sqe_writes == 1);
                for (unsigned i = 0; i < 6; i++) assert(marker_values[i] == i + 1);
                if (quiet || !pause || !fine) assert(!marker_pause_count);
            }
    prepare_markers(3, 100, 1, false);
    assert(!adreno8_gpu_cp_init()); ring_retained(0); assert(!marker_count && !ring_sqe_writes);
    for (unsigned mode = 0; mode <= 2; mode += 2) {
        prepare_markers(mode, 100, 1, false); ring_drop_write = 0x815;
        assert(!adreno8_gpu_cp_init()); ring_retained(0);
        assert(marker_count == 6 && !ring_wptr_writes && !ring_payloads);
        for (unsigned stop = 1; stop <= 2; stop++) {
            prepare_markers(mode, 100, 1, false); marker_stall = stop;
            if (!setjmp(marker_jump)) { (void)adreno8_gpu_cp_init(); assert(false); }
            assert(marker_count == (stop == 1 ? 2u : 3u));
            assert(!ring_wptr_writes && !ring_payloads && allocation_count == 5 && adreno8_reset_required);
        }
        prepare_markers(mode, 100, 1, false); ring_publish_fail = 1;
        assert(!adreno8_gpu_cp_init()); ring_retained(0); assert(!marker_count);
    }
    marker_count = 0; marker_stall = 0; marker_expect_pause = false;
    for (unsigned mode = 0; mode < 4; mode++) {
        reset(); prepare(); raw_nop_asked = mode;
        (void)adreno8_gpu_cp_init(); assert(!marker_count);
    }
    marker_scope_only = true;
    for (unsigned direct = 0; direct < 2; direct++) {
        adreno8_direct_ring = direct;
        adreno8_wr(adreno8_gpu.base + 0x2050, 1);
        adreno8_wr(adreno8_gpu.base + 0x2058, 1);
        adreno8_wr(adreno8_gmu.base + 0x2054, 1);
        if (!direct) adreno8_wr(adreno8_gpu.base + 0x2054, 1);
    }
    vm_offset_t mapped = adreno8_gpu.base; adreno8_gpu.base = 0;
    adreno8_wr(0x2054, 1); assert(!marker_count); adreno8_gpu.base = mapped;
    marker_scope_only = false; marker_expect_pause = false;
    reset(); free(files[0]); free(files[1]);
    puts("SQE RAM markers: raw STR/pause/DSB/read ordering, interrupted operations, failed readback and scope: PASS");
}
'''
source = replace_once(source, '    for (unsigned mode = 0; mode <= 2; mode += 2)\n',
                      fixture['table_checks'] + '\n    for (unsigned mode = 0; mode <= 2; mode += 2)\n')
raw_pre = 'if (ordinary_sqe) loader_log_line("adreno8: ordinary SQE raw STR enter\\n");'
raw_post = 'if (ordinary_sqe) loader_log_line("adreno8: ordinary SQE raw STR returned\\n");'
halt = 'loader_log_line("adreno8: ordinary SQE HALT DSB returned\\n");'
start = 'loader_log_line("adreno8: ordinary SQE start DSB returned\\n");'
mutations = [
    ("pre-missing", "raw", raw_pre, ""),
    ("post-missing", "raw", raw_post, ""),
    ("pre-after-store", "raw", raw_pre + '\n\thost_store(address, value);', 'host_store(address, value);\n\t' + raw_pre),
    ("post-before-store", "raw", '\thost_store(address, value);\n\t' + raw_post, '\t' + raw_post + '\n\thost_store(address, value);'),
    ("HS-marked", "raw", 'adreno8_direct_ring && ', ''),
    ("unmapped-marked", "raw", 'adreno8_gpu.base && ', ''),
    ("wrong-address", "raw", '+ 0x2054', '+ 0x2058'),
    ("all-registers-marked", "raw", 'address == adreno8_gpu.base + 0x2054', '(address != 0)'),
    ("console-pre", "raw", raw_pre, raw_pre.replace('loader_log_line', 'adreno8_say')),
    ("console-post", "raw", raw_post, raw_post.replace('loader_log_line', 'adreno8_say')),
    ("halt-missing", "gpu", halt, ''),
    ("start-missing", "gpu", start, ''),
    ("halt-early", "gpu", '\t\t\tadreno8_settle();\n\t\t\t' + halt, '\t\t\t' + halt + '\n\t\t\tadreno8_settle();'),
    ("start-early", "gpu", '\tadreno8_settle();\n\t' + start, '\t' + start + '\n\tadreno8_settle();'),
    ("halt-barrier-missing", "gpu", '\t\t\tadreno8_settle();\n\t\t\t' + halt, '\t\t\t' + halt),
    ("start-barrier-missing", "gpu", '\tadreno8_settle();\n\t' + start, '\t' + start),
    ("halt-wrong-label", "gpu", halt, start),
    ("start-wrong-label", "gpu", start, halt),
    ("HALT-after-read", "gpu", halt + '\n\t\t\tif (gpu_read(GEN8_CP_SQE_CNTL) != 3)\n\t\t\t\treturn gpu_fail("ordinary SQE HALT readback");',
     'if (gpu_read(GEN8_CP_SQE_CNTL) != 3)\n\t\t\t\treturn gpu_fail("ordinary SQE HALT readback");\n\t\t\t' + halt),
    ("start-after-read", "gpu", start + '\n\tif (gpu_read(GEN8_CP_SQE_CNTL) != 1)\n\t\treturn gpu_fail("direct ring SQE enable readback");',
     'if (gpu_read(GEN8_CP_SQE_CNTL) != 1)\n\t\treturn gpu_fail("direct ring SQE enable readback");\n\t' + start),
    ("access-pause-missing", "put", 'adreno8_hold(true);', ''),
]
with tempfile.TemporaryDirectory(prefix="adreno8-sqe-markers-") as temp:
    variants = [("production", source, production)]
    for label, owner, before, after in mutations:
        body = {"raw": raw, "put": put, "gpu": production}[owner]
        changed = replace_once(body, before, after)
        variants.append((label, replace_once(source, body, changed) if owner != "gpu" else source,
                         changed if owner == "gpu" else production))
    for label, code, gpu in variants:
        path = Path(temp) / label; path.mkdir()
        (path / "gpu.c").write_text(code); (path / "adreno8_gpu.c").write_text(gpu)
        for page in (4096, 16384):
            binary = path / f"markers-{page}"
            subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                            "-fsanitize=address,undefined", f"-DPAGE_SIZE={page}", "-I", str(dev),
                            str(path / "gpu.c"), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary), str(args.firmware_dir / "gen80000_sqe.fw"),
                                     str(args.firmware_dir / "gen80000_aqe.fw")], capture_output=True, text=True)
            if label == "production":
                assert result.returncode == 0, result.stdout + result.stderr
                print(f"SQE RAM markers PAGE_SIZE={page}: PASS")
            else:
                assert result.returncode != 0 and 'assert' in result.stderr.lower(), (label, page, result.stderr)
print(f"SQE RAM marker mutations rejected: {len(mutations)} variants x 2 page sizes")
