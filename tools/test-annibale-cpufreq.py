#!/usr/bin/env python3
"""Run annibale's SCMI performance requests and temperature watch against a fake CPUCP on the host."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "xnu/files/bsd/dev/redwood_cpufreq.c").read_text()
block = text[text.index("/*\n * Annibale (SM8750) has no EPSS"):text.index("/* On the way back to the loader")]

source = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
typedef int boolean_t, wait_result_t, kern_return_t;
typedef void *thread_t;
typedef void (*thread_continue_t)(void *, wait_result_t);
typedef struct { uint32_t phys_id; } ml_topology_cpu_t;
typedef struct { unsigned int num_cpus; ml_topology_cpu_t cpus[2]; } ml_topology_info_t;
#define FALSE 0
#define KERN_SUCCESS 0
#define NSEC_PER_USEC 1000ULL
#define NSEC_PER_MSEC 1000000ULL
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define __builtin_arm_rsr64(name) 0x10100ULL
#define __builtin_arm_dsb(kind) ((void)0)

static uint32_t shmem[0x80], doorbell[0xc0], tsens[3][0x80];
static uint32_t version = 0x40000, levels = 20, batch = 8, boot_level = 2, sustained = 17, indexing = 1;
static uint32_t level[2], sets, set_domain[16], set_level[16], deny_set, never_free;
static uint32_t mode, membench, threads, seconds, stop_after, benches;
static int32_t schedule[64];            /* cluster 1 temperature for each second, tenths; < -1000: invalid */
static void *thread_arg;
static uint64_t now_ns;
static jmp_buf out_of_thread;
static ml_topology_info_t topology = { 2, { { 0x10100 }, { 0x10000 } } };

static int tell(const char *format, ...) { (void)format; return 0; }
#define printf tell
static const ml_topology_info_t *ml_get_topology_info(void) { return &topology; }
static boolean_t ml_set_interrupts_enabled(boolean_t on) { (void)on; return 1; }
static uint64_t mach_absolute_time(void) { return now_ns++; }
static void absolutetime_to_nanoseconds(uint64_t a, uint64_t *n) { *n = a; }
static void nanoseconds_to_absolutetime(uint64_t n, uint64_t *a) { *a = n; }
static void memory_bench(void) { benches++; }
static void thread_deallocate(thread_t t) { (void)t; }
static kern_return_t kernel_thread_start(thread_continue_t f, void *arg, thread_t *t) { (void)f; thread_arg = arg; *t = 0; threads++; return 0; }
static bool PE_parse_boot_argn(const char *name, void *value, int size)
{
    (void)size;
    if (!strcmp(name, "cpufreq") && mode) { *(uint32_t *)value = mode; return true; }
    if (!strcmp(name, "membench") && membench) { *(uint32_t *)value = membench; return true; }
    return false;
}
static vm_offset_t ml_io_map(vm_offset_t phys, unsigned long size)
{
    (void)size;
    switch (phys) {
    case 0x17b4e000: return (vm_offset_t)shmem;
    case 0x17830000: return (vm_offset_t)doorbell;
    case 0x0c228000: return (vm_offset_t)tsens[0];
    case 0x0c229000: return (vm_offset_t)tsens[1];
    case 0x0c22a000: return (vm_offset_t)tsens[2];
    }
    assert(0);
    return 0;
}
static void set_temperature(unsigned group, int32_t tenths)
{
    for (unsigned s = 0; s < 0x18; s++)
        tsens[group][(0xa0 + 4 * s) / 4] = tenths < -1000 ? 0 : (1u << 21) | ((uint32_t)tenths & 0xfff);
}

/* The platform: answers whatever is in the channel once the doorbell rang. */
static void firmware(void)
{
    uint32_t header = shmem[6], message = header & 0xff, *in = &shmem[7], *out = &shmem[8], words = 0;
    int32_t status = 0;

    if (never_free || (shmem[1] & 1) || doorbell[0x41] == 0)
        return;
    doorbell[0x41] = 0;
    assert(((header >> 10) & 0xff) == 0x13 && shmem[4] == 0);
    switch (message) {
    case 0x0: out[0] = version; words = 1; break;
    case 0x1: out[0] = 2; words = 4; break;
    case 0x3:
        out[0] = 0x6a000000 & (indexing ? ~0u : ~(1u << 25)); out[1] = 0; out[2] = 3100000; out[3] = sustained;
        memcpy(&out[4], in[0] ? "cpu6\0\0\0\0\0\0\0\0\0\0\0\0" : "cpu0\0\0\0\0\0\0\0\0\0\0\0\0", 16);
        words = 8; break;
    case 0x4: {
        uint32_t entry = version >= 0x40000 ? 5 : 3, first = in[1], n = MIN(batch, levels - first);
        out[0] = n | (levels - first - n) << 16;
        for (uint32_t i = 0; i < n; i++) {
            out[1 + i * entry] = 300000 + (first + i) * 200000;     /* the top of 20: 4100000 */
            out[2 + i * entry] = 7;
            if (entry == 5) { out[4 + i * entry] = out[1 + i * entry]; out[5 + i * entry] = first + i; }
        }
        words = 1 + n * entry; break;
    }
    case 0x6: out[0] = 4100000; out[1] = 300000; words = 2; break;
    case 0x7:
        if (deny_set) { status = -3; break; }
        if (indexing ? in[1] >= levels : (in[1] < 300000 || (in[1] - 300000) % 200000 || (in[1] - 300000) / 200000 >= levels)) {
            status = -2; break;                                     /* (a value where an index is wanted, or no level) */
        }
        set_domain[sets] = in[0]; set_level[sets++] = in[1]; level[in[0]] = in[1]; break;
    case 0x8: out[0] = level[in[0]]; words = 1; break;
    default: status = -1;
    }
    shmem[7] = (uint32_t)status;
    shmem[5] = 8 + 4 * words;
    shmem[1] = 1;
}

static void delay_for_interval(uint32_t interval, uint64_t scale)
{
    if (interval == 1000 && scale == NSEC_PER_MSEC) {
        if (++seconds > stop_after)
            longjmp(out_of_thread, 1);
        set_temperature(1, schedule[seconds]);
        return;
    }
    firmware();
}

static void reset(uint32_t m)
{
    memset(shmem, 0, sizeof(shmem));
    shmem[1] = 1;
    level[0] = level[1] = boot_level;
    sets = deny_set = never_free = seconds = threads = benches = membench = 0;
    version = 0x40000; levels = 20; batch = 8; sustained = 17; indexing = 1;
    mode = m;
    memset(perf_domain, 0, sizeof(perf_domain));
    for (unsigned g = 0; g < 3; g++) set_temperature(g, 400);
}

static void run_thread(uint32_t until)
{
    stop_after = until;
    if (!setjmp(out_of_thread))
        thermal_thread(thread_arg, 0);
}

int main(void)
{
    int32_t t;

    /* only reports: nothing is set, the watch runs without control */
    reset(2);
    annibale_cpufreq();
    assert(sets == 0 && threads == 1 && thread_arg == NULL && perf_domain[1].top == 17 && perf_domain[1].boot_level == boot_level);
    assert(perf_domain[1].ours && !perf_domain[0].ours);
    for (unsigned i = 0; i < 64; i++) schedule[i] = 990;
    run_thread(40);
    assert(sets == 0);

    /* raised at boot when cool, only the cluster with our cores */
    reset(1);
    annibale_cpufreq();
    assert(sets == 1 && set_domain[0] == 1 && set_level[0] == 17 && perf_domain[1].raised && thread_arg != NULL);

    /* hot: back to the boot loader's level; 92 C: stays there; below 90 C: up again */
    for (unsigned i = 0; i < 64; i++) schedule[i] = 600;
    schedule[3] = 950; schedule[4] = 920; schedule[5] = 905; schedule[6] = 899;
    run_thread(8);
    assert(sets == 3 && set_level[1] == boot_level && set_level[2] == 17 && perf_domain[1].raised);

    /* no valid reading while raised: down, and not up again without one */
    reset(1);
    annibale_cpufreq();
    for (unsigned i = 0; i < 64; i++) schedule[i] = -2000;
    run_thread(5);
    assert(sets == 2 && set_level[1] == boot_level && !perf_domain[1].raised);

    /* too warm at boot: not raised until it cools */
    reset(1);
    set_temperature(1, 930);
    annibale_cpufreq();
    assert(sets == 0);
    for (unsigned i = 0; i < 64; i++) schedule[i] = i < 3 ? 930 : 700;
    run_thread(4);
    assert(sets == 1 && set_level[0] == 17);

    /* a sustained level that is not among those described: the top one */
    reset(1);
    sustained = 99;
    annibale_cpufreq();
    assert(sets == 1 && set_level[0] == 19);

    /* a refused request is not repeated every second */
    reset(1);
    deny_set = 1;
    annibale_cpufreq();
    assert(sets == 0 && perf_domain[1].refused);
    for (unsigned i = 0; i < 64; i++) schedule[i] = 500;
    run_thread(10);
    assert(sets == 0);

    /* SCMI 3.x levels (three words each, by value), one per answer; the sustained one among them, then not */
    reset(1);
    version = 0x30000; batch = 1; indexing = 0; sustained = 3100000; level[1] = 1100000;
    annibale_cpufreq();
    assert(sets == 1 && set_level[0] == 3100000 && perf_domain[1].boot_level == 1100000);
    reset(1);
    version = 0x30000; batch = 1; levels = 5; indexing = 0; sustained = 3100000;
    annibale_cpufreq();
    assert(perf_domain[1].top == 300000 + 4 * 200000 && sets == 1 && set_level[0] == 1100000);

    /* 4.0 without level indexing: values */
    reset(1);
    indexing = 0; sustained = 3100000;
    annibale_cpufreq();
    assert(sets == 1 && set_level[0] == 3100000);

    /* a channel that never comes free: no level, no hang */
    reset(1);
    shmem[1] = 0; never_free = 1;
    annibale_cpufreq();
    assert(sets == 0 && threads == 0 && !perf_domain[1].known);

    /* membench=1 still runs the memory test */
    reset(2);
    membench = 1;
    annibale_cpufreq();
    assert(benches == 1);

    /* readings: tenths, signed, valid bit */
    set_temperature(2, -55);
    assert(tsens_hottest(2, &t) && t == -55);
    set_temperature(2, -2000);
    assert(!tsens_hottest(2, &t));
    puts("annibale cpufreq: ok");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / "t.c"
    exe = Path(tmp) / "t"
    head = source[:source.index("static void firmware(void)")]
    tail = source[source.index("static void firmware(void)"):]
    # the driver's statics (perf_domain, ...) must exist before the fake firmware and reset() use them
    forward = "static void delay_for_interval(uint32_t interval, uint64_t scale);\n"
    c.write_text(head + forward + block + tail)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wno-unused-function", "-o", str(exe), str(c)], check=True)
    subprocess.run([str(exe)], check=True)
