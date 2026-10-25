#!/usr/bin/env python3
"""Execute production ACTIVE CX/MX preparation, retaining all uncertain transactions."""
from pathlib import Path
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "xnu/files/bsd/dev"

MODEL = r'''
#include "adreno8_rpmh.c"
#undef printf
static uint32_t regs[0x10000 / 4], before[0x10000 / 4];
static uint32_t inherited[2], votes[2], addresses[2] = {0x30000, 0x30010};
static unsigned scenario, fail_at, reads, writes, triggers, responses, lookups, tx_polls, voltage_writes;
static bool triggered, done, tx_write, poisoned_poll;
static unsigned tx_resource;
static uint32_t initial_control = ARC_AMC_ENABLE, initial_irq = 1;
static bool PE_parse_boot_argn(const char *key, void *out, unsigned size) {
    assert(size == 4); *(uint32_t *)out = 0;
    if (!strcmp(key, "adreno8_arc_read")) { *(uint32_t *)out = 1; return true; }
    unsigned n = !strcmp(key, "touchscreen") ? 0 : !strcmp(key, "wfi") ? 1 : 2;
    assert(n != 2 || !strcmp(key, "wfe_mode"));
    if (scenario == 3 + 2 * n) return false;
    if (scenario == 4 + 2 * n) *(uint32_t *)out = 1;
    return true;
}
static void adreno8_settle(void) {}
static void adreno8_sleep_us(uint32_t us) { assert(us == 100); }
static uint32_t adreno8_rpmh_lookup(const char *name, uint16_t *levels, unsigned *count,
                                  const uint8_t **data, unsigned *length) {
    assert(!data && !length);
    unsigned rail = !strcmp(name, "cx.lvl") ? 0 : 1;
    assert(!rail || !strcmp(name, "mx.lvl")); lookups++;
    const uint16_t standard[] = {0,16,64,128,192,256,320,384,416,448,480};
    memcpy(levels, standard, sizeof(standard)); *count = rail ? 10 : 11;
    if (scenario == 27) *count = 0;
    if (scenario == 28) *count = 65;
    if (scenario == 29) levels[4] = levels[3];
    if (scenario == 30) *count = 2;
    if (scenario == 34) { levels[2]=32; levels[3]=64; levels[4]=128; }
    if ((scenario == 25 && !rail) || (scenario == 26 && rail)) return 0;
    if (scenario == 52) return 0x40000;
    if (scenario == 53) return 0x30001;
    if (scenario == 38) return 0x30000;
    return addresses[rail];
}
static uint32_t adreno8_get(struct adreno8_block *block, const char *name, uint32_t off) {
    assert(!poisoned_poll);
    assert(block->phys == 0x16520000 && block->size == 0x10000 && off < 0x10000 && !(off & 3));
    assert(off == 0 || off == 4 || off == 0xc || off == 0xd28 || off == 0xfc8 ||
        off == ARC_CONTROL || off == ARC_STATUS || off == ARC_ENABLE || off == ARC_WAIT ||
        off == ARC_MSGID || off == ARC_ADDRESS || off == ARC_DATA || off == ARC_COMMAND_STATUS ||
        off == ARC_RESPONSE || off == ARC_IRQ_ENABLE || off == ARC_IRQ_STATUS ||
        (off >= 0x1500 && off <= 0x1f8c && ((off - 0xd00) % 0x2a0 >= 0x20) &&
         ((off - 0xd00) % 0x2a0 <= 0x2c)));
    block->base = 0x100000; reads++;
    if ((scenario == 59 && !strcmp(name, "irq enable before restore")) ||
        (scenario == 60 && triggers == 3 && !strcmp(name, "irq status after request")) ||
        (scenario == 61 && triggers == 3 && off == ARC_COMMAND_STATUS) ||
        (scenario == 63 && triggers == 3 && !strcmp(name, "irq enable before restore")))
        return UINT32_MAX;
    if (scenario == 48 && triggers == 2 && !strcmp(name, "irq enable before request"))
        regs[ARC_IRQ_ENABLE / 4] |= 1u << 5;
    if (scenario == 49 && !strcmp(name, "irq enable before restore"))
        regs[ARC_IRQ_ENABLE / 4] |= 1u << 5;
    if (scenario == 51 && triggers == 3 && !strcmp(name, "TCS2 control after completion"))
        regs[ARC_CONTROL / 4] |= 2;
    if (scenario == 57 && triggers == 2 && off == 4) regs[1] |= 1u << 24;
    if (off == ARC_RESPONSE) { assert(done && !tx_write); responses++; }
    return regs[off / 4];
}
static void adreno8_put(struct adreno8_block *block, const char *name, uint32_t off, uint32_t value) {
    (void)name;
    assert(!poisoned_poll && adreno8_reset_required && block->phys == 0x16520000);
    assert(off == ARC_CONTROL || off == ARC_ENABLE || off == ARC_MSGID || off == ARC_ADDRESS ||
        off == ARC_DATA || off == ARC_WAIT || off == ARC_IRQ_ENABLE || off == ARC_IRQ_CLEAR);
    assert(!triggered || done || (off == ARC_CONTROL && value == (ARC_AMC_ENABLE | ARC_AMC_TRIGGER)));
    writes++;
    if (off == ARC_DATA) {
        unsigned rail = regs[ARC_ADDRESS / 4] == addresses[0] ? 0 : 1;
        unsigned required = scenario == 34 ? 4 : 3;
        if (required < inherited[rail]) required = inherited[rail];
        assert(responses >= 2 && value == required && value >= inherited[rail]);
        voltage_writes++;
    }
    if (off == ARC_WAIT) assert(value == 0 || value == 1);
    if (off == ARC_MSGID) assert(value == 0x108 || value == 0x10108);
    if (off == ARC_ADDRESS) assert(value == 0x30000 || value == 0x30010);
    if (fail_at == writes) { regs[off / 4] = value ^ 1; return; }
    if (off == ARC_IRQ_CLEAR) {
        assert(done && value == ARC_TCS_BIT);
        if (!(scenario == 50 && triggers == 3)) regs[ARC_IRQ_STATUS / 4] &= ~value;
        return;
    }
    if (off == ARC_CONTROL && value == (ARC_AMC_ENABLE | ARC_AMC_TRIGGER)) {
        assert(regs[ARC_CONTROL / 4] == ARC_AMC_ENABLE && !(regs[ARC_IRQ_STATUS / 4] & ARC_TCS_BIT));
        assert(regs[ARC_IRQ_ENABLE / 4] & ARC_TCS_BIT);
        tx_write = regs[ARC_MSGID / 4] == 0x10108;
        assert(regs[ARC_ENABLE / 4] == 1 && regs[ARC_WAIT / 4] == unsigned(tx_write));
        tx_resource = regs[ARC_ADDRESS / 4] == addresses[0] ? 0 : 1;
        triggered = true; done = false; tx_polls = 0; triggers++;
    }
    regs[off / 4] = value;
}
static uint32_t adreno8_rd(vm_offset_t address) {
    assert(address == 0x100000 + ARC_IRQ_STATUS && triggered); reads++; tx_polls++;
    if (scenario == 62 && triggers == 3) { poisoned_poll=true; return UINT32_MAX; }
    bool timeout = scenario >= 40 && scenario <= 45 && triggers == scenario - 39;
    if (tx_polls == 3 && !timeout) {
        done = true;
        if (tx_write) votes[tx_resource] = regs[ARC_DATA / 4];
        if (scenario == 47 && tx_write) votes[tx_resource]--;
        regs[ARC_IRQ_STATUS / 4] |= ARC_TCS_BIT | 2;
        regs[ARC_COMMAND_STATUS / 4] = scenario == 46 && triggers == 3 ? 1u << 8 : ARC_COMMAND_DONE;
        regs[ARC_STATUS / 4] = 1;
        regs[ARC_RESPONSE / 4] = scenario == 58 && triggers == 4 ? 11 : votes[tx_resource];
    }
    return regs[ARC_IRQ_STATUS / 4];
}
static void setup(void) {
    adreno8_gmu_from_pll = 1;
    regs[0]=0x30800; regs[1]=0x04010100; regs[3]=0x800c8104;
    regs[ARC_CONTROL / 4]=initial_control; regs[ARC_STATUS / 4]=1;
    regs[0xd28 / 4]=regs[0xfc8 / 4]=1;
    regs[ARC_IRQ_ENABLE / 4]=initial_irq; regs[ARC_DATA / 4]=0xdeadbeef;
    for(unsigned i=3;i<8;i++) regs[(0xd00+i*0x2a0+0x28)/4]=1;
    switch(scenario) {
    case 1: adreno8_gmu_from_pll=0; break;
    case 2: adreno8_gmu_from_pll=2; break;
    case 9: regs[0]=0x30700; break;
    case 10: regs[1]=UINT32_MAX; break;
    case 11: regs[1]|=1u<<24; break;
    case 12: regs[3]=0; break;
    case 13: regs[(0xd00+3*0x2a0+0x2c)/4]=1; break;
    case 14: regs[(0xd00+4*0x2a0+0x20)/4]=1; break;
    case 15: regs[(0xd00+5*0x2a0+0x24)/4]=1; break;
    case 16: regs[(0xd00+7*0x2a0+0x28)/4]=0; break;
    case 17: regs[0xd28/4]=0; break;
    case 18: regs[0xfc8/4]=0; break;
    case 19: regs[ARC_STATUS/4]=0; break;
    case 20: regs[ARC_ENABLE/4]=1; break;
    case 21: regs[ARC_WAIT/4]=1; break;
    case 22: regs[ARC_CONTROL/4]|=ARC_AMC_TRIGGER; break;
    case 23: regs[ARC_IRQ_STATUS/4]=ARC_TCS_BIT; break;
    case 24: regs[ARC_IRQ_ENABLE/4]=UINT32_MAX; break;
    case 31: inherited[0]=7; inherited[1]=6; break;
    case 32: inherited[0]=7; break;
    case 33: inherited[1]=7; break;
    case 35: inherited[0]=inherited[1]=3; break;
    case 36: inherited[0]=11; break;
    case 37: inherited[1]=10; break;
    }
    memcpy(votes,inherited,sizeof(votes)); memcpy(before,regs,sizeof(regs));
}
int main(int argc,char **argv) {
    assert(argc>=2); scenario=atoi(argv[1]); if(argc>2)fail_at=atoi(argv[2]); setup();
    if(scenario==55) assert(adreno8_rpmh_arc_read() && arc_state==ARC_READ_COMPLETE);
    bool result=adreno8_rpmh_clock_prepare();
    bool success=scenario==0 || scenario==1 || (scenario>=31 && scenario<=35) || scenario==49 || scenario==55;
    if(fail_at) assert(!result && writes==fail_at && adreno8_reset_required);
    else assert(result==success);
    if(scenario==1) assert(!reads && !writes && !lookups);
    if(result && scenario!=1) {
        unsigned target=scenario==34?4:3;
        for(unsigned i=0;i<2;i++) assert(votes[i]==(inherited[i]>target?inherited[i]:target));
        unsigned expected_writes=(inherited[0]<target)+(inherited[1]<target);
        assert(voltage_writes==expected_writes && adreno8_reset_required);
        assert(regs[ARC_CONTROL/4]==initial_control && !regs[ARC_ENABLE/4] && !regs[ARC_WAIT/4]);
        assert(regs[ARC_IRQ_ENABLE/4]==(initial_irq | (scenario==49?1u<<5:0)));
        assert(regs[ARC_IRQ_STATUS/4]==2);
    }
    if(!result && !fail_at && scenario<=28) assert(!voltage_writes);
    if(scenario==40 || scenario==41 || scenario==42 || scenario==43 || scenario==44 || scenario==45)
        assert(triggers==scenario-39 && tx_polls==100000 && !done);
    if(scenario==59) assert(writes==13 && triggers==1 && !voltage_writes);
    if(scenario==60 || scenario==61 || scenario==62) assert(writes==38 && triggers==3 && voltage_writes==1);
    if(scenario==62) assert(tx_polls==1 && !done);
    if(scenario==63) assert(writes==44 && triggers==3 && voltage_writes==1);
    for(unsigned i=0;i<0x10000/4;i++) {
        unsigned off=4*i;
        bool tcs2=off>=ARC_WAIT && off<=ARC_RESPONSE;
        if(off!=ARC_IRQ_ENABLE && off!=ARC_IRQ_STATUS && !tcs2 &&
           !(scenario==57 && off==4)) assert(regs[i]==before[i]);
    }
    unsigned old_reads=reads, old_writes=writes;
    if(scenario!=1) {
        assert(!adreno8_rpmh_clock_prepare());
        if(scenario!=2) assert(!adreno8_rpmh_arc_read());
    } else assert(adreno8_rpmh_clock_prepare());
    assert(reads==old_reads && writes==old_writes);
}
'''


