#!/usr/bin/env python3
"""Exercise the six GPUCC probe branches through the production stage5 C model."""
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

fixture = runpy.run_path(str(Path(__file__).with_name("test-adreno8-config.py")))
source = fixture["source"].split("int main(void) {", 1)[0]
production = fixture["driver"]
probe = re.search(r"bool\nadreno8_step_gpucc\(void\).*?\n}", production, re.S).group()


def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after, 1)


spies = r'''
static const uint32_t expected_offsets[6] = {0x93a4, 0x9008, 0x9064, 0x93a8, 0x9004, 0x90cc};
static const char *const expected_names[6] = {"cb", "cxo_aon", "gx_ahb_ff", "rscc_hub_aon", "rscc_xo_aon", "sleep"};
static unsigned always_reads, always_writes, always_scan, always_write_mask, always_settles;
static unsigned always_pending = 6, always_fault;
static int always_fault_index = -1;
static bool always_probe;
static int always_index(uint32_t off) {
    for (unsigned i = 0; i < 6; i++) if (expected_offsets[i] == off) return (int)i;
    return -1;
}
'''
source = replace_once(source, "static bool clock_boot_ok = true;", spies + "\nstatic bool clock_boot_ok = true;")
source = replace_once(source, "clock_boot_calls++; ahb_scan_mask = 0;", "clock_boot_calls++; ahb_scan_mask = 0; always_scan = 0;")
source = replace_once(source, "    if (b == &adreno8_cx_misc) {\n        assert(off >=", r'''
    int index = b == &adreno8_gpucc ? always_index(off) : -1;
    if (index >= 0) {
        assert(strcmp(n, expected_names[index]) == 0);
        if (always_probe) assert(!clock_boot_calls && !writes);
        else assert(adreno8_gmu_from_pll && clock_boot_ok && clock_boot_calls && branches == 1);
        always_reads++;
        if (always_pending < 6) {
            assert(index == (int)always_pending && always_settles == always_writes);
            always_pending = 6;
        } else {
            assert(!(always_scan & (1u << index)));
            always_scan |= 1u << index;
        }
    }
    if (b == &adreno8_cx_misc) {
        assert(off >=''')
source = replace_once(source, "    uint32_t previous = regs[(b->base + off) / 4];", r'''
    uint32_t previous = regs[(b->base + off) / 4];
    int index = b == &adreno8_gpucc ? always_index(off) : -1;
    if (index >= 0) {
        assert(!always_probe && adreno8_gmu_from_pll && clock_boot_ok && clock_boot_calls && branches == 1);
        assert(strcmp(n, expected_names[index]) == 0 && always_scan == 63);
        assert(always_pending == 6 && adreno8_reset_required && !(previous & CBCR_ENABLE));
        assert(value == (previous | CBCR_ENABLE) && !(always_write_mask & (1u << index)));
        assert(always_write_mask < (1u << index));
        always_writes++; always_write_mask |= 1u << index; always_pending = (unsigned)index;
    }''')
source = replace_once(source, "    regs[(b->base + off) / 4] = value; writes++;", r'''
    regs[(b->base + off) / 4] = value; writes++;
    if (index >= 0 && index == always_fault_index) {
        if (always_fault == 1) regs[(b->base + off) / 4] = previous;
        if (always_fault == 2) regs[(b->base + off) / 4] &= ~CBCR_ENABLE;
        if (always_fault == 3) regs[(b->base + off) / 4] = UINT32_MAX;
        if (always_fault == 4) regs[(b->base + off) / 4] ^= CBCR_OFF;
    }''')
