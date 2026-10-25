/*
 * The clock speed of the processor cores the kernel runs on, on the POCO X5 Pro (redwood).
 * Only with boot-arg cpufreq=1: the addresses are this phone's.
 *
 * The boot loader leaves each group of cores at a modest frequency and expects the operating
 * system to ask for more. On this SoC that is one register write: the hardware ("EPSS") has
 * a table of frequencies per group and switches the clock source and the supply voltage by
 * itself when told which row is wanted. There is no reason for us ever to want less than the
 * top row, for every group that has a core of ours in it (one core, unless boot-arg cpus=N
 * says more).
 *
 * Register layout: Linux drivers/cpufreq/qcom-cpufreq-hw.c ("epss" variant).
 *
 * The REDMI K90 (annibale) asks its CPUCP firmware over SCMI instead, with a temperature
 * watch: annibale_cpufreq() below.
 */
#include <kern/clock.h>
#include <kern/thread.h>
#include <os/atomic_private.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>
#include <vm/vm_kern.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern void loader_log_putc(char c);
extern void redwood_tl(const char *format, ...) __printflike(1, 2);
extern bool ml_cpu_idle_time(int cpu_id, uint64_t *idle);

/*
 * The loader's log as well: this runs early, and the kernel's own log of a run keeps only its end. With
 * interrupts off -- on the way back to the loader (redwood_ddr_quiesce()) -- the loader's log only: the
 * kernel's console may not be written there, and printf was a panic ("Console I/O from interrupt-disabled
 * context") at the end of every run once the memory vote was taken away on the way back.
 */
static void
tell(const char *format, ...) __printflike(1, 2);

static void
tell(const char *format, ...)
{
	char line[256];
	va_list list;

	va_start(list, format);
	vsnprintf(line, sizeof(line), format, list);
	va_end(list);
	for (const char *c = line; *c != 0; c++) {
		loader_log_putc(*c);
	}
	if (ml_get_interrupts_enabled()) {
		printf("%s", line);
	}
}

#define EPSS_PHYS               0x18591000UL    /* three groups ("domains") of 0x1000: small cores, big, biggest */
#define EPSS_DOMAIN_SIZE        0x1000UL
#define EPSS_DOMAINS            3
#define EPSS_FREQ_TABLE         0x100           /* a word per row: [31:30] clock source, [7:0] multiple of 19.2 MHz */
#define EPSS_TABLE_ROWS         40
#define EPSS_PERF_STATE         0x320           /* the row that is wanted */
#define XO_KHZ                  19200
#define ALTERNATE_KHZ           300000          /* source 0: a fixed 300 MHz */

/* How fast a simple loop runs, in thousands of iterations per millisecond. */
static uint32_t
loop_speed(void)
{
	volatile uint64_t sink = 0;
	uint64_t start = mach_absolute_time(), nanos;

	for (uint32_t i = 0; i < 2000000; i++) {
		sink += i;
	}
	absolutetime_to_nanoseconds(mach_absolute_time() - start, &nanos);
	return nanos ? (uint32_t)(2000000ULL * 1000 / nanos) : 0;
}

/* The group of the core with that number in its MPIDR (the device tree's "reg": the number is the second field). */
static uint32_t
domain_of(uint32_t phys_id)
{
	uint32_t core = (phys_id >> 8) & 0xff;

	return core < 4 ? 0 : core < 7 ? 1 : 2;
}

static void
raise_domain(uint32_t domain, uint32_t cores)
{
	uint32_t top = 0, top_khz = 0, was;
	vm_offset_t base;

	base = ml_io_map(EPSS_PHYS + domain * EPSS_DOMAIN_SIZE, EPSS_DOMAIN_SIZE);
	for (uint32_t row = 0; row < EPSS_TABLE_ROWS; row++) {
		uint32_t entry = *(volatile uint32_t *)(base + EPSS_FREQ_TABLE + 4 * row);
		uint32_t khz = (entry >> 30) ? XO_KHZ * (entry & 0xff) : ALTERNATE_KHZ;

		if (row > 0 && khz <= top_khz) {
			break;                  /* the table ends by repeating its last row */
		}
		top = row;
		top_khz = khz;
	}
	was = *(volatile uint32_t *)(base + EPSS_PERF_STATE);
	*(volatile uint32_t *)(base + EPSS_PERF_STATE) = top;
	delay_for_interval(20, NSEC_PER_MSEC);
	tell("cpufreq: group %u (%u of our cores): row %u -> %u of the frequency table, %u MHz\n", domain, cores, was, top,
	    top_khz / 1000);
}

/*
 * Boot-arg membench=1: how fast this processor reads, writes and copies memory, in MB a second
 * (32 MB at a time, far more than any cache): what the memory's clock allows a processor, which
 * nobody here asks to be raised.
 */