def run(source, target, cases, reject=False):
    fixture = runpy.run_path(str(ROOT / "tools/test-adreno8-arc-read.py"))
    (target / "adreno8_private.h").write_text(fixture["HEADER"])
    (target / "adreno8_rpmh_private.h").write_text((DEV / "adreno8_rpmh_private.h").read_text())
    (target / "adreno8_rpmh.c").write_text(source)
    (target / "check.cpp").write_text(MODEL)
    binary = target / "check"
    subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-Wno-missing-field-initializers", "-fsanitize=address,undefined",
                    str(target / "check.cpp"), "-o", str(binary)], check=True)
    for args in cases:
        result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True)
        if reject:
            assert result.returncode, f"mutation survived {args}"
        elif result.returncode:
            raise RuntimeError(f"case {args}:\n" + result.stdout + result.stderr)


def main():
    production = (DEV / "adreno8_rpmh.c").read_text()
    cases = [(n,) for n in range(64) if n not in (39, 54, 56)]
    cases += [(0, n) for n in range(1, 91) if n not in (8, 12, 22, 26, 38, 43, 53, 57, 69, 74, 84, 88)]
    mutations = [
        ("drop_inherited", "if (required[i] < votes[i].selector) required[i] = votes[i].selector;", "", (31,)),
        ("wrong_selector_units", "votes[i].selector = required[i];", "votes[i].selector = 128;", (0,)),
        ("skip_write_ack", "if (!arc_transfer(&votes[i], true) ||", "if (false ||", (0,)),
        ("skip_verify", "votes[i].selector != required[i]) return false;", "false) return false;", (47,)),
        ("allow_solver", "(solver & (1u << 24)) == 0", "true", (11,)),
        ("skip_sleep_guard", 'i = 3; i < 8', 'i = 8; i < 8', (13,)),
        ("ignore_touch", 'zero != 0', 'false', (4,)),
        ("repeat", "adreno8_gmu_from_pll != 1 || arc_state == ARC_CLOSED", "adreno8_gmu_from_pll != 1", (0,)),
        ("irq_peer_loss", "irq & ~(ARC_TCS_BIT & ~irq_before)", "irq_before", (49,)),
        ("write_read_message", "write ? 0x10108u : 0x108u", "0x108u", (0,)),
        ("timeout_cleanup", 'printf("adreno8: ARC transaction completion unconfirmed;',
         'arc_write("premature cleanup", ARC_ENABLE, 0); printf("adreno8: ARC transaction completion unconfirmed;', (42,)),
        ("poll_unreadable", 'if (irq == 0xffffffffu) {', 'if (false) {', (62,)),
        ("command_unreadable", 'command == 0xffffffffu ||', '', (61,)),
        ("restore_unreadable", 'if (irq == 0xffffffffu ||\n\t    !arc_write("irq restore TCS2 bit",',
         'if (!arc_write("irq restore TCS2 bit",', (63,)),
    ]
    with tempfile.TemporaryDirectory(prefix="adreno8-rpmh-clock-") as directory:
        target = Path(directory)
        run(production, target, cases)
        for name, old, new, case in mutations:
            assert production.count(old) == 1, name
            run(production.replace(old, new, 1), target, [case], True)
        unreadable_irq = production.replace('irq != 0xffffffffu && (irq & ARC_TCS_BIT)', '(irq & ARC_TCS_BIT)', 1)
        unreadable_irq = unreadable_irq.replace('irq == 0xffffffffu || !(irq & ARC_TCS_BIT)', '!(irq & ARC_TCS_BIT)', 1)
        assert unreadable_irq != production
        run(unreadable_irq, target, [(60,)], True)
    print(f"ARC ACTIVE CX/MX: {len(cases)} production scenarios PASS; {len(mutations) + 1} mutations rejected")


if __name__ == "__main__":
    main()