source = replace_once(source, "static void adreno8_settle(void) {", "static void adreno8_settle(void) { if (always_pending < 6) always_settles++;")
source += "\nbool\n" + probe.split("bool\n", 1)[1] + r'''
static uint32_t *always_control(unsigned index) {
    assert(index < 6);
    return &regs[expected_offsets[index] / 4];
}
static void always_reset(unsigned mode) {
    ahb_reset(mode);
    for (unsigned i = 0; i < 5; i++) *ahb_control(i) = 0x1b12;
    always_reads = always_writes = always_scan = always_write_mask = always_settles = 0;
    always_pending = 6; always_fault = 0; always_fault_index = -1; always_probe = false;
    for (unsigned i = 0; i < 6; i++) *always_control(i) = 0xa5000100u | (i << 4);
}
static void no_consumers(void) {
    assert(branches == 1 && !ahb_reads && !ahb_writes && always_pending == 6);
}
int main(void) {
    assert(sizeof(always_on) / sizeof(always_on[0]) == 6);
    for (unsigned i = 0; i < 6; i++) {
        assert(always_on[i].offset == expected_offsets[i]);
        assert(strcmp(always_on[i].name, expected_names[i]) == 0);
    }
    always_reset(0); always_probe = true;
    assert(adreno8_step_gpucc() && always_reads == 6 && !always_writes && !writes);
    for (unsigned bad = 0; bad < 6; bad++) {
        always_reset(0); always_probe = true; *always_control(bad) = UINT32_MAX;
        assert(!adreno8_step_gpucc() && always_reads == bad + 1 && !writes);
    }
    always_reset(0);
    assert(adreno8_step_clocks() && !always_reads && !always_writes && !adreno8_reset_required);
    for (unsigned i = 0; i < 6; i++) assert(*always_control(i) == (0xa5000100u | (i << 4)));
    static const uint32_t inherited[6] = {0x08000001, 0x08000000, 0x88000001, 0x08000001, 0x08000001, 0x88000001};
    for (unsigned mode = 1; mode <= 2; mode++) {
        always_reset(mode);
        for (unsigned i = 0; i < 6; i++) *always_control(i) = inherited[i];
        assert(adreno8_step_clocks() && always_writes == 1 && always_write_mask == 2);
        assert(always_reads == 7 && always_settles == 1 && adreno8_reset_required);
        for (unsigned i = 0; i < 6; i++) assert(*always_control(i) == (inherited[i] | 1u));
        adreno8_reset_required = false; branches = 0;
        assert(adreno8_step_clocks() && always_reads == 13 && always_writes == 1);
        assert(!adreno8_reset_required);
        for (unsigned enabled = 0; enabled < 64; enabled++) {
            always_reset(mode);
            for (unsigned i = 0; i < 6; i++) *always_control(i) |= (enabled >> i) & 1u;
            assert(adreno8_step_clocks() && always_write_mask == (63u ^ enabled));
            assert(always_reads == 6 + always_writes && always_settles == always_writes);
            assert(adreno8_reset_required == (enabled != 63));
            for (unsigned i = 0; i < 6; i++) assert(*always_control(i) == (0xa5000101u | (i << 4)));
        }
        for (unsigned bad = 0; bad < 6; bad++) {
            always_reset(mode); *always_control(bad) = UINT32_MAX;
            assert(!adreno8_step_clocks() && always_reads == bad + 1 && !always_writes);
            assert(!adreno8_reset_required); no_consumers();
        }
        for (unsigned fault = 1; fault <= 3; fault++) for (unsigned bad = 0; bad < 6; bad++) {
            always_reset(mode); always_fault = fault; always_fault_index = (int)bad;
            assert(!adreno8_step_clocks() && always_writes == bad + 1);
            assert(always_reads == 6 + bad + 1 && always_settles == bad + 1);
            assert(always_write_mask == (1u << (bad + 1)) - 1 && adreno8_reset_required);
            no_consumers();
            for (unsigned i = bad + 1; i < 6; i++) assert(*always_control(i) == (0xa5000100u | (i << 4)));
        }
        for (unsigned i = 0; i < 6; i++) {
            always_reset(mode); always_fault = 4; always_fault_index = (int)i;
            assert(adreno8_step_clocks() && always_writes == 6);
            assert(*always_control(i) == ((0xa5000101u | (i << 4)) ^ CBCR_OFF));
        }
        always_reset(mode); clock_boot_ok = false;
        assert(!adreno8_step_clocks() && !always_reads && !always_writes && !ahb_reads);
        always_reset(mode); powered = false;
        assert(!adreno8_step_clocks() && !clock_boot_calls && !always_reads && !writes);
        always_reset(mode); fail_branch = 1;
        assert(!adreno8_step_clocks() && !clock_boot_calls && !always_reads && !always_writes);
        always_reset(mode); reject_gemnoc = true;
        assert(!adreno8_step_clocks() && !clock_boot_calls && !always_reads && !always_writes);
    }
    puts("GPUCC always-on branches: inherited bits, ordering, all-ones/drop failures, XO and stage2 read-only: PASS");
}
'''

clocks = fixture["clocks_function"]
mutations = [
    ("no-PLL-guard", "if (adreno8_gmu_from_pll) {", "if (true) {"),
    ("ignore-clock-boot-failure", "if (!adreno8_clock_boot()) return false;", "(void)adreno8_clock_boot();"),
    ("skip-last-branch", "i < sizeof(always_on) / sizeof(always_on[0])", "i + 1 < sizeof(always_on) / sizeof(always_on[0])"),
    ("inherited-all1", "if (always_values[i] == 0xffffffffu) return false;", "if (false) return false;"),
    ("rewrite-enabled", "if (always_values[i] & CBCR_ENABLE) continue;", "if (false) continue;"),
    ("missing-retention", "\n\t\t\tadreno8_reset_required = true;", ""),
    ("missing-write", "adreno8_put(&adreno8_gpucc, always_on[i].name, always_on[i].offset, always_values[i] | CBCR_ENABLE);", ""),
    ("discard-inherited", "always_values[i] | CBCR_ENABLE);", "CBCR_ENABLE);"),
    ("missing-barrier", "\n\t\t\tadreno8_settle();", ""),
    ("ignore-readback", "if (value == 0xffffffffu || !(value & CBCR_ENABLE)) return false;", "if (false && value) return false;"),
    ("readback-all1", "value == 0xffffffffu || !(value & CBCR_ENABLE)", "!(value & CBCR_ENABLE)"),
    ("readback-enable", "value == 0xffffffffu || !(value & CBCR_ENABLE)", "value == 0xffffffffu"),
    ("require-clock-on", "value == 0xffffffffu || !(value & CBCR_ENABLE)", "value == 0xffffffffu || !(value & CBCR_ENABLE) || (value & CBCR_OFF)"),
    ("full-value-readback", "value == 0xffffffffu || !(value & CBCR_ENABLE)", "value != (always_values[i] | CBCR_ENABLE)"),
]
with tempfile.TemporaryDirectory(prefix="adreno8-always-clock-") as temp:
    variants = [("production", source)]
    for label, before, after in mutations:
        assert before in clocks, label
        changed = clocks.replace(before, after)
        assert changed != clocks, label
        variants.append((label, replace_once(source, clocks, changed)))
    for label, code in variants:
        path = Path(temp) / label
        path.with_suffix(".c").write_text(code)
        subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        "-Wno-unused-function", "-fsanitize=address,undefined", str(path.with_suffix(".c")),
                        "-o", str(path)], check=True)
        result = subprocess.run([str(path)], capture_output=True, text=True)
        if label == "production":
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout, end="")
        else:
            assert result.returncode != 0 and "assert" in result.stderr.lower(), (label, result.stdout, result.stderr)
print("GPUCC always-on mutations rejected:", len(mutations))