static void
memory_bench(void)
{
	const vm_size_t size = 32u << 20;
	vm_offset_t a = 0, b = 0;
	uint64_t began, ns[3] = { 0, 0, 0 };
	volatile uint64_t sum = 0;

	if (kmem_alloc(kernel_map, &a, size, KMA_KOBJECT | KMA_DATA, VM_KERN_MEMORY_DIAG) != KERN_SUCCESS ||
	    kmem_alloc(kernel_map, &b, size, KMA_KOBJECT | KMA_DATA, VM_KERN_MEMORY_DIAG) != KERN_SUCCESS) {
		printf("membench: no memory\n");
		return;
	}
	memset((void *)a, 1, size);
	memset((void *)b, 2, size);
	for (int round = 0; round < 4; round++) {
		uint64_t t;

		began = mach_absolute_time();
		memset((void *)a, round, size);
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		ns[0] += t;
		began = mach_absolute_time();
		for (const uint64_t *p = (const uint64_t *)b, *end = (const uint64_t *)(b + size); p < end; p += 8) {
			sum += p[0] + p[1] + p[2] + p[3] + p[4] + p[5] + p[6] + p[7];
		}
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		ns[1] += t;
		began = mach_absolute_time();
		memcpy((void *)b, (const void *)a, size);
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		ns[2] += t;
	}
	tell("membench: on core %llu: writing %llu MB/s, reading %llu MB/s, copying %llu MB/s\n",
	    (__builtin_arm_rsr64("MPIDR_EL1") >> 8) & 0xff, 4ull * (size >> 20) * 1000000000ull / ns[0],
	    4ull * (size >> 20) * 1000000000ull / ns[1], 4ull * (size >> 20) * 1000000000ull / ns[2]);
	kmem_free(kernel_map, a, size);
	kmem_free(kernel_map, b, size);
}

/*
 * Boot-arg membench=2: the same on every core of ours at once (a thread each, started together, 16 MB
 * at a time), 2 s after this starts, so that all the cores are up: the sum of what they get is what
 * the memory gives at the clock it has been left at.
 */
#define BENCH_THREADS   8
static struct {
	volatile uint32_t ready, go, done;
	uint64_t ns[BENCH_THREADS][3];
	uint32_t core[BENCH_THREADS];
} bench;

static void
bench_thread(void *arg, wait_result_t wr __unused)
{
	const vm_size_t size = 16u << 20;
	uintptr_t index = (uintptr_t)arg;
	vm_offset_t a = 0, b = 0;
	volatile uint64_t sum = 0;

	if (kmem_alloc(kernel_map, &a, size, KMA_KOBJECT | KMA_DATA, VM_KERN_MEMORY_DIAG) != KERN_SUCCESS ||
	    kmem_alloc(kernel_map, &b, size, KMA_KOBJECT | KMA_DATA, VM_KERN_MEMORY_DIAG) != KERN_SUCCESS) {
		os_atomic_inc(&bench.ready, relaxed);
		os_atomic_inc(&bench.done, relaxed);
		return;
	}
	memset((void *)a, 1, size);
	memset((void *)b, 2, size);
	os_atomic_inc(&bench.ready, relaxed);
	while (!os_atomic_load(&bench.go, acquire)) {
	}
	bench.core[index] = (uint32_t)(__builtin_arm_rsr64("MPIDR_EL1") >> 8) & 0xff;
	for (int round = 0; round < 4; round++) {
		uint64_t began, t;

		began = mach_absolute_time();
		memset((void *)a, round, size);
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		bench.ns[index][0] += t;
		began = mach_absolute_time();
		for (const uint64_t *p = (const uint64_t *)b, *end = (const uint64_t *)(b + size); p < end; p += 8) {
			sum += p[0] + p[1] + p[2] + p[3] + p[4] + p[5] + p[6] + p[7];
		}
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		bench.ns[index][1] += t;
		began = mach_absolute_time();
		memcpy((void *)b, (const void *)a, size);
		absolutetime_to_nanoseconds(mach_absolute_time() - began, &t);
		bench.ns[index][2] += t;
	}
	os_atomic_inc(&bench.done, release);
	kmem_free(kernel_map, a, size);
	kmem_free(kernel_map, b, size);
}

