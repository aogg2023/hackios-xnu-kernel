#!/usr/bin/env python3
"""Execute the production ARC CX probe with tracked MMIO and retained failures."""
from pathlib import Path
import argparse
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "xnu/files/bsd/dev"
PRODUCTION = (DEV / "adreno8_rpmh.c").read_text()
HEADER = r'''
#ifndef _TEST_ADRENO8_PRIVATE_H
#define _TEST_ADRENO8_PRIVATE_H
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
struct adreno8_block { const char *name; uint32_t phys, size; vm_offset_t base; };
#define RPMH_LEVELS 64
static bool adreno8_reset_required;
static bool PE_parse_boot_argn(const char *, void *, unsigned);
static uint32_t adreno8_get(struct adreno8_block *, const char *, uint32_t);
static void adreno8_put(struct adreno8_block *, const char *, uint32_t, uint32_t);
static uint32_t adreno8_rd(vm_offset_t);
static void adreno8_settle(void);
static void adreno8_sleep_us(uint32_t);
static uint32_t adreno8_rpmh_lookup(const char *, uint16_t *, unsigned *, const uint8_t **, unsigned *);
static int adreno8_say(const char *format, ...) { (void)format; return 0; }
#endif
'''
HARNESS = r'''
#include "adreno8_rpmh.c"
#undef printf
static uint32_t regs[0x10000 / 4], before[0x10000 / 4];
static unsigned scenario, fail_at, reads, writes, polls, sleeps, responses, triggers, lookups;
static bool flag_present = true, touch_present = true, triggered, done;
static uint32_t flag = 1, touch, cx_address = 0x30000, level_count = 11, response_value = 3;
static uint32_t initial_control = ARC_AMC_ENABLE, initial_irq = 1;
static bool PE_parse_boot_argn(const char *name, void *out, unsigned size) {
    assert(size == sizeof(uint32_t));
    if (!strcmp(name, "adreno8_arc_read")) {
        if (flag_present) *(uint32_t *)out = flag;
        return flag_present;
    }
    assert(!strcmp(name, "touchscreen"));
    if (touch_present) *(uint32_t *)out = touch;
    return touch_present;
}
static void adreno8_settle(void) {}
static void adreno8_sleep_us(uint32_t us) { assert(us == 100); sleeps++; }
static uint32_t adreno8_rpmh_lookup(const char *name, uint16_t *levels, unsigned *count,
                                  const uint8_t **data, unsigned *length) {
    assert(!strcmp(name, "cx.lvl") && !data && !length);
    lookups++; *count = level_count;
    for (unsigned i = 0; i < level_count && i < RPMH_LEVELS; i++) levels[i] = i * 16;
    return cx_address;
}
static uint32_t adreno8_get(struct adreno8_block *b, const char *name, uint32_t off) {
    assert(b->phys == 0x16520000 && b->size == 0x10000 && off < b->size && !(off & 3));
    b->base = 0x100000; reads++;
    if (scenario == 27 && !strcmp(name, "irq enable before request"))
        regs[ARC_IRQ_ENABLE / 4] |= 1u << 5;
    if (scenario == 28 && !strcmp(name, "irq enable before restore"))
        regs[ARC_IRQ_ENABLE / 4] |= 1u << 5;
    if (scenario == 30 && !strcmp(name, "TCS2 control after completion"))
        regs[ARC_CONTROL / 4] |= 2;
    if (off == ARC_RESPONSE) { assert(done); responses++; }
    return regs[off / 4];
}
static void adreno8_put(struct adreno8_block *b, const char *name, uint32_t off, uint32_t value) {
    (void)name;
    assert(adreno8_reset_required && b->phys == 0x16520000);
    assert(off == ARC_CONTROL || off == ARC_ENABLE || off == ARC_MSGID ||
           off == ARC_ADDRESS || off == ARC_IRQ_ENABLE || off == ARC_IRQ_CLEAR);
    writes++;
    if (off == ARC_MSGID) assert(value == 0x108);
    if (off == ARC_ADDRESS) assert(value == cx_address && (value >> 16) == 3);
    if (fail_at == writes) { regs[off / 4] = value ^ 1; return; }
    if (off == ARC_IRQ_CLEAR) {
        assert(done && value == ARC_TCS_BIT);
        if (scenario != 29) regs[ARC_IRQ_STATUS / 4] &= ~value;
        return;
    }
    if (off == ARC_CONTROL && value == (ARC_AMC_ENABLE | ARC_AMC_TRIGGER)) {
        assert(!triggered && regs[ARC_CONTROL / 4] == ARC_AMC_ENABLE);
        assert(regs[ARC_MSGID / 4] == 0x108 && regs[ARC_ADDRESS / 4] == cx_address);
        assert(regs[ARC_ENABLE / 4] == 1 && regs[ARC_WAIT / 4] == 0);
        assert(regs[ARC_IRQ_ENABLE / 4] & ARC_TCS_BIT);
        triggered = true; triggers++;
    } else if (triggered && !done) {
        assert(!"cleanup before confirmed completion");
    }
    regs[off / 4] = value;
}
static uint32_t adreno8_rd(vm_offset_t address) {
    assert(address == 0x100000 + ARC_IRQ_STATUS && triggered);
    reads++; polls++;
    if (polls == 3 && scenario != 23) {
        done = true;
        regs[ARC_IRQ_STATUS / 4] |= ARC_TCS_BIT | 2;
        regs[ARC_COMMAND_STATUS / 4] = scenario == 24 ? 1u << 8 : ARC_COMMAND_DONE;
        regs[ARC_STATUS / 4] = scenario == 25 ? 0 : 1;
        regs[ARC_RESPONSE / 4] = response_value;
    }
    return regs[ARC_IRQ_STATUS / 4];
}
static void prepare(void) {
    regs[0] = 0x30800; regs[1] = 0x04010100; regs[3] = 0x800c8104;
    regs[ARC_STATUS / 4] = 1; regs[ARC_CONTROL / 4] = initial_control;
    regs[0xd28 / 4] = regs[0xfc8 / 4] = 1;
    regs[ARC_IRQ_ENABLE / 4] = initial_irq; regs[0x127c / 4] = 0xdeadbeef;
    regs[0xd2c / 4] = 0x7f; regs[0x1270 / 4] = 0x5a5a;
    switch (scenario) {
    case 0: flag_present = false; break;
    case 1: flag = 0; break;
    case 2: flag = 2; break;
    case 3: flag = UINT32_MAX; break;
    case 4: touch_present = false; break;
    case 5: touch = 2; break;
    case 6: regs[0] = 0x30700; break;
    case 7: regs[1] |= 1u << 24; break;
    case 8: regs[3] = 16u << 27 | 7u << 12; break;
    case 9: regs[3] = 8u << 12; break;
    case 10: regs[ARC_STATUS / 4] = 0; break;
    case 11: regs[ARC_ENABLE / 4] = 1; break;
    case 12: regs[ARC_WAIT / 4] = 1; break;
    case 13: regs[ARC_IRQ_STATUS / 4] = ARC_TCS_BIT; break;
    case 14: regs[ARC_CONTROL / 4] |= ARC_AMC_TRIGGER; break;
    case 15: regs[ARC_CONTROL / 4] |= 2; break;
    case 16: cx_address = 0; break;
    case 17: cx_address = 0x40000; break;
    case 18: level_count = 0; break;
    case 19: level_count = 65; break;
    case 21: initial_control = regs[ARC_CONTROL / 4] = 0; break;
    case 22: initial_irq = regs[ARC_IRQ_ENABLE / 4] = 5; break;
    case 26: response_value = level_count; break;
    case 33: response_value = 0; break;
    case 34: regs[0] = UINT32_MAX; break;
    case 35: regs[0xd28 / 4] = 0; break;
    case 36: regs[0xfc8 / 4] = 0; break;
    case 37: regs[ARC_IRQ_ENABLE / 4] = UINT32_MAX; break;
    }
    memcpy(before, regs, sizeof(regs));
}
int main(int argc, char **argv) {
    assert(argc >= 2); scenario = strtoul(argv[1], NULL, 0);
    if (argc == 3) fail_at = strtoul(argv[2], NULL, 0);
    prepare();
    bool result = adreno8_rpmh_arc_read();
    bool success = scenario == 20 || scenario == 21 || scenario == 22 || scenario == 28 || scenario == 33;
    if (fail_at) assert(!result && writes == fail_at && adreno8_reset_required);
    else if (scenario <= 1) assert(result && !writes && !reads && !lookups);
    else if (success) {
        assert(result && writes == 14 && triggers == 1 && responses == 1 && adreno8_reset_required);
        assert(regs[ARC_CONTROL / 4] == initial_control && !regs[ARC_ENABLE / 4] && !regs[ARC_WAIT / 4]);
        assert(regs[ARC_IRQ_ENABLE / 4] == (initial_irq | (scenario == 28 ? 1u << 5 : 0)));
        assert(regs[ARC_IRQ_STATUS / 4] == 2);
    } else {
        assert(!result);
        if (scenario <= 19 || scenario >= 34) assert(!writes && !adreno8_reset_required);
        else if (scenario == 27) assert(writes == 5 && !triggers && adreno8_reset_required);
        else if (scenario == 29) assert(writes == 12 && triggers == 1 && adreno8_reset_required);
        else assert(writes == 8 && triggers == 1 && adreno8_reset_required);
        if (scenario == 23) assert(polls == 100000 && sleeps == 100000 && !responses);
        if (scenario == 24 || scenario == 25) assert(!responses);
    }
    assert(regs[0x127c / 4] == 0xdeadbeef);
    assert(regs[0xd2c / 4] == before[0xd2c / 4] && regs[0x1270 / 4] == before[0x1270 / 4]);
    unsigned saved_writes = writes, saved_reads = reads;
    if (scenario <= 1) assert(adreno8_rpmh_arc_read());
    else assert(!adreno8_rpmh_arc_read());
    assert(writes == saved_writes && reads == saved_reads);
}
'''


