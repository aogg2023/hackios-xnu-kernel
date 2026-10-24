#!/usr/bin/env python3
"""Run the production SMMU mapper against allocated memory and simulated registers."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
header = (ROOT / "xnu/files/bsd/dev/adreno8_private.h").read_text()
driver = (ROOT / "xnu/files/bsd/dev/adreno8_smmu.c").read_text()
structures = "\n".join(
    re.search(rf"struct {name} \{{.*?\n}};", header, re.S).group()
    for name in ("adreno8_block", "adreno8_gpu_block", "adreno8_space_table", "adreno8_gpu_space")
)
constants = "\n".join(
    line for line in header.splitlines()
    if line.startswith(("#define SMMU_", "#define PTE_", "#define SCTLR_", "#define TCR_",
                        "#define TCR2_", "#define MAIR0_", "#define FSR_", "#define SMR_", "#define STAGE_1",
                        "#define GMU_CONTEXT", "#define SECURE_CONTEXT", "#define SECURE_STREAM",
                        "#define SECURE_MATCH", "#define SECURE_MODE_"))
)
# Replace only the privileged-platform MMIO instruction; the mapper and lifecycle run unchanged.
driver, replaced = re.subn(
    r'static void __attribute__\(\(noinline\)\)\nadreno8_smmu_write64\(.*?\n}',
    'static void adreno8_smmu_write64(vm_offset_t address, uint64_t value) { mock_atos(address, value); }',
    driver, count=1, flags=re.S,
)
assert replaced == 1
stub = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
#define PAGE_MASK (PAGE_SIZE - 1)
#define KMA_KOBJECT 0
#define KMA_ZERO 0
#define KMA_DATA 0
#define VM_KERN_MEMORY_IOKIT 0
#define KERN_SUCCESS 0
static int kernel_map;
static unsigned adreno8_step;
extern uint32_t adreno8_gpu8_secure_mode; /* defined by the memory harness; unused by the SMMU tests */
''' + structures + "\n" + constants + r'''
/* The driver's printf is adreno8_say; the real one reaches the console, so
 * the stub forwards to stdout and the runner can check message order. */
static int adreno8_say(const char *s, ...) {
    va_list ap;
    int n;
    va_start(ap, s);
    n = vprintf(s, ap);
    va_end(ap);
    return n;
}
static void adreno8_announce(const char *s, ...) {}
static void adreno8_settle(void) {}
static bool adreno8_cx_ready(void) { return true; }
static uint64_t mach_absolute_time(void) { return 0x1234abcdull; }
static void absolutetime_to_nanoseconds(uint64_t time, uint64_t *ns) { *ns = time; }
static void adreno8_space_off(struct adreno8_gpu_space *, uint32_t);
static bool adreno8_space_faulted(struct adreno8_gpu_space *);
static uint32_t regs[0x40000 / 4];
static unsigned writes, translations, ats_requests;
struct write_record { uint32_t offset, value; };
static struct write_record write_log[512];
static unsigned write_log_count;
static vm_offset_t cleaned[4096];
static unsigned clean_count;
static unsigned reads[0x40000 / 4];
static bool sync_ok = true, atos_ok = true, atos_wait_ok = true;
static uint32_t reject_offset = ~0u, reject_value;
static uint32_t sctlr_raz_mask, tcr2_read_xor, s2cr_trans_raz_mask;
static int alloc_fail_after = -1;
struct allocation { uintptr_t va; size_t size; uint64_t pa; };
static struct allocation allocations[2048];
static unsigned allocation_count;
static uint64_t next_pa = 0x80000000;
static int kmem_alloc(int map, vm_offset_t *out, size_t size, int flags, int tag) {
    if (alloc_fail_after == 0) return 1;
    if (alloc_fail_after > 0) alloc_fail_after--;
    void *p = aligned_alloc(PAGE_SIZE, size);
    assert(p);
    memset(p, 0, size);
    allocations[allocation_count++] = (struct allocation){(uintptr_t)p, size, next_pa};
    next_pa += (size / PAGE_SIZE) * 0x20000;
    *out = (uintptr_t)p;
    return KERN_SUCCESS;
}
static void kmem_free(int map, vm_offset_t va, size_t size) {
    for (unsigned i = 0; i < allocation_count; i++) {
        if (allocations[i].va == va) {
            assert(allocations[i].size == size);
            free((void *)va);
            allocations[i].va = 0;
            return;
        }
    }
    assert(false);
}
static uint64_t kvtophys(vm_offset_t va) {
    for (unsigned i = 0; i < allocation_count; i++) {
        struct allocation a = allocations[i];
        if (a.va && va >= a.va && va - a.va < a.size) {
            uint64_t delta = va - a.va;
            return a.pa + delta / PAGE_SIZE * 0x20000 + delta % PAGE_SIZE;
        }
    }
    assert(false);
    return 0;
}
static uint64_t *physical_table(uint64_t pa) {
    for (unsigned i = 0; i < allocation_count; i++) {
        if (allocations[i].va && allocations[i].pa == pa)
            return (uint64_t *)allocations[i].va;
    }
    assert(false);
    return NULL;
}
static uint64_t walk(uint64_t root, uint32_t va) {
    uint64_t entry = physical_table(root)[va >> 30];
    if ((entry & 3) != 3) return 1;
    entry = physical_table(entry & PTE_ADDRESS)[va >> 21 & 511];
    if ((entry & 3) != 3) return 1;
    entry = physical_table(entry & PTE_ADDRESS)[va >> 12 & 511];
    if ((entry & 0xc03) != 0xc03) return 1; /* ATS1PR is a privileged read. */
    return entry & PTE_ADDRESS;
}
static uint32_t adreno8_rd(vm_offset_t address) {
    assert(address < sizeof(regs));
    return regs[address / 4];
}
static uint32_t adreno8_get(struct adreno8_block *b, const char *name, uint32_t offset) {
    reads[offset / 4]++;
    return adreno8_rd(offset);
}
static void adreno8_put(struct adreno8_block *b, const char *name, uint32_t offset, uint32_t value) {
    assert(offset < sizeof(regs));
    assert(write_log_count < sizeof(write_log) / sizeof(write_log[0]));
    write_log[write_log_count++] = (struct write_record){offset, value};
    writes++;
    if (offset == reject_offset && value == reject_value) return;
    if (offset == 0x22000 + SMMU_CB_SCTLR) value &= ~sctlr_raz_mask;
    if (offset == 0x22000 + SMMU_CB_TCR2) value = (value | 0x60) ^ tcr2_read_xor;
    if (offset >= 0x20000 && (offset & 0xfff) == SMMU_CB_FSR) {
        regs[offset / 4] &= ~value;
        return;
    }
    if (offset == 0x22000 + SMMU_CB_TLBASID) assert(value == GMU_CONTEXT);
    if (offset == SMMU_S2CR(1) && (value & 0x30000) == 0) value &= ~s2cr_trans_raz_mask;
    if (offset == SMMU_S2CR(1) && value == (GMU_CONTEXT | 0x300)) translations++;
    regs[offset / 4] = value;
}
static bool adreno8_wait_for(struct adreno8_block *b, uint32_t offset, uint32_t mask, uint32_t value, uint32_t ms) {
    if (offset == 0x22000 + 0x8f0) return atos_wait_ok;
    assert(offset == 0x22000 + SMMU_CB_TLBSTATUS || offset == 0x20000 + SMMU_CB_TLBSTATUS);
    return sync_ok;
}
static void adreno8_to_memory(vm_offset_t memory, vm_size_t size) {
    assert(size == 4096);
    assert(clean_count < 4096);
    cleaned[clean_count++] = memory;
}
static void mock_atos(vm_offset_t address, uint64_t value) {
    assert(address == 0x22800);
    assert((regs[SMMU_S2CR(1) / 4] & 0x30000) == 0x20000);
    assert(regs[0x22804 / 4] == 0);
    uint64_t root = regs[(0x22000 + SMMU_CB_TTBR0) / 4];
    root |= (uint64_t)regs[(0x22000 + SMMU_CB_TTBR0 + 4) / 4] << 32;
    uint64_t par = atos_ok ? walk(root & PTE_ADDRESS, (uint32_t)value) : 1;
    regs[(0x22000 + 0x50) / 4] = par;
    regs[(0x22000 + 0x54) / 4] = par >> 32;
    ats_requests++;
}
'''
tests = r'''
#undef printf
static void release_all(void) {
    for (unsigned i = 0; i < allocation_count; i++) {
        if (allocations[i].va) free((void *)allocations[i].va);
    }
    allocation_count = 0;
    next_pa = 0x80000000;
}
static void reset(struct adreno8_gpu_space *space, bool atos) {
    release_all();
    memset(space, 0, sizeof(*space));
    space->context = GMU_CONTEXT;
    memset(regs, 0, sizeof(regs));
    memset(reads, 0, sizeof(reads));
    regs[SMMU_IDR0 / 4] = 0x4c017e18 & ~(atos ? 1u << 26 : 0);
    regs[SMMU_IDR1 / 4] = 0x40000015;
    regs[SMMU_IDR2 / 4] = 0x5511;
    regs[SMMU_SCR0 / 4] = 0x2e0406;
    sync_ok = atos_ok = atos_wait_ok = true;
    sctlr_raz_mask = 1u << 12;
    tcr2_read_xor = s2cr_trans_raz_mask = 0;
    writes = translations = ats_requests = 0;
    write_log_count = 0;
    clean_count = 0;
    reject_offset = ~0u;
    alloc_fail_after = -1;
}
static void map_one(struct adreno8_gpu_space *space) {
    assert(adreno8_space_new_table(space, 0, 0));
    assert(adreno8_space_map(space, 0x60001000, 0x90001000));
}
int main(void) {
    struct adreno8_gpu_space space;
    assert(GMU_CONTEXT == 2 && adreno8_gmu_space.context == 2 && SMR_MASK == 0);
    assert(SMMU_CBA2R(1) == 0x804 && SMMU_CBA2R(2) == 0x808);
    reset(&space, false);
    assert(adreno8_step_smmu_read());
    assert(reads[(0x20000 + SMMU_CB_SCTLR) / 4] == 1);
    assert(reads[(0x21000 + SMMU_CB_SCTLR) / 4] == 1);
    assert(reads[(0x22000 + SMMU_CB_SCTLR) / 4] == 1);
    assert(reads[(0x23000 + SMMU_CB_SCTLR) / 4] == 1);
    assert(writes == 0);
    /* AArch64 privileged instruction fetch requires AP_UNPRIV clear on writable pages. */
    assert((PTE_PAGE & (3u << 6)) == 0 && (PTE_PAGE & (3ULL << 53)) == 0);
    assert((PTE_PAGE & (3u << 10)) == (3u << 10) && (MAIR0_NORMAL & 255) == 0x44);
    assert(TCR_39_BITS == 0x803519 && TCR2_39_BITS == 0x38011);
    assert(STAGE_1 == 0x1f300);
    for (unsigned bit = 8; bit <= 9; bit++) {
        reset(&space, false);
        map_one(&space);
        s2cr_trans_raz_mask = 1u << bit;
        assert(!adreno8_space_on(&space, 5, 1));
        assert(!space.is_on && translations == 0);
    }
    /* ASIDPNE is optional; every enabled control bit must still read back. */
    const uint32_t required_sctlr[] = {1u, 2u, 4u, 32u};
    for (unsigned i = 0; i < sizeof(required_sctlr) / sizeof(required_sctlr[0]); i++) {
        reset(&space, false);
        map_one(&space);
        sctlr_raz_mask |= required_sctlr[i];
        assert(!adreno8_space_on(&space, 5, 1));
        assert(!space.is_on && translations == 0);
        assert(reads[(0x22000 + SMMU_CB_TTBR0 + 4) / 4] == 1);
        assert(reads[(0x22000 + SMMU_CB_MAIR0) / 4] == 1);
        assert(reads[(0x22000 + SMMU_CB_MAIR1) / 4] == 1);
    }
    const uint32_t tcr2_mismatches[] = {1u, 2u, 4u, 1u << 4, 1u << 7, 1u << 15, 1u << 16, 1u << 17};
    for (unsigned i = 0; i < sizeof(tcr2_mismatches) / sizeof(tcr2_mismatches[0]); i++) {
        reset(&space, false);
        map_one(&space);
        tcr2_read_xor = tcr2_mismatches[i];
        assert(!adreno8_space_on(&space, 5, 1));
        assert(!space.is_on && translations == 0);
        assert(reads[(0x22000 + SMMU_CB_TTBR1) / 4] == 1);
        assert(reads[(0x22000 + SMMU_CB_MAIR1) / 4] == 1);
    }
    /* Ordinary takeover keeps the conditions the step14b runs verified: an
     * active route into the requested context blocks it (reads only); a
     * fault leftover that only carries the context index does not block,
     * and neither does a non-zero context SCTLR. */
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(5) / 4] = 0x80000003;
    regs[SMMU_S2CR(5) / 4] = 2u;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(5) / 4] = 0x80000003;
    regs[SMMU_S2CR(5) / 4] = 0x20002u;
    assert(adreno8_space_on(&space, 5, 1));
    assert(space.is_on && translations == 1);
    adreno8_space_off(&space, 1);
    /* The device-read CB2 SCTLR 0x107c430c (enable bit clear, leftover TCR
     * stage fields) is the run001 step 9 reading. Ordinary initialization
     * must succeed with it and write exactly what the SCTLR=0 baseline
     * writes. */
    static struct write_record baseline[512];
    reset(&space, false);
    map_one(&space);
    assert(adreno8_space_on(&space, 5, 1));
    assert(space.is_on && write_log_count > 0);
    unsigned baseline_writes = write_log_count;
    memcpy(baseline, write_log, baseline_writes * sizeof(baseline[0]));
    adreno8_space_off(&space, 1);
    reset(&space, false);
    regs[(0x22000 + SMMU_CB_SCTLR) / 4] = 0x107c430c;
    map_one(&space);
    assert(adreno8_space_on(&space, 5, 1));
    assert(space.is_on && translations == 1);
    assert(write_log_count == baseline_writes);
    assert(memcmp(baseline, write_log, baseline_writes * sizeof(baseline[0])) == 0);
    adreno8_space_off(&space, 1);
    /* The reuse exception still requires a disabled context: the candidate
     * match holding this exact stream with a fault route but SCTLR
     * 0x107c430c rejects, reads only. */
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(1) / 4] = 0x80000005;
    regs[SMMU_S2CR(1) / 4] = 0x20300u;
    regs[(0x22000 + SMMU_CB_SCTLR) / 4] = 0x107c430c;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);
    /* Routing to another context does not conflict, even while active. */
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(5) / 4] = 0x80000003;
    regs[SMMU_S2CR(5) / 4] = 0x10004;
    assert(adreno8_space_on(&space, 5, 1));
    adreno8_space_off(&space, 1);
    reset(&space, false);
    map_one(&space);
    assert(adreno8_space_on(&space, 5, 1));
    assert(regs[(0x22000 + SMMU_CB_SCTLR) / 4] == 0x27);
    assert(regs[(0x22000 + SMMU_CB_TCR2) / 4] == 0x38071);
    assert(space.is_on && translations == 1);
    adreno8_space_off(&space, 1);
    assert(!space.is_on);
    reset(&space, false);
    alloc_fail_after = 0;
    assert(!adreno8_space_new_table(&space, 0, 0) && space.table_count == 0);
    alloc_fail_after = -1;
    assert(!adreno8_space_new_table(&space, 2, 0) && allocation_count == 0);
    assert(adreno8_space_new_table(&space, 0, 0));
    alloc_fail_after = 1;
    assert(!adreno8_space_map(&space, 0x3ffff000, 0x90000000) && space.table_count == 2);
    alloc_fail_after = -1;
    assert(adreno8_space_map(&space, 0x3ffff000, 0x90000000));
    assert(!adreno8_space_map(&space, 0x3ffff000, 0x90001000));
    assert(walk(space.tables[0].phys, 0x3ffff000) == 0x90000000);
    assert(!adreno8_space_map(&space, 0x40000001, 0x90000000));
    assert(!adreno8_space_map(&space, 0x40000000, 0x90000001));
    assert(!adreno8_space_map(&space, 0x40000000, 1ULL << 36));
    struct adreno8_gpu_block block = {"test", 0x3fffe000, 3 * PAGE_SIZE + 1, 0};
    assert(kmem_alloc(0, &block.memory, 4 * PAGE_SIZE, 0, 0) == 0);
    unsigned count = space.table_count;
    assert(!adreno8_space_map_block(&space, &block));
    assert(space.table_count == count && walk(space.tables[0].phys, 0x3fffe000) == 1);
    reset(&space, false);
    assert(adreno8_space_new_table(&space, 0, 0));
    assert(kmem_alloc(0, &block.memory, 4 * PAGE_SIZE, 0, 0) == 0);
    assert(adreno8_space_map_block(&space, &block));
    for (unsigned offset = 0; offset < block.size; offset += 4096) {
        assert(walk(space.tables[0].phys, block.address + offset) == kvtophys(block.memory + offset));
    }
    block.address = 0xfffff000;
    block.size = 4097;
    assert(!adreno8_space_map_block(&space, &block));
    assert(adreno8_space_on(&space, 5, 1));
    assert(space.is_on && ats_requests == 0 && translations == 1);
    assert(!adreno8_space_map(&space, 0x70000000, 0x90000000));
    adreno8_space_off(&space, 1);
    assert(!space.is_on && regs[SMMU_SMR(1) / 4] == 0x80000005);
    assert(regs[SMMU_S2CR(1) / 4] == 0x20300);
    assert(adreno8_space_on(&space, 5, 1));
    adreno8_space_off(&space, 1);
    assert(!space.is_on && translations == 2);
    reset(&space, true);
    map_one(&space);
    assert(adreno8_space_on(&space, 5, 1));
    assert(ats_requests == 1 && translations == 1);
    sync_ok = false;
    adreno8_space_off(&space, 1);
    assert(space.is_on);
    sync_ok = true;
    adreno8_space_off(&space, 1);
    assert(!space.is_on);
    reset(&space, true);
    map_one(&space);
    atos_ok = false;
    assert(!adreno8_space_on(&space, 5, 1));
    assert(!space.is_on && ats_requests == 1 && translations == 0);
    reset(&space, true);
    map_one(&space);
    atos_wait_ok = false;
    assert(!adreno8_space_on(&space, 5, 1));
    assert(space.is_on && translations == 0);
    atos_wait_ok = true;
    adreno8_space_off(&space, 1);
    assert(!space.is_on);
    reset(&space, false);
    map_one(&space);
    reject_offset = 0x1808;
    reject_value = 1;
    assert(!adreno8_space_on(&space, 5, 1));
    assert(!space.is_on && translations == 0);
    reset(&space, false);
    map_one(&space);
    sync_ok = false;
    assert(!adreno8_space_on(&space, 5, 1));
    assert(space.is_on && translations == 0);
    reset(&space, false);
    map_one(&space);
    reject_offset = SMMU_S2CR(1);
    reject_value = 0x20300;
    assert(!adreno8_space_on(&space, 5, 1));
    assert(space.is_on && translations == 0);
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(2) / 4] = 0x84000405;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SMR(2) / 4] = 0x80000010;
    regs[SMMU_S2CR(2) / 4] = GMU_CONTEXT;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);
    reset(&space, false);
    map_one(&space);
    ((uint64_t *)space.tables[0].table)[1] = 0;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);
    reset(&space, false);
    map_one(&space);
    regs[SMMU_SCR0 / 4] |= 1;
    assert(!adreno8_space_on(&space, 5, 1) && writes == 0);

    reset(&space, false);
    assert(adreno8_space_new_table(&space, 0, 0));
    vm_offset_t payload;
    assert(kmem_alloc(0, &payload, 2 * PAGE_SIZE, 0, 0) == KERN_SUCCESS);
    assert(adreno8_space_map_region(&space, 0x1ff000, payload, 2 * PAGE_SIZE,
                                   ADRENO8_MEM_READ | ADRENO8_MEM_EXEC));
    assert((*adreno8_space_entry(&space, 0x1ff000) & ~PTE_ADDRESS) == 0xec3);
    assert(walk(space.tables[0].phys, 0x200000) == kvtophys(payload + 4096));
    assert(walk(space.tables[0].phys, 0x1ff000 + PAGE_SIZE) == kvtophys(payload + PAGE_SIZE));
    uint32_t found;
    assert(adreno8_space_find_range(&space, 0x1ff000, 0x400000, 4096, 0x10000, &found));
    assert(found >= 0x1ff000 + 2 * PAGE_SIZE && (found & 0xffff) == 0);
    unsigned tables_before = space.table_count;
    assert(!adreno8_space_map_region(&space, 0x1ff000, payload, 4096, ADRENO8_MEM_READ));
    assert(!adreno8_space_map_region(&space, 0xfffff000, payload, 8192, ADRENO8_MEM_READ));
    assert(!adreno8_space_map_region(&space, 0x800000, payload, 4096, ADRENO8_MEM_WRITE));
    assert(space.table_count == tables_before);
    assert(adreno8_space_on(&space, 5, 1));
    clean_count = 0;
    assert(adreno8_space_map_region(&space, 0x800000, payload, 4096,
                                   ADRENO8_MEM_READ | ADRENO8_MEM_WRITE | ADRENO8_MEM_PRIV));
    assert(cleaned[0] == adreno8_space_table(&space, 2, 4)->table);
    assert(cleaned[1] == adreno8_space_table(&space, 1, 0)->table);
    assert((*adreno8_space_entry(&space, 0x800000) & ~PTE_ADDRESS) == (0xe03 | (3ULL << 53)));
    alloc_fail_after = 1;
    assert(!adreno8_space_map_region(&space, 0x3ffff000, payload, 8192, ADRENO8_MEM_READ));
    assert(adreno8_space_entry(&space, 0x3ffff000) != NULL);
    assert(*adreno8_space_entry(&space, 0x3ffff000) == 0);
    assert(adreno8_space_entry(&space, 0x40000000) == NULL);
    alloc_fail_after = -1;
    sync_ok = false;
    assert(!adreno8_space_map_region(&space, 0x801000, payload, 4096, ADRENO8_MEM_READ));
    assert(space.is_on && walk(space.tables[0].phys, 0x801000) == kvtophys(payload));
    sync_ok = true;
    adreno8_space_off(&space, 1);

    /* CB0 and CB2 retain separate stream ownership and ASIDs. */
    reset(&space, false);
    map_one(&space);
    assert(adreno8_space_on(&space, 5, 1));
    struct adreno8_gpu_space gpu = { .name = "gpu", .context = 0 };
    assert(adreno8_space_new_table(&gpu, 0, 0));
    assert(kmem_alloc(0, &payload, PAGE_SIZE, 0, 0) == KERN_SUCCESS);
    assert(adreno8_space_map_region(&gpu, 0x10000000, payload, 4096,
                                   ADRENO8_MEM_READ | ADRENO8_MEM_EXEC));
    assert(adreno8_space_on(&gpu, 0, 0));
    assert(regs[SMMU_S2CR(0) / 4] == 0x300 && regs[SMMU_S2CR(1) / 4] == 0x302);
    assert(regs[(0x20000 + SMMU_CB_TTBR0 + 4) / 4] >> 16 == 0);
    assert(regs[(0x22000 + SMMU_CB_TTBR0 + 4) / 4] >> 16 == 2);
    assert(regs[SMMU_SMR(2) / 4] == 0);

    /* Secure preflight for the candidate route (stream 2, match 2, CB 3):
     * every rejection is reads only, and bounds are checked before any MMIO
     * read. A pass reports register state, nothing was written. */
    unsigned reads_before = 0;
    reset(&space, false);
    assert(adreno8_secure_preflight());
    assert(writes == 0);
    assert(reads[(0x23000 + SMMU_CB_SCTLR) / 4] == 1);
    assert(reads[SMMU_SMR(2) / 4] == 1 && reads[SMMU_SMR(23) / 4] == 1);
    assert(reads[SMMU_S2CR(2) / 4] == 0);
    /* The device-read CB3 SCTLR 0x96cc51a is nonzero with the enable (M) bit
     * clear; only M rejects. With M set the strict pass still takes the
     * one-pass read-only view (each SMR once, S2CR only behind a valid SMR),
     * printing the invalid candidate slot with its S2CR explicitly unread. */
    reset(&space, false);
    regs[(0x23000 + SMMU_CB_SCTLR) / 4] = 1u;
    assert(!adreno8_secure_preflight() && writes == 0);
    assert(reads[(0x23000 + SMMU_CB_SCTLR) / 4] == 1);
    for (unsigned j = 0; j < 24; j++) {
        assert(reads[SMMU_SMR(j) / 4] == 1 && reads[SMMU_S2CR(j) / 4] == 0);
    }
    /* The same device-read value with the M bit clear satisfies the HLOS-side
     * conditions at the sample time; secure-world occupancy stays invisible. */
    reset(&space, false);
    regs[(0x23000 + SMMU_CB_SCTLR) / 4] = 0x96cc51au;
    assert(adreno8_secure_preflight() && writes == 0);
    assert(reads[(0x23000 + SMMU_CB_SCTLR) / 4] == 1);
    for (unsigned j = 0; j < 24; j++) {
        assert(reads[SMMU_SMR(j) / 4] == 1 && reads[SMMU_S2CR(j) / 4] == 0);
    }
    /* A routing conflict while the M bit rejects: the candidate slot taken and
     * a route into CB3 both print, the scan reaches the last match with
     * each register read once and no S2CR read for the other 22 invalid
     * slots, and the M-bit rejection still stands. */
    reset(&space, false);
    regs[(0x23000 + SMMU_CB_SCTLR) / 4] = 1u;
    regs[SMMU_SMR(2) / 4] = 0x80000002;
    regs[SMMU_S2CR(2) / 4] = 3u;
    regs[SMMU_SMR(5) / 4] = 0x80020000;
    regs[SMMU_S2CR(5) / 4] = 0x10003u;
    assert(!adreno8_secure_preflight() && writes == 0);
    for (unsigned j = 0; j < 24; j++) {
        assert(reads[SMMU_SMR(j) / 4] == 1 &&
               reads[SMMU_S2CR(j) / 4] == (j == 2 || j == 5 ? 1 : 0));
    }
    /* The kernel's own GMU takeover route (exact SID5 -> context 2, as
     * stage 9 writes it) neither names context 3 nor covers stream 2, so the
     * HLOS-side conditions hold, yet the route still prints with its raw
     * values; post-analysis can tell a real view from degraded all-invalid
     * reads. It counts in scanned and valid only. */
    reset(&space, false);
    regs[(0x23000 + SMMU_CB_SCTLR) / 4] = 0x96cc51au;
    regs[SMMU_SMR(6) / 4] = 0x80000005;
    regs[SMMU_S2CR(6) / 4] = 0x302u;   /* SHCFG_NSH | context 2, the exact value stage 9 writes */
    assert(adreno8_secure_preflight() && writes == 0);
    for (unsigned j = 0; j < 24; j++) {
        assert(reads[SMMU_SMR(j) / 4] == 1 &&
               reads[SMMU_S2CR(j) / 4] == (j == 6 ? 1 : 0));
    }
    reset(&space, false);
    regs[SMMU_SMR(2) / 4] = 0x80000002;
    regs[SMMU_S2CR(2) / 4] = 3u;
    assert(!adreno8_secure_preflight() && writes == 0);
    assert(reads[SMMU_S2CR(2) / 4] == 1);
    reset(&space, false);
    regs[SMMU_SMR(5) / 4] = 0x80000002;
    regs[SMMU_S2CR(5) / 4] = 0x20003u;
    assert(!adreno8_secure_preflight() && writes == 0);
    reset(&space, false);
    regs[SMMU_SMR(5) / 4] = 0x80000004;
    regs[SMMU_S2CR(5) / 4] = 3u;
    assert(!adreno8_secure_preflight() && writes == 0);
    /* Fault and bypass leftovers that route to context 3 from a slot whose
     * SMR does not cover stream 2 still reject under strict rules; the
     * ordinary path would let both through. */
    const uint32_t cb3_route_leftovers[] = {0x20003u, 0x10003u};
    for (unsigned i = 0; i < 2; i++) {
        reset(&space, false);
        regs[SMMU_SMR(5) / 4] = 0x80000004;
        regs[SMMU_S2CR(5) / 4] = cb3_route_leftovers[i];
        assert(!adreno8_secure_preflight() && writes == 0);
    }
    reset(&space, false);
    regs[SMMU_SMR(5) / 4] = 0x80000004;
    regs[SMMU_S2CR(5) / 4] = 0x20004u;
    /* An exact entry that does not match stream 2 and does not route to
     * context 3 is fine; the pass needs both halves. */
    assert(adreno8_secure_preflight() && writes == 0);
    /* Stream 2 active on the candidate slot but routed to context 4: the
     * candidate slot is taken, so the preflight rejects. */
    reset(&space, false);
    regs[SMMU_SMR(2) / 4] = 0x80000002;
    regs[SMMU_S2CR(2) / 4] = 0x10004u;
    assert(!adreno8_secure_preflight() && writes == 0);
    assert(reads[SMMU_S2CR(2) / 4] == 1);
    /* A range match whose mask covers stream 2 rejects even when it routes
     * to another context. */
    reset(&space, false);
    regs[SMMU_SMR(5) / 4] = 0x80020000;
    regs[SMMU_S2CR(5) / 4] = 0x10004u;
    assert(!adreno8_secure_preflight() && writes == 0);
    assert(reads[SMMU_SMR(5) / 4] == 1 && reads[SMMU_S2CR(5) / 4] == 1);
    /* An exact match on stream 2 sitting on a non-candidate slot rejects
     * while routing to context 4: the stream is taken either way. */
    reset(&space, false);
    regs[SMMU_SMR(5) / 4] = 0x80000002;
    regs[SMMU_S2CR(5) / 4] = 0x10004u;
    assert(!adreno8_secure_preflight() && writes == 0);
    assert(reads[SMMU_S2CR(5) / 4] == 1);
    /* Candidate slot beyond the SMR count: rejected with no MMIO read at all. */
    reset(&space, false);
    regs[SMMU_IDR0 / 4] = 0x4c017e02;
    assert(adreno8_step_smmu_read());
    for (unsigned i = 0; i < sizeof(reads) / sizeof(reads[0]); i++) reads_before += reads[i];
    assert(!adreno8_secure_preflight() && writes == 0);
    unsigned reads_after = 0;
    for (unsigned i = 0; i < sizeof(reads) / sizeof(reads[0]); i++) reads_after += reads[i];
    assert(reads_after == reads_before);
    release_all();
    puts("SMMU mapping, register writes, ATOS capability and failure cleanup: PASS");
}
'''
if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="adreno8-smmu-") as directory:
        target = Path(directory)
        (target / "adreno8_private.h").write_text(stub)
        (target / "adreno8_memory_private.h").write_text((ROOT / "xnu/files/bsd/dev/adreno8_memory_private.h").read_text())
        (target / "adreno8_smmu.c").write_text(driver)
        (target / "check.c").write_text('#include "adreno8_smmu.c"\n' + tests)
        for page_size in (4096, 16384):
            binary = target / f"check-{page_size}"
            subprocess.run(["cc", "-std=c11", f"-DPAGE_SIZE={page_size}", "-fsanitize=address,undefined",
                            "-fno-omit-frame-pointer", str(target / "check.c"), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            lines = result.stdout.splitlines()
            out = "\n".join(lines)
            summaries = [l for l in lines if "preflight routing sample at t=" in l]
            rejects = [i for i, l in enumerate(lines) if "has the M bit set; the context bank fails the preflight" in l]
            stands = [i for i, l in enumerate(lines) if "the M-bit rejection stands" in l]
            inval = [i for i, l in enumerate(lines) if "invalid; s2cr not read" in l]
            entries = [i for i, l in enumerate(lines)
                       if "preflight routing sample: match" in l and "invalid" not in l]
            # Fourteen strict preflight runs: each prints exactly one summary.
            # Verdicts: two M-bit rejections, eight routing conflicts, four
            # satisfied. Two M-set scenarios (the M-set leftover, the M-set
            # conflict) each keep the rejection line first, at most one sample
            # line per probed slot, and close with their own summary.
            assert len(summaries) == 14, len(summaries)
            assert len(rejects) == 2 and len(stands) == 2, (len(rejects), len(stands))
            assert out.count("a routing conflict rejects the candidate") == 8
            assert out.count("HLOS-side conditions are satisfied at the sample time") == 4
            assert out.count("context 3 satisfied the HLOS-side conditions") == 4
            for r, s in zip(rejects, stands):
                assert r < s and not [t for t in rejects if r < t < s]
                assert len([i for i in inval + entries if r < i < s]) <= 24
            assert out.count("match 2 smr 0 invalid; s2cr not read") == 11
            assert len(inval) == 11 and len(entries) == 12
            assert "match 2 smr 0x80000002 s2cr 0x3" in out
            assert "match 5 smr 0x80020000 s2cr 0x10003" in out
            assert "match 6 smr 0x80000005 s2cr 0x302" in out
            assert "scanned 24, valid 0, 0 on" in lines[stands[0]], lines[stands[0]]
            assert "scanned 24, valid 2, 2 on" in lines[stands[1]], lines[stands[1]]
            own_gmu = [l for l in summaries if "scanned 24, valid 1, 0 on" in l]
            assert len(own_gmu) == 2 and all("are satisfied at the sample time" in l for l in own_gmu), own_gmu
            for l in lines:
                if "preflight" in l:
                    print(l)
            print(f"XNU PAGE_SIZE={page_size}: PASS")