static void
bench_all(void *arg __unused, wait_result_t wr __unused)
{
	const uint32_t threads = MIN(ml_get_topology_info()->num_cpus, BENCH_THREADS);
	const uint64_t mb = 4 * 16;                     /* per thread and test */
	uint64_t total[3] = { 0, 0, 0 };

	delay_for_interval(2000, NSEC_PER_MSEC);
	for (uintptr_t i = 0; i < threads; i++) {
		thread_t thread;

		if (kernel_thread_start(bench_thread, (void *)i, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			os_atomic_inc(&bench.ready, relaxed);
			os_atomic_inc(&bench.done, relaxed);
		}
	}
	while (os_atomic_load(&bench.ready, relaxed) < threads) {
		delay_for_interval(1, NSEC_PER_MSEC);
	}
	os_atomic_store(&bench.go, 1, release);
	while (os_atomic_load(&bench.done, acquire) < threads) {
		delay_for_interval(1, NSEC_PER_MSEC);
	}
	for (uint32_t i = 0; i < threads; i++) {
		tell("membench: all at once: core %u: writing %llu MB/s, reading %llu MB/s, copying %llu MB/s\n", bench.core[i],
		    bench.ns[i][0] ? mb * 1000000000ull / bench.ns[i][0] : 0, bench.ns[i][1] ? mb * 1000000000ull / bench.ns[i][1] : 0,
		    bench.ns[i][2] ? mb * 1000000000ull / bench.ns[i][2] : 0);
		for (int k = 0; k < 3; k++) {
			total[k] += bench.ns[i][k] ? mb * 1000000000ull / bench.ns[i][k] : 0;
		}
	}
	tell("membench: all %u at once, together: writing %llu MB/s, reading %llu MB/s, copying %llu MB/s\n", threads, total[0],
	    total[1], total[2]);
}

/*
 * Boot-arg cpuload=MS: every MS milliseconds, how busy each of our cores was meanwhile, in
 * percent, to the loader's log (tools/frames.py shows it per gesture):
 *   tl: u at US 7:NN 6:NN ... 1:NN      the core's number (as in its MPIDR), then its share
 * of the time it was not idle (interrupts included).
 */
static uint32_t load_every_ms;

static void
load_thread(void *arg __unused, wait_result_t wr __unused)
{
	const ml_topology_info_t *topology = ml_get_topology_info();
	uint64_t idle_before[MAX_CPUS] = { 0 }, then = mach_absolute_time();

	for (;;) {
		char line[192];
		int at;
		uint64_t now, us;

		delay_for_interval(load_every_ms, NSEC_PER_MSEC);
		now = mach_absolute_time();
		absolutetime_to_nanoseconds(now, &us);
		at = snprintf(line, sizeof(line), "tl: u at %llu", us / 1000);
		for (unsigned int i = 0; i < topology->num_cpus && i < MAX_CPUS; i++) {
			uint64_t idle = 0, busy;

			if (!ml_cpu_idle_time((int)i, &idle)) {
				continue;
			}
			busy = (now - then) > (idle - idle_before[i]) ? (now - then) - (idle - idle_before[i]) : 0;
			idle_before[i] = idle;
			if (at < (int)sizeof(line) - 12) {
				at += snprintf(line + at, sizeof(line) - at, " %u:%llu", (topology->cpus[i].phys_id >> 8) & 0xff,
				    100 * busy / (now - then));
			}
		}
		then = now;
		redwood_tl("%s\n", line);
	}
}

static void
load_start(void)
{
	thread_t thread;

	if (!PE_parse_boot_argn("cpuload", &load_every_ms, sizeof(load_every_ms)) || load_every_ms == 0) {
		return;
	}
	if (kernel_thread_start(load_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	}
}

/*
 * Boot-arg ddr=1: the memory's bus clock managers ("BCM") in the command database the boot loader
 * left, with what is needed to vote for bandwidth on them -- only read. (membench=2 says the cores
 * together get no more from memory than one alone: 12 GB/s; nobody here votes for more.)
 */
#define CMD_DB_PHYS             0x80860000UL
#define CMD_DB_SIZE             0x20000UL
#define CMD_DB_BCM              5               /* the kind of resource in a slave's id: bus clock managers */

struct cmd_db_slave {
	uint16_t id, header_offset, data_offset, count, version, reserved[3];
};
struct cmd_db_header {
	uint32_t version;
	uint8_t magic[4];
	struct cmd_db_slave slave[8];
	uint32_t checksum, reserved;
	uint8_t data[];
};
struct cmd_db_entry {
	char id[8];
	uint32_t priority[2];
	uint32_t address;
	uint16_t length, offset;
};
struct cmd_db_bcm {                             /* a BCM's auxiliary data (Linux: struct bcm_db) */
	uint32_t unit;
	uint16_t width;
	uint8_t vcd, reserved;
};

/* (the same condition as the cores' clocks: the addresses are this phone's) */
static bool
cpufreq_wanted(void)
{
	uint32_t on = 0;

	return PE_parse_boot_argn("cpufreq", &on, sizeof(on)) && on != 0;
}

static void
ddr_look(void)
{
	/* (memory, not registers: mapped so that reads of any size and alignment are allowed) */
	const struct cmd_db_header *db = (const struct cmd_db_header *)ml_io_map_wcomb(CMD_DB_PHYS, CMD_DB_SIZE);

	if (db->magic[0] != 0xdb || db->magic[1] != 0x30 || db->magic[2] != 0x03 || db->magic[3] != 0x0c) {
		tell("ddr: no command database\n");
		return;
	}
	for (unsigned s = 0; s < 8; s++) {
		const struct cmd_db_slave *slave = &db->slave[s];
		const struct cmd_db_entry *entry = (const struct cmd_db_entry *)(db->data + slave->header_offset);

		if (slave->id != CMD_DB_BCM || slave->header_offset >= CMD_DB_SIZE - 0x1000 || slave->count > 512) {
			continue;
		}
		for (unsigned i = 0; i < slave->count; i++) {
			struct cmd_db_bcm aux = { 0 };
			char name[9] = { 0 };

			memcpy(name, entry[i].id, 8);
			if (entry[i].length >= sizeof(aux) && slave->data_offset + entry[i].offset + sizeof(aux) < CMD_DB_SIZE - 0x40) {
				memcpy(&aux, db->data + slave->data_offset + entry[i].offset, sizeof(aux));
			}
			tell("ddr: BCM %-8s at %#x: unit %u, width %u, vcd %u (aux %u bytes)\n", name, entry[i].address, aux.unit,
			    aux.width, aux.vcd, entry[i].length);
		}
	}
}

/*
 * ... and what the boot loader's firmware left in the application processor's RPMh command slots
 * ("DRV 2"; Linux: rpmh-rsc.c): 2 active, 3 sleep, 3 wake slot sets and a control one, of 16 commands
 * each (sc7280's qcom,tcs-config): every command with an address, and which of them go to the BCMs
 * above -- the bandwidth it asked for, and that is in force whenever the processors are awake.
 */
#define RSC_PHYS                0x18220000UL
#define RSC_SIZE                0x10000UL
#define RSC_TCS(n, reg)         (0xd00 + 0x2a0 * (n) + (reg))
#define RSC_TCS_COUNT           9
#define RSC_CMDS                16

static void
ddr_slots(void)
{
	vm_offset_t rsc = ml_io_map(RSC_PHYS, RSC_SIZE);

	for (unsigned t = 0; t < RSC_TCS_COUNT; t++) {
		uint32_t enabled = *(volatile uint32_t *)(rsc + RSC_TCS(t, 0x1c));

		for (unsigned c = 0; c < RSC_CMDS; c++) {
			uint32_t msgid = *(volatile uint32_t *)(rsc + RSC_TCS(t, 0x30 + 20 * c));
			uint32_t address = *(volatile uint32_t *)(rsc + RSC_TCS(t, 0x34 + 20 * c));
			uint32_t data = *(volatile uint32_t *)(rsc + RSC_TCS(t, 0x38 + 20 * c));

			if (address == 0 && data == 0) {
				continue;
			}
			tell("ddr: slot set %u (%s) command %u%s: message %#x, address %#x, data %#x%s\n", t,
			    t < 2 ? "active" : t < 5 ? "sleep" : t < 8 ? "wake" : "control", c, enabled & (1u << c) ? " (on)" : "",
			    msgid, address, data, (address >> 16 & 0xf) == CMD_DB_BCM ? " <- a BCM" : "");
		}
	}
}

/*
 * The memory's bandwidth (on by default; boot-arg ddr=0: not): a vote through RPMh -- the most there
 * is -- on the bus clock managers of the memory controller (MC0: the memory's clock) and of the
 * shared hub in front of it with the system cache (SH0), as Linux's interconnect driver casts for
 * a busy GPU (icc-bcm-voter.c: a BCM command is commit, valid, the average and the peak, 14 bits each,
 * in the BCM's units; the BCM takes the highest of its levels the vote reaches), and the memory kept
 * on for this processor (ACV, bit 3, which the boot loader's firmware had set already, the same).
 * All cores together got 12.4 GB/s writing without it (membench=2: what one core gets alone), 14.8
 * with it; frames over 16.7 ms 28% instead of 34-36%. ddr_vote=N: N instead of the most (16383).
 * When the kernel goes back to the loader (redwood_ddr_quiesce(), on every way back, a panic's too)
 * the vote goes down to ddr_rest=N (default 1024). What the firmware had voted cannot be read back,
 * but what the cores get from memory can be measured (membench=2, writing): with any vote from 4
 * to 4096 12.0 to 12.4 GB/s, as before the first vote (12.4); with the most, 15.4; with none at all
 * ("no request", valid clear: what Linux sends for nothing) 0.2 -- the memory at its floor, and the
 * loader's USB transfers at 8 MB/s instead of 30. So the firmware had a vote of its own in our name,
 * of that middle range, and one from the middle of it is left.
 */
#define BCM_COMMIT              (1u << 30)
#define BCM_VALID               (1u << 29)
#define BCM_VOTE(x, y)          (BCM_COMMIT | BCM_VALID | ((uint32_t)(x) & 0x3fff) << 14 | ((uint32_t)(y) & 0x3fff))
#define TCS_REG(reg)            (ddr_rsc + 0xd00 + (reg))       /* command slot set 0, an "active" one */
#define BCM_ACV_APPS            8
#define DDR_REST                1024            /* the vote left for the loader (ddr_rest=N); see redwood_ddr_quiesce() */

static vm_offset_t ddr_rsc;
static uint32_t ddr_mc0, ddr_sh0, ddr_acv;
static bool ddr_voted;

static uint32_t
cmd_db_address(const char *name)
{
	const struct cmd_db_header *db = (const struct cmd_db_header *)ml_io_map_wcomb(CMD_DB_PHYS, CMD_DB_SIZE);
	char id[8] = { 0 };

	if (db->magic[0] != 0xdb || db->magic[1] != 0x30 || db->magic[2] != 0x03 || db->magic[3] != 0x0c) {
		return 0;
	}
	strlcpy(id, name, sizeof(id));
	for (unsigned s = 0; s < 8; s++) {
		const struct cmd_db_slave *slave = &db->slave[s];
		const struct cmd_db_entry *entry = (const struct cmd_db_entry *)(db->data + slave->header_offset);

		if (slave->id != CMD_DB_BCM || slave->header_offset >= CMD_DB_SIZE - 0x1000 || slave->count > 512) {
			continue;
		}
		for (unsigned i = 0; i < slave->count; i++) {
			if (memcmp(entry[i].id, id, sizeof(id)) == 0) {
				return entry[i].address;
			}
		}
	}
	return 0;
}

/* Up to three writes through command slot set 0, and the wait until RPMh has done them (as bsd/dev/redwood_goodix.c) */
static bool
ddr_rpmh_write(const uint32_t *address, const uint32_t *data, unsigned count)
{
	uint32_t irq_enable = *(volatile uint32_t *)TCS_REG(0x00);
	bool done = false;

	for (int i = 0; *(volatile uint32_t *)TCS_REG(0x18) == 0; i++) {
		if (i > 2000) {
			tell("ddr: RPMh: the command slots stay busy\n");
			return false;
		}
		delay_for_interval(100, NSEC_PER_USEC);
	}
	*(volatile uint32_t *)TCS_REG(0x14) &= ~(1u << 24);
	*(volatile uint32_t *)TCS_REG(0x14) &= ~(1u << 16);
	*(volatile uint32_t *)TCS_REG(0x08) = 1;
	for (unsigned i = 0; i < count; i++) {
		*(volatile uint32_t *)TCS_REG(0x30 + 20 * i) = 0x10008 | 0x100;        /* a 4-byte write, with a response */
		*(volatile uint32_t *)TCS_REG(0x34 + 20 * i) = address[i];
		*(volatile uint32_t *)TCS_REG(0x38 + 20 * i) = data[i];
	}
	*(volatile uint32_t *)TCS_REG(0x10) = (1u << count) - 1;
	*(volatile uint32_t *)TCS_REG(0x1c) = (1u << count) - 1;
	*(volatile uint32_t *)TCS_REG(0x00) = irq_enable | 1;
	*(volatile uint32_t *)TCS_REG(0x14) = 1u << 16;
	*(volatile uint32_t *)TCS_REG(0x14) = 1u << 16 | 1u << 24;
	for (int i = 0; i < 2000 && !done; i++) {
		done = (*(volatile uint32_t *)TCS_REG(0x04) & 1) != 0;
		if (!done) {
			delay_for_interval(100, NSEC_PER_USEC);
		}
	}
	*(volatile uint32_t *)TCS_REG(0x14) &= ~(1u << 24);
	*(volatile uint32_t *)TCS_REG(0x14) &= ~(1u << 16);
	*(volatile uint32_t *)TCS_REG(0x1c) = 0;
	*(volatile uint32_t *)TCS_REG(0x10) = 0;
	*(volatile uint32_t *)TCS_REG(0x08) = 1;
	*(volatile uint32_t *)TCS_REG(0x00) = irq_enable;
	if (!done) {
		tell("ddr: RPMh: no answer to the vote\n");
	}
	return done;
}

static bool
ddr_vote(uint32_t vote)
{
	uint32_t address[3] = { ddr_mc0, ddr_sh0, ddr_acv };
	uint32_t data[3] = { BCM_VOTE(vote, vote), BCM_VOTE(vote, vote), BCM_VOTE(0, BCM_ACV_APPS) };

	return ddr_rpmh_write(address, data, 3);
}

static void
ddr_raise(void)
{
	uint32_t vote = 0x3fff;

	PE_parse_boot_argn("ddr_vote", &vote, sizeof(vote));
	ddr_mc0 = cmd_db_address("MC0");
	ddr_sh0 = cmd_db_address("SH0");
	ddr_acv = cmd_db_address("ACV");
	if (ddr_mc0 == 0 || ddr_sh0 == 0 || ddr_acv == 0 || vote == 0 || vote > 0x3fff) {
		tell("ddr: no vote (MC0 %#x, SH0 %#x, ACV %#x, vote %u)\n", ddr_mc0, ddr_sh0, ddr_acv, vote);
		return;
	}
	ddr_rsc = ml_io_map(RSC_PHYS, RSC_SIZE);
	ddr_voted = ddr_vote(vote);
	tell("ddr: memory bandwidth vote %u on MC0 (%#x) and SH0 (%#x)%s\n", vote, ddr_mc0, ddr_sh0, ddr_voted ? "" : ": FAILED");
}

/*
 * Annibale (SM8750) has no EPSS registers for its cores: the CPUCP firmware sets their clocks
 * and voltages, and asks to be told a performance level through SCMI's performance protocol
 * (0x13), one message at a time in a channel of shared memory, with a mailbox doorbell after
 * each. Domain 0 is the six cores of cluster 0, domain 1 the two of cluster 1 (stock device
 * tree: CPU0-5 clocks = <&scmi_perf 0>, CPU6-7 <&scmi_perf 1>).
 *
 * Boot-arg cpufreq=2 only reports: the protocol, every domain with its levels and the level it
 * is at, how fast a chain of adds runs, and every 30 s the temperatures. cpufreq=1 also asks
 * for the sustained level (the highest the firmware says can be kept up; the top one if it names
 * none of those it describes) of each domain with a core of ours, and watches that cluster's temperature
 * once a second: at 95 C (the stock thermal-hal-config trip of cpu-1-*) the domain goes back
 * to the level the boot loader left, below 90 C (that trip's 5 C hysteresis) up again;
 * without a valid reading it is not raised.
 *
 * Channel layout: Linux drivers/firmware/arm_scmi/shmem.c; doorbell: drivers/mailbox/
 * qcom-cpucp-mbox.c (TX_MBOX_CMD(chan) + 4; any value); addresses: the stock device tree
 * (mmio-sram@0x17b4e000, qcom,cpucp@0x17830000, channel 0 for "tx").
 */
#define SCMI_SHMEM_PHYS         0x17b4e000UL
#define SCMI_SHMEM_SIZE         0x200U
#define SCMI_DOORBELL_PHYS      0x17830000UL
#define SCMI_DOORBELL_SIZE      0x300UL
#define SCMI_DOORBELL           (0x100 + 4)     /* channel 0 */
#define SHMEM_STATUS            0x04            /* bit 0: free (the platform is done with it), bit 1: error */
#define SHMEM_FLAGS             0x10            /* bit 0: an interrupt when done; we poll */
#define SHMEM_LENGTH            0x14            /* bytes of header and payload */
#define SHMEM_HEADER            0x18
#define SHMEM_PAYLOAD           0x1c
#define SHMEM_WORDS             ((SCMI_SHMEM_SIZE - SHMEM_PAYLOAD) / 4)
#define SCMI_PERF               0x13
#define PERF_VERSION            0x0
#define PERF_ATTRIBUTES         0x1
#define PERF_DOMAIN_ATTRIBUTES  0x3
#define PERF_DESCRIBE_LEVELS    0x4
#define PERF_LIMITS_GET         0x6
#define PERF_LEVEL_SET          0x7
#define PERF_LEVEL_GET          0x8
#define PERF_LEVEL_SET_ALLOWED  (1u << 30)
#define PERF_LEVEL_INDEXING     (1u << 25)      /* (4.0) levels are asked for and told by index, as on annibale */
#define SCMI_BUSY               (-100)          /* the channel never came free */
#define SCMI_TIMEOUT            (-101)          /* no answer */
#define SCMI_CHANNEL_ERROR      (-102)
#define SCMI_WRONG_ANSWER       (-103)          /* the answer is to another message */
#define PERF_DOMAINS            2

/* The temperature sensors (Linux drivers/thermal/qcom/tsens-v2.c: a status word a sensor, the last
 * reading in its low 12 bits, signed, in tenths of a degree, bit 21 set when valid). */
#define TSENS_STATUS(sensor)    (0xa0 + 4 * (sensor))
#define TSENS_VALID             (1u << 21)
#define HOT_TENTHS              950
#define COOL_TENTHS             900
static const struct {
	const char *name;
	vm_offset_t phys;
	uint32_t first, last;   /* sensors (stock thermal-zones: cpu-0-*, cpuss-0-*; cpu-1-*, cpuss-1-*; gpuss-*) */
} tsens_groups[] = {
	{ "cluster 0", 0x0c228000, 1, 14 },
	{ "cluster 1", 0x0c229000, 1, 6 },
	{ "GPU", 0x0c22a000, 1, 8 },
};
#define TSENS_GROUPS            (sizeof(tsens_groups) / sizeof(tsens_groups[0]))

static volatile uint32_t *scmi_shmem, *scmi_doorbell;
static uint32_t scmi_token;
static vm_offset_t tsens_base[TSENS_GROUPS];
static struct {
	bool ours, known, raised, refused;
	uint32_t boot_level, top;       /* as LEVEL_SET takes them: index or value */
} perf_domain[PERF_DOMAINS];        /* (domain N: cluster N, whose sensors are tsens_groups[N]) */

/* Adds that each wait for the one before: about the core's clock in MHz. */
static uint32_t
chain_mhz(void)
{
	uint64_t x = 0, start, nanos;
	boolean_t enabled = ml_set_interrupts_enabled(FALSE);

	start = mach_absolute_time();
	for (uint32_t i = 0; i < 10000; i++) {
		__asm__ volatile (".rept 1000\n\tadd %0, %0, #1\n\t.endr" : "+r"(x));
	}
	absolutetime_to_nanoseconds(mach_absolute_time() - start, &nanos);
	ml_set_interrupts_enabled(enabled);
	return nanos ? (uint32_t)(10000000ULL * 1000 / nanos) : 0;
}

static bool
scmi_wait_free(uint32_t ms)
{
	uint64_t deadline;

	nanoseconds_to_absolutetime((uint64_t)ms * NSEC_PER_MSEC, &deadline);
	deadline += mach_absolute_time();
	while ((scmi_shmem[SHMEM_STATUS / 4] & 3) == 0) {
		if (mach_absolute_time() > deadline) {
			return false;
		}
		delay_for_interval(10, NSEC_PER_USEC);
	}
	return true;
}

/* One command: `in_words` out; on return *words (at most what it was) back in `out`. The SCMI status
 * (0: success), or one of SCMI_BUSY and the others above. */
static int32_t
scmi_call(uint32_t message, const uint32_t *in, uint32_t in_words, uint32_t *out, uint32_t *words)
{
	uint32_t header = message | SCMI_PERF << 10 | (scmi_token++ & 0x3ff) << 18, got;

	if (!scmi_wait_free(10)) {
		return SCMI_BUSY;
	}
	scmi_shmem[SHMEM_STATUS / 4] = 0;
	scmi_shmem[SHMEM_FLAGS / 4] = 0;
	scmi_shmem[SHMEM_LENGTH / 4] = 4 + 4 * in_words;
	scmi_shmem[SHMEM_HEADER / 4] = header;
	for (uint32_t i = 0; i < in_words; i++) {
		scmi_shmem[SHMEM_PAYLOAD / 4 + i] = in[i];
	}
	__builtin_arm_dsb(0xf);
	*scmi_doorbell = 1;
	if (!scmi_wait_free(50)) {
		return SCMI_TIMEOUT;
	}
	if (scmi_shmem[SHMEM_STATUS / 4] & 2) {
		return SCMI_CHANNEL_ERROR;
	}
	if (scmi_shmem[SHMEM_HEADER / 4] != header) {
		return SCMI_WRONG_ANSWER;
	}
	got = scmi_shmem[SHMEM_LENGTH / 4];
	got = got >= 8 ? (got - 8) / 4 : 0;
	got = MIN(got, MIN(*words, SHMEM_WORDS - 1));
	for (uint32_t i = 0; i < got; i++) {
		out[i] = scmi_shmem[SHMEM_PAYLOAD / 4 + 1 + i];
	}
	*words = got;
	return (int32_t)scmi_shmem[SHMEM_PAYLOAD / 4];
}

static int32_t
perf_level(uint32_t domain, uint32_t *level)
{
	uint32_t words = 1;
	int32_t status = scmi_call(PERF_LEVEL_GET, &domain, 1, level, &words);

	return status == 0 && words < 1 ? SCMI_WRONG_ANSWER : status;
}

static int32_t
perf_set(uint32_t domain, uint32_t level)
{
	uint32_t in[2] = { domain, level }, words = 0;

	return scmi_call(PERF_LEVEL_SET, in, 2, NULL, &words);
}

/* The hottest valid reading of a group, in tenths of a degree; false if none was valid. */
static bool
tsens_hottest(unsigned group, int32_t *tenths)
{
	bool any = false;

	for (uint32_t s = tsens_groups[group].first; s <= tsens_groups[group].last; s++) {
		uint32_t status = *(volatile uint32_t *)(tsens_base[group] + TSENS_STATUS(s));
		int32_t t = (int32_t)(status << 20) >> 20;

		if ((status & TSENS_VALID) && (!any || t > *tenths)) {
			*tenths = t;
			any = true;
		}
	}
	return any;
}

static void
temperatures(char *line, size_t size)
{
	int at = 0;

	line[0] = 0;
	for (unsigned g = 0; g < TSENS_GROUPS && at < (int)size; g++) {
		int32_t t;

		at += tsens_hottest(g, &t) ? snprintf(line + at, size - at, "%s%s %d.%d C", g ? ", " : "", tsens_groups[g].name, t / 10,
		    (t < 0 ? -t : t) % 10) : snprintf(line + at, size - at, "%s%s no reading", g ? ", " : "", tsens_groups[g].name);
	}
}

/* Its attributes, its levels (the one to ask for kept), the level it is at and its limits, all to the log. */
static void
perf_describe(uint32_t domain, uint32_t version)
{
	uint32_t out[SHMEM_WORDS], words, attributes, sustained, levels = 0, top = 0, top_key = 0, target, level = 0, limits[2] = { 0, 0 };
	uint32_t entry = version >= 0x40000 ? 5 : 3;    /* words a level takes (4.0 adds a frequency and an index) */
	char name[17] = { 0 }, list[200];
	bool indexing, found = false;
	int32_t status;
	int at = 0;

	words = 8;
	status = scmi_call(PERF_DOMAIN_ATTRIBUTES, &domain, 1, out, &words);
	if (status != 0 || words < 4) {
		tell("cpufreq: SCMI domain %u: no attributes (status %d)\n", domain, status);
		return;
	}
	attributes = out[0];
	sustained = out[3];
	indexing = entry == 5 && (attributes & PERF_LEVEL_INDEXING);
	if (words >= 8) {
		memcpy(name, &out[4], 16);
	}
	tell("cpufreq: SCMI domain %u \"%s\": attributes %#x, rate limit %u us, sustained %u kHz at level %u\n", domain, name,
	    attributes, out[1], out[2], sustained);
	list[0] = 0;
	for (uint32_t index = 0; levels < 256;) {
		uint32_t in[2] = { domain, index }, returned, remaining;

		words = SHMEM_WORDS - 1;
		status = scmi_call(PERF_DESCRIBE_LEVELS, in, 2, out, &words);
		if (status != 0 || words < 1) {
			break;
		}
		returned = out[0] & 0xfff;
		remaining = out[0] >> 16;
		if (returned == 0 || 1 + returned * entry > words) {
			break;
		}
		for (uint32_t i = 0; i < returned; i++) {
			const uint32_t *e = &out[1 + i * entry];
			uint32_t key = indexing ? e[4] : e[0], khz = entry == 5 && e[3] != 0 ? e[3] : e[0];

			if (levels + i == 0 || e[0] > top) {
				top = e[0];
				top_key = key;
			}
			found = found || key == sustained;
			if (at < (int)sizeof(list) - 16) {
				at += snprintf(list + at, sizeof(list) - at, " %u/%u", khz / 1000, key);
			}
		}
		levels += returned;
		index += returned;
		if (remaining == 0) {
			break;
		}
	}
	status = perf_level(domain, &level);
	words = 2;
	scmi_call(PERF_LIMITS_GET, &domain, 1, limits, &words);
	target = found ? sustained : top_key;
	tell("cpufreq: SCMI domain %u: %u levels (MHz/%s):%s\n", domain, levels, indexing ? "index" : "value", list);
	tell("cpufreq: SCMI domain %u: at %u (status %d), limits %u to %u; would ask for %u\n", domain, level, status,
	    limits[1], limits[0], target);
	if (domain < PERF_DOMAINS && status == 0 && levels != 0 && (attributes & PERF_LEVEL_SET_ALLOWED)) {
		perf_domain[domain].known = true;
		perf_domain[domain].boot_level = level;
		perf_domain[domain].top = target;
	}
}

static void
perf_raise(uint32_t domain, bool up)
{
	uint32_t want = up ? perf_domain[domain].top : perf_domain[domain].boot_level, now = 0;
	int32_t status = perf_set(domain, want), got;

	delay_for_interval(20, NSEC_PER_MSEC);
	got = perf_level(domain, &now);
	perf_domain[domain].raised = up && status == 0;
	perf_domain[domain].refused = up && status != 0;     /* (not asked again every second) */
	tell("cpufreq: SCMI domain %u: asked for level %u (status %d), now at %u (status %d); a chain of adds at %u MHz\n",
	    domain, want, status, now, got, chain_mhz());
}

static void
thermal_thread(void *arg, wait_result_t wr __unused)
{
	bool control = arg != NULL;

	for (uint32_t second = 1;; second++) {
		delay_for_interval(1000, NSEC_PER_MSEC);
		for (uint32_t d = 0; control && d < PERF_DOMAINS; d++) {
			int32_t t = 0;
			bool valid = tsens_hottest(d, &t);

			if (!perf_domain[d].ours || !perf_domain[d].known || perf_domain[d].refused) {
				continue;
			}
			if (perf_domain[d].raised && (!valid || t >= HOT_TENTHS)) {
				tell("cpufreq: %s at %d.%d C%s: back to the boot loader's level\n", tsens_groups[d].name, t / 10,
				    (t < 0 ? -t : t) % 10, valid ? "" : " (no valid reading)");
				perf_raise(d, false);
			} else if (!perf_domain[d].raised && valid && t < COOL_TENTHS) {
				perf_raise(d, true);
			}
		}
		if (second % 30 == 0) {
			char line[160];

			temperatures(line, sizeof(line));
			printf("cpufreq: %s\n", line);
		}
	}
}

static void
annibale_cpufreq(void)
{
	const ml_topology_info_t *topology = ml_get_topology_info();
	uint32_t mode = 0, out[4], words, version, domains, bench = 0;
	char line[160];
	thread_t thread;
	int32_t status;

	if (!PE_parse_boot_argn("cpufreq", &mode, sizeof(mode)) || (mode != 1 && mode != 2)) {
		return;
	}
	for (unsigned g = 0; g < TSENS_GROUPS; g++) {
		tsens_base[g] = ml_io_map(tsens_groups[g].phys, 0x200);
	}
	for (unsigned int i = 0; i < topology->num_cpus; i++) {
		uint32_t cluster = (topology->cpus[i].phys_id >> 16) & 0xff;

		if (cluster < PERF_DOMAINS) {
			perf_domain[cluster].ours = true;
		}
	}
	scmi_shmem = (volatile uint32_t *)ml_io_map(SCMI_SHMEM_PHYS, SCMI_SHMEM_SIZE);
	scmi_doorbell = (volatile uint32_t *)(ml_io_map(SCMI_DOORBELL_PHYS, SCMI_DOORBELL_SIZE) + SCMI_DOORBELL);
	temperatures(line, sizeof(line));
	tell("cpufreq: %s; SCMI channel status %#x; a chain of adds at %u MHz on core %#llx\n", line, scmi_shmem[SHMEM_STATUS / 4],
	    chain_mhz(), __builtin_arm_rsr64("MPIDR_EL1") & 0xffffff);
	words = 1;
	status = scmi_call(PERF_VERSION, NULL, 0, out, &words);
	if (status != 0 || words < 1) {
		tell("cpufreq: SCMI performance protocol: no answer (status %d)\n", status);
		return;
	}
	version = out[0];
	words = 4;
	status = scmi_call(PERF_ATTRIBUTES, NULL, 0, out, &words);
	domains = status == 0 && words >= 1 ? out[0] & 0xffff : 0;
	tell("cpufreq: SCMI performance protocol %u.%u, %u domains (status %d)\n", version >> 16, version & 0xffff, domains, status);
	for (uint32_t d = 0; d < domains && d < 4; d++) {
		perf_describe(d, version);
	}
	for (uint32_t d = 0; mode == 1 && d < PERF_DOMAINS; d++) {
		int32_t t;

		if (perf_domain[d].ours && perf_domain[d].known && tsens_hottest(d, &t) && t < COOL_TENTHS) {
			perf_raise(d, true);
		}
	}
	if (PE_parse_boot_argn("membench", &bench, sizeof(bench)) && bench == 1) {
		memory_bench();
	}
	if (kernel_thread_start(thermal_thread, mode == 1 ? (void *)1 : NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	}
}

/* On the way back to the loader (osfmk/arm64/loader_return.c: loader_return(), whatever the reason; the other processors are stopped) */
void redwood_ddr_quiesce(void);

void
redwood_ddr_quiesce(void)
{
	uint32_t address[2] = { ddr_mc0, ddr_sh0 };
	uint32_t rest = DDR_REST, data[2];
	bool done;

	if (!ddr_voted) {
		return;
	}
	ddr_voted = false;
	PE_parse_boot_argn("ddr_rest", &rest, sizeof(rest));
	rest = MIN(rest, 0x3fff);
	data[0] = data[1] = rest != 0 ? BCM_VOTE(rest, rest) : BCM_COMMIT;     /* 0: no request */
	done = ddr_rpmh_write(address, data, 2);
	tell("ddr: memory bandwidth vote %u for the loader: %s\n", rest, done ? "done" : "FAILED");
}

void
redwood_cpufreq_init(void)
{
	const ml_topology_info_t *topology = ml_get_topology_info();
	uint32_t enabled = 0, cores[EPSS_DOMAINS] = { 0 }, before, here;

	if (redwood_platform_is_annibale()) {
		/* (EPSS_PHYS and RSC_PHYS are redwood's: SM8750 has no EPSS for its cores, and its RSC is at 0x16500000) */
		load_start();
		annibale_cpufreq();
		return;
	}
	if (redwood_platform_blocks_legacy_drivers()) {
		printf("cpufreq: legacy RPMh/EPSS initialization skipped by platform guard\n");
		return;
	}
	load_start();
	enabled = 2;
	PE_parse_boot_argn("ddr", &enabled, sizeof(enabled));
	if (enabled == 1) {
		ddr_look();
		ddr_slots();
	} else if (enabled == 2 && cpufreq_wanted()) {
		ddr_raise();
	}
	enabled = 0;
	if (!PE_parse_boot_argn("cpufreq", &enabled, sizeof(enabled)) || !enabled) {
		return;
	}
	for (unsigned int i = 0; i < topology->num_cpus; i++) {
		cores[domain_of(topology->cpus[i].phys_id)]++;
	}
	/* (whichever core this thread happens to be on, and stays on or not: it only goes to the log) */
	here = (uint32_t)(__builtin_arm_rsr64("MPIDR_EL1") >> 8) & 0xff;
	before = loop_speed();
	for (uint32_t domain = 0; domain < EPSS_DOMAINS; domain++) {
		if (cores[domain] != 0) {
			raise_domain(domain, cores[domain]);
		}
	}
	printf("cpufreq: on core %u a loop ran at %u, now at %u\n", here, before, loop_speed());
	if (PE_parse_boot_argn("membench", &enabled, sizeof(enabled)) && enabled == 1) {
		memory_bench();
	} else if (enabled == 2) {
		thread_t thread;

		if (kernel_thread_start(bench_all, NULL, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		}
	}
}
