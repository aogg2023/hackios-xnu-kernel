#!/usr/bin/env python3
"""Drive production MP_ASSIGN transfers against scripted SCM outcomes."""
from pathlib import Path
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "tools/test-adreno8-smmu.py"))
stub = fixture["stub"].replace("assert(size == 4096);", "assert(size != 0);")
poison_hook = """static vm_offset_t poison_va; /* when set, kvtophys fails for that page */
static uint64_t kvtophys(vm_offset_t va) {
    if (poison_va && va >= poison_va && va < poison_va + PAGE_SIZE) return 0;"""
stub = stub.replace("static uint64_t kvtophys(vm_offset_t va) {", poison_hook, 1)
assert "poison_va" in stub
driver = (ROOT / "xnu/files/bsd/dev/adreno8_secure.c").read_text()
setup = fixture["tests"].split("int main(void) {")[0]
fakes = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
int32_t adreno8_scm_call(uint64_t function, uint64_t arginfo, uint64_t argument0,
    uint64_t argument1, uint64_t argument2, uint64_t argument3, uint64_t *answer);
void adreno8_sleep_ms(uint32_t ms);
struct wire_region { uint64_t address, size; };
struct wire_perm { uint32_t vmid, perm; uint64_t context; uint32_t context_size, unused; };
static struct scm_call { uint64_t function, arginfo, args[4]; } calls[80];
static struct wire_region snap[80][32];
static unsigned call_count, clean_at_call[80];
static int32_t outcomes[80][2];
static uint64_t outcome_answers[80];
static unsigned outcome_count, outcome_next;
static unsigned sleeps, sleeps_total;
static void script(int32_t status, uint64_t answer) {
    assert(outcome_count < 80);
    outcomes[outcome_count][0] = status;
    outcome_answers[outcome_count] = answer;
    outcome_count++;
}
int32_t adreno8_scm_call(uint64_t function, uint64_t arginfo, uint64_t argument0,
    uint64_t argument1, uint64_t argument2, uint64_t argument3, uint64_t *answer)
{
    assert(call_count < 80);
    assert(function == 0x42000c16ull && arginfo == 0x1117ull);
    calls[call_count] = (struct scm_call){function, arginfo, {argument0, argument1, argument2, argument3}};
    clean_at_call[call_count] = clean_count;
    for (unsigned i = 0; i < argument1 / 16; i++) {
        struct wire_region *live = (struct wire_region *)(uintptr_t)physical_table(argument0);
        snap[call_count][i] = live[i];
    }
    unsigned pick = outcome_next < outcome_count ? outcome_next : outcome_count - 1;
    *answer = outcome_answers[pick];
    int32_t status = outcomes[pick][0];
    if (outcome_next < outcome_count) outcome_next++;
    call_count++;
    return status;
}
void adreno8_sleep_ms(uint32_t ms) { sleeps++; sleeps_total += ms; }
static struct wire_region *staged(uint64_t page_phys) {
    return (struct wire_region *)physical_table(page_phys);
}
static struct wire_perm *staged_perms(uint64_t page_phys) {
    return (struct wire_perm *)((uintptr_t)physical_table(page_phys) + 576);
}
static uint64_t *staged_extended(uint64_t page_phys) {
    return (uint64_t *)((uintptr_t)physical_table(page_phys) + 640);
}
static uint32_t *staged_sources(uint64_t page_phys) {
    return (uint32_t *)((uintptr_t)physical_table(page_phys) + 512);
}
static void check_envelope(unsigned call, unsigned regions) {
    struct scm_call *c = &calls[call];
    assert(c->args[0] == kvtophys((vm_offset_t)(uintptr_t)physical_table(c->args[0])));
    assert(c->args[1] == regions * 16);
    assert(c->args[2] == c->args[0] + 512 && c->args[3] == c->args[0] + 640);
    assert(*staged_sources(c->args[0]) == 3);
    uint64_t *ext = staged_extended(c->args[0]);
    assert(ext[0] == 4 && ext[1] == c->args[0] + 576);
    assert(clean_at_call[call] >= 1); /* the page was cleaned before this call */
}
'''
tests = r'''
static struct adreno8_memory make_buffer(unsigned pages) {
    struct adreno8_memory buffer;
    vm_offset_t memory;
    assert(kmem_alloc(0, &memory, (vm_size_t)pages * PAGE_SIZE, 0, 0) == KERN_SUCCESS);
    memset(&buffer, 0, sizeof(buffer));
    buffer.name = "test";
    buffer.memory = memory;
    buffer.size = pages * PAGE_SIZE;
    return buffer;
}
static void check_regions(unsigned call, struct adreno8_memory *buffer, unsigned pages,
    unsigned first_page)
{
    struct scm_call *c = &calls[call];
    struct wire_region *regions = snap[call];
    unsigned seen = 0;
    assert(c->args[1] % 16 == 0);
    seen = c->args[1] / 16;
    /* The fixture gives every kernel page a non-neighbor physical address. */
    assert(seen == pages);
    for (unsigned i = 0; i < seen; i++) {
        assert(regions[i].address == kvtophys(buffer->memory + (first_page + i) * PAGE_SIZE));
        assert(regions[i].size == PAGE_SIZE);
    }
}
int main(int argc, char **argv) {
    struct adreno8_memory buffer;
    bool answer_mode = argc > 1 && strcmp(argv[1], "answer") == 0;
    bool waitq_mode = argc > 1 && strcmp(argv[1], "waitq") == 0;

    if (answer_mode) {
        script(0, 5);
        buffer = make_buffer(1);
        assert(!adreno8_secure_data(&buffer));
        assert(call_count == 1 && adreno8_secure_lost());
        puts("MP_ASSIGN nonzero answer retained: PASS");
        return 0;
    }
    if (waitq_mode) {
        script(2, 0);
        buffer = make_buffer(1);
        assert(!adreno8_secure_data(&buffer));
        assert(call_count == 1 && sleeps == 0 && adreno8_secure_lost());
        buffer = make_buffer(1);
        assert(!adreno8_secure_data(&buffer) && call_count == 1);
        puts("MP_ASSIGN WAITQ retained without continuation: PASS");
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "poison") == 0) {
        /* An unprovable physical page retains every buffer page until cold reset,
         * whether the failing page sits before the first batch or after one. */
        bool after_batch = argc > 2 && strcmp(argv[2], "boundary") == 0;
        script(0, 0);
        buffer = make_buffer(after_batch ? ASSIGN_SECTIONS + 8 : 2);
        poison_va = buffer.memory + (after_batch ? ASSIGN_SECTIONS : 0) * PAGE_SIZE;
        struct adreno8_gpu_space space = { .name = "secure", .context = SECURE_CONTEXT };
        assert(adreno8_space_new_table(&space, 0, 0));
        assert(!adreno8_secure_data(&buffer));
        assert(adreno8_secure_lost());
        assert(call_count == (after_batch ? 1u : 0u));
        assert(!adreno8_secure_data(&buffer) && !adreno8_secure_page_tables(&space));
        assert(call_count == (after_batch ? 1u : 0u));
        printf("MP_ASSIGN retained after %s-batch physical check failure: PASS\n",
            after_batch ? "boundary" : "pre");
        return 0;
    }

    /* The argument page allocation is retryable and never sets the retained flag. */
    buffer = make_buffer(1);
    alloc_fail_after = 0;
    assert(!adreno8_secure_data(&buffer) && !adreno8_secure_lost());
    alloc_fail_after = -1;
    script(0, 0);
    assert(adreno8_secure_data(&buffer));
    check_envelope(0, 1);
    check_regions(0, &buffer, 1, 0);

    /* Data pages hand over to CP_PIXEL read/write with the full envelope. */
    script(0, 0);
    buffer = make_buffer(3);
    assert(adreno8_secure_data(&buffer));
    check_envelope(1, 3);
    check_regions(1, &buffer, 3, 0);
    struct wire_perm *perms = staged_perms(calls[1].args[0]);
    assert(perms[0].vmid == 10 && perms[0].perm == 6 && perms[0].context == 0);
    assert(staged_extended(calls[1].args[0])[2] == 24);

    /* Batching: 70 pages become 32 + 32 + 6 region entries. */
    script(0, 0); script(0, 0); script(0, 0);
    buffer = make_buffer(70);
    assert(adreno8_secure_data(&buffer));
    assert(calls[2].args[1] == 32 * 16 && calls[3].args[1] == 32 * 16 && calls[4].args[1] == 6 * 16);
    check_regions(2, &buffer, 32, 0);
    check_regions(3, &buffer, 32, 32);
    check_regions(4, &buffer, 6, 64);

    /* Page tables: {HLOS read/write, CP_PIXEL read}, only pages past the cursor. */
    script(0, 0);
    struct adreno8_gpu_space space = { .name = "secure", .context = SECURE_CONTEXT };
    assert(adreno8_space_new_table(&space, 0, 0));
    assert(adreno8_space_new_table(&space, 1, 0));
    assert(adreno8_space_new_table(&space, 2, 0));
    assert(adreno8_secure_page_tables(&space));
    check_envelope(5, 3);
    perms = staged_perms(calls[5].args[0]);
    assert(perms[0].vmid == 3 && perms[0].perm == 6);
    assert(perms[1].vmid == 10 && perms[1].perm == 4);
    assert(staged_extended(calls[5].args[0])[2] == 48);
    for (unsigned i = 0; i < 3; i++) {
        assert(staged(calls[5].args[0])[i].address == space.tables[i].phys);
        assert(staged(calls[5].args[0])[i].size == PAGE_SIZE);
    }
    assert(space.secure_tables == 3);
    script(0, 0);
    assert(adreno8_space_new_table(&space, 2, 1));
    assert(adreno8_secure_page_tables(&space));
    assert(calls[6].args[1] == 16 && staged(calls[6].args[0])[0].address == space.tables[3].phys);
    assert(space.secure_tables == 4);

    /* -EBUSY retries at the vendor's 30 ms spacing, then succeeds. */
    script(-12, 0); script(0, 0);
    buffer = make_buffer(1);
    assert(adreno8_secure_data(&buffer));
    assert(call_count == 9 && sleeps == 1 && sleeps_total == 30);

    /* EBUSY retries exhausted: pages retained, every later call refuses. */
    for (unsigned i = 0; i < 20; i++) script(-12, 0);
    buffer = make_buffer(1);
    assert(!adreno8_secure_data(&buffer));
    assert(adreno8_secure_lost());
    unsigned calls_before = call_count;
    assert(!adreno8_secure_data(&buffer) && !adreno8_secure_page_tables(&space));
    assert(call_count == calls_before);
    release_all();
    puts("MP_ASSIGN batching, envelopes, page-table cursor and retention: PASS");
}
'''
if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="adreno8-secure-") as directory:
        target = Path(directory)
        (target / "adreno8_private.h").write_text(stub)
        for name in ("adreno8_memory_private.h", "adreno8_secure_private.h"):
            (target / name).write_text((ROOT / "xnu/files/bsd/dev" / name).read_text())
        (target / "adreno8_smmu.c").write_text(fixture["driver"])
        (target / "adreno8_secure.c").write_text(driver)
        (target / "check.c").write_text('#include "adreno8_smmu.c"\n' + fakes +
            '#include "adreno8_secure.c"\n' + setup + tests)
        for page_size in (4096, 16384):
            binary = target / f"check-{page_size}"
            subprocess.run(["cc", "-std=c11", f"-DPAGE_SIZE={page_size}", "-fsanitize=address,undefined",
                            "-fno-omit-frame-pointer", str(target / "check.c"), "-o", str(binary)], check=True)
            for mode in ((), ("answer",), ("waitq",), ("poison", "pre"), ("poison", "boundary")):
                subprocess.run([str(binary), *mode], check=True)
            print(f"XNU PAGE_SIZE={page_size}: PASS")