def run(source, directory, scenarios, reject=False):
    (directory / "adreno8_private.h").write_text(HEADER)
    (directory / "adreno8_rpmh_private.h").write_text((DEV / "adreno8_rpmh_private.h").read_text())
    (directory / "adreno8_rpmh.c").write_text(source)
    path, binary = directory / "check.c", directory / "check"
    path.write_text(HARNESS)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                    "-fsanitize=address,undefined", str(path), "-o", str(binary)], check=True)
    for args in scenarios:
        result = subprocess.run([str(binary), *map(str, args)], capture_output=True)
        if reject:
            assert result.returncode != 0, f"mutation survived {args}"
        else:
            result.check_returncode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    scenarios = [(s,) for s in range(38) if s not in (31, 32)]
    scenarios += [(20, n) for n in range(1, 15) if n != 8 and n != 12]
    with tempfile.TemporaryDirectory(prefix="adreno8-arc-read-") as directory:
        directory = Path(directory)
        run(PRODUCTION, directory, scenarios)
        if args.mutations:
            mutations = [
                ("resource_write", "ARC_MSGID, 0x108", "ARC_MSGID, 0x10108", (20,)),
                ("command_data_write", "ARC_ADDRESS, address)", "0x127cu, address)", (20,)),
                ("missing_reset", "adreno8_reset_required = true;", "adreno8_reset_required = false;", (20,)),
                ("repeat_allowed", "asked != 1 || arc_attempted", "asked != 1 || false", (20,)),
                ("touch_implicit_zero", '!PE_parse_boot_argn("touchscreen", &touch, sizeof(touch)) || touch != 0',
                 'PE_parse_boot_argn("touchscreen", &touch, sizeof(touch)) && touch != 0', (4,)),
                ("unknown_parameter", "asked != 1 || arc_attempted", "asked == 0 || arc_attempted", (2,)),
                ("response_from_data", 'arc_read("TCS2 cx.lvl response", ARC_RESPONSE)',
                 'arc_read("TCS2 cx.lvl response", 0x127c)', (20,)),
                ("timeout_cleanup", 'printf("adreno8: ARC read completion unconfirmed;',
                 'arc_write("premature clear", ARC_ENABLE, 0); printf("adreno8: ARC read completion unconfirmed;', (23,)),
                ("irq_snapshot_restore", "irq & ~(ARC_TCS_BIT & ~irq_before)", "irq_before", (28,)),
                ("clear_peer_irq", "ARC_IRQ_CLEAR, ARC_TCS_BIT", "ARC_IRQ_CLEAR, 0xffffffffu", (20,)),
                ("incomplete_command", '(arc_read("TCS2 command status", ARC_COMMAND_STATUS) & ARC_COMMAND_DONE) != ARC_COMMAND_DONE',
                 "false", (24,)),
                ("other_tcs_busy", 'arc_read("TCS0 status", 0xd28) != 1', "false", (35,)),
                ("skip_register_verify", "if (arc_read(name, offset) == value)", "if (true)", (20, 3)),
                ("commands_stay_enabled", '"TCS2 completed command disable", ARC_ENABLE, 0',
                 '"TCS2 completed command disable", ARC_ENABLE, 1', (20,)),
                ("restore_trigger", '"TCS2 restore initial control", ARC_CONTROL, control)',
                 '"TCS2 restore initial control", ARC_CONTROL, control | ARC_AMC_TRIGGER)', (20,)),
                ("stale_irq", '(arc_read("irq status", ARC_IRQ_STATUS) & ARC_TCS_BIT) != 0', "false", (13,)),
            ]
            for name, old, new, scenario in mutations:
                assert PRODUCTION.count(old) == 1, name
                run(PRODUCTION.replace(old, new, 1), directory, [scenario], reject=True)
            print(f"ARC CX failure mutations: {len(mutations)} rejected")
    print(f"ARC CX production probe: {len(scenarios)} success, failure and replay cases PASS")


if __name__ == "__main__":
    main()
