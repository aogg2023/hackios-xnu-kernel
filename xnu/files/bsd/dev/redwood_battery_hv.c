/*
 * The battery of the REDMI K90 (annibale, SM8750 "sun"), read from the fuel gauge inside the
 * power management IC, for the sysctls kern.redwood.battery_* and the iOS battery icon that reads
 * them (bsd/dev/redwood_battery.c carries the report onward, sys/redwood_input.h the shape of
 * it). Only with boot-arg battery=1; every address here belongs to this phone.
 *
 * The phone's charger and gauge are not on I2C as bsd/dev/redwood_battery.c's are -- that one
 * reads a state block xnuloader filled from Qualcomm's UEFI; on this phone xnuloader gives us
 * nothing (it prints so itself when battery=1 starts it here as well, and steps aside). The
 * gauge is a peripheral of the PMIH010X power management chip, reached over SPMI through the
 * version 7 arbiter, the way bsd/dev/redwood_buttons_hv.c reads the buttons and
 * bsd/dev/redwood_haptic_hv.c the motor.
 *
 * The device tree does not name the gauge. Xiaomi's annibale tree describes the chip
 * (qcom,pmih010x@7, line 20140 of the merged decompile) and its PMIC-GLINK charger node (line
 * 21718, status "disabled" to Linux, which charges through the ADSP instead), but dropped the
 * gauge's and the SDAM's child nodes that a stock tree carries. So the driver cannot be told
 * where the gauge is; it asks. The arbiter's table (core + 0x2000, four bytes per channel) says
 * which peripheral every channel answers, and every QPNP peripheral answers a type number at
 * register 0x04: the Qualcomm gauge (QG) answers 0x0d (qg-reg.h) and the SRAM the gauge's
 * estimates live in (SDAM) answers 0x2e (qg-sdam.h). One pass over the table and one two-byte
 * read per peripheral finds both, and the first run on hardware sees the whole list besides.
 *
 * What is read, and what it is worth -- all of it pure reads:
 *   the gauge    register 0x08: the gauge says whether it is up (QG_OK_BIT) and whether it sees a
 *                battery (BATTERY_PRESENT_BIT; 0x09 says BATTERY_MISSING_BIT); registers 0x80 and
 *                0x82: the averaged battery voltage and current as the gauge measured them last,
 *                two raw 16-bit samples each -- 194637/1000 uV per unit (qg-defs.h V_RAW_TO_UV)
 *                and 152588/1000 uA (5 A range) or 305176/1000 uA (10 A, from the gauge's
 *                subtype 3 and 4, qg-util.c qg_iraw_to_ua), the current signed. A current of
 *                0x8000 says "never measured" (FIFO_I_RESET_VAL) and then the last single ADC
 *                result at 0xc0 and 0xc2 is asked instead.
 *   the SDAM     offsets 0x46 on: the firmware's own state of charge (one byte, whole per cent),
 *                its temperature (two bytes, tenths of a degree Celsius, signed), open-circuit
 *                voltage (four, uV) and current (four, uA signed) as the charge algorithm last
 *                had them, cycle count (0x58) and learned capacity (0x68, mAh, qpnp-qg.c
 *                qg_get_learned_capacity -- multiplied by a thousand for the uAh the report
 *                wants). A valid byte of 1 means the firmware has written the table and it may
 *                be believed (qpnp-qg.c writes 1 there when it fills it).
 *   not read     the charger. It hangs off the PMIC-GLINK, not this bus: whether a cable is on
 *                the port and what it gives is the SMB charger block's story, and the charge
 *                status published here is guessed from the current's sign -- positive into the
 *                battery means charging. TODO: read the real thing once the charger block is
 *                found, on this bus or through the ADSP.
 *
 * The steps, each said in the log before it is made. Boot-arg battery_hv_steps=N stops after the
 * first N (battery=1 asks for all five):
 *   1  map the arbiter and read its version             (the first access to that block)
 *   2  dump the arbiter's table: every channel, what it is, who owns it
 *   3  ask every peripheral what it is                  (the first transfers on the bus)
 *   4  read what the gauge and the SDAM say now, raw numbers in the log as well
 *   5  publish and look again every five seconds        (the default)
 *
 * Register layouts: drivers/power/supply/qcom/qpnp-qg.c and its qg-reg.h, qg-defs.h, qg-util.c
 * and qg-sdam.{c,h} of the vendor kernel (kernel_oneplus_sm8750, branch sixteen -- the same
 * platform), drivers/spmi/spmi-pmic-arb.c for the way in, and the phone's own device tree.
 */
#include <kern/clock.h>
#include <kern/locks.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_audio.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);
extern void loader_log_putc(char c);

static bool leaving;

static int say(const char *format, ...) __printflike(1, 2);

static int
say(const char *format, ...)
{
	char line[400];
	va_list list;

	va_start(list, format);
	vsnprintf(line, sizeof(line), format, list);
	va_end(list);
	for (const char *c = line; *c != 0; c++) {
		loader_log_putc(*c);
	}
	if (!leaving && ml_get_interrupts_enabled()) {
		printf("%s", line);
	}
	return 0;
}
#define printf say

static inline uint32_t
rd(vm_offset_t address)
{
	return *(volatile uint32_t *)address;
}

static inline void
wr(vm_offset_t address, uint32_t value)
{
	*(volatile uint32_t *)address = value;
}

/* ---- the PMIC arbiter, version 7 (the windows and arithmetic of redwood_haptic_hv.c) -------------- */

#define ARB_CORE_PHYS           0x0c400000UL
#define ARB_CORE_SIZE           0x003000UL
#define ARB_CNFG_PHYS           0x0c42d000UL
#define ARB_CNFG_SIZE           0x004000UL
#define ARB_OBSRV_PHYS          0x0c440000UL    /* the read channels: this driver only ever reads */
#define ARB_OBSRV_SIZE          0x0080000UL

#define ARB_VERSION             0x000
#define ARB_VERSION_7           0x70000000u
#define ARB_FEATURES            0x004           /* [10:0] the number of channels */
#define ARB_SYSTEM              0
#define APID_MAX                ((ARB_CORE_SIZE - 0x2000) / 4)
#define APID_MAP(n)             (0x2000UL + 4 * (n))    /* core: [19:8] the peripheral (slave and function) */
#define APID_OWNER(n)           (4 * (n))               /* cnfg: [2:0] the system that may write through it */

#define CH_CMD                  0x00
#define CH_STATUS               0x08
#define CH_RDATA0               0x18
#define CH_RDATA1               0x1c
#define STATUS_DONE             0x1
#define STATUS_FAILURE          0x2
#define STATUS_DENIED           0x4
#define STATUS_DROPPED          0x8
#define CMD_OPC(opc)            ((uint32_t)(opc) << 27)
#define CMD_REG(reg)            (((uint32_t)(reg) & 0xff) << 4)
#define OPC_EXT_READL           1
#define OPC_EXT_READ            13
#define READ_TIMEOUT_US         2000

static vm_offset_t arb_core, arb_cnfg, arb_obsrvr;

/* ---- the arbiter's table, so far as it says anything --------------------------------------------- */

/*
 * One entry per peripheral (a peripheral answers through several channels, one per system; the
 * first that is ours to write is kept, as Linux keeps it). The whole table is held because step
 * 3 reads through all of it and step 4 still wants the channels of the gauge it picked.
 */
struct candidate {
	uint16_t        ppid;           /* slave << 8 | function */
	uint16_t        apid;           /* the channel it answers through */
	uint8_t         owner;          /* the system that may write through it */
	int16_t         type, subtype;  /* what it says it is, -1 until asked */
};
static struct candidate table[APID_MAX];
static unsigned candidates;

/* How the reads have gone. */
static struct {
	uint64_t reads, failed, last_ns;
} bus;

static const char *
spmi_read(uint16_t ppid, uint8_t reg, uint8_t *bytes, uint32_t count)
{
	const struct candidate *c = NULL;
	vm_offset_t channel;
	uint64_t start = mach_absolute_time(), now, interval, taken;
	uint32_t status = 0, data[2] = { 0, 0 };
	const char *problem = NULL;

	for (unsigned i = 0; i < candidates; i++) {
		if (table[i].ppid == ppid) {
			c = &table[i];
			break;
		}
	}
	if (c == NULL) {
		return "no channel is that peripheral";
	}
	nanoseconds_to_absolutetime(READ_TIMEOUT_US * (uint64_t)NSEC_PER_USEC, &interval);
	channel = arb_obsrvr + 0x8000UL * ARB_SYSTEM + 0x20UL * c->apid;
	redwood_spmi_hv_lock();          /* the buttons' file owns the lock the annibale drivers share */
	wr(channel + CH_CMD, CMD_OPC(count > 4 ? OPC_EXT_READL : OPC_EXT_READ) | CMD_REG(reg) | (count - 1));
	do {
		status = rd(channel + CH_STATUS);
		now = mach_absolute_time();
	} while (!(status & STATUS_DONE) && now - start < interval);
	if (status & STATUS_DONE) {
		data[0] = rd(channel + CH_RDATA0);
		data[1] = count > 4 ? rd(channel + CH_RDATA1) : 0;
	}
	redwood_spmi_hv_unlock();

	if (!(status & STATUS_DONE)) {
		problem = "no answer from the arbiter";
	} else if (status & STATUS_DENIED) {
		problem = "the arbiter refuses";
	} else if (status & STATUS_FAILURE) {
		problem = "the transfer failed (nobody at that address?)";
	} else if (status & STATUS_DROPPED) {
		problem = "the transfer was dropped";
	} else {
		for (uint32_t i = 0; i < count; i++) {
			bytes[i] = (uint8_t)(data[i / 4] >> (8 * (i % 4)));
		}
	}
	absolutetime_to_nanoseconds(now - start, &taken);
	bus.reads++;
	bus.last_ns = taken;
	if (problem != NULL) {
		bus.failed++;
	}
	return problem;
}

/* Step 2: what the arbiter's table says, everything in it. */
static uint32_t
dump_table(uint32_t channels)
{
	uint32_t version = rd(arb_core + ARB_VERSION);

	printf("battery: the arbiter is version %#010x with %u channels\n", version, channels);
	for (uint32_t n = 0; n < channels && candidates < APID_MAX; n++) {
		uint32_t map = rd(arb_core + APID_MAP(n)), ppid;
		unsigned owner;
		bool ours;

		if (map == 0) {
			continue;
		}
		ppid = (map >> 8) & 0xfff;
		owner = rd(arb_cnfg + APID_OWNER(n)) & 7;
		ours = owner == ARB_SYSTEM;
		{
			unsigned i;

			for (i = 0; i < candidates; i++) {
				if (table[i].ppid == (uint16_t)ppid) {
					break;
				}
			}
			if (i < candidates) {
				if (ours && table[i].owner != ARB_SYSTEM) {
					table[i].apid = (uint16_t)n;
					table[i].owner = (uint8_t)owner;
				}
				continue;
			}
		}
		printf("battery: channel %3u is ppid %#04x (slave %u function %#02x), written by system %u%s\n", n, ppid,
		    ppid >> 8, ppid & 0xff, owner, (map & (1u << 24)) ? ", which also gets its interrupts" : "");
		table[candidates].ppid = (uint16_t)ppid;
		table[candidates].apid = (uint16_t)n;
		table[candidates].owner = (uint8_t)owner;
		table[candidates].type = table[candidates].subtype = -1;
		candidates++;
	}
	printf("battery: the table names %u different peripherals\n", candidates);
	return version;
}

/* the type numbers, of qg-reg.h and qg-sdam.h */
#define QG_TYPE                 0x0d
#define SDAM_TYPE               0x2e
#define PMIH_SLAVE              7               /* qcom,pmih010x@7, the device tree's line 20140 */

static uint16_t gauge, storage;                 /* the ppids picked, 0 when nothing was */

/*
 * Step 3: ask every peripheral what it is. A silent or unreadable one is kept as silent -- some
 * of these belong to other systems and answer to reads, but a peripheral that fails the type
 * read is printed too, because on the first run everything said is news.
 */
static void
ask_everyone(void)
{
	unsigned answered = 0, silent = 0;
	static uint16_t tally[256];
	char line[380];
	size_t used = 0;

	__bzero(tally, sizeof(tally));
	printf("battery: next: one two-byte read per peripheral: %u of them, the first transfers on the bus\n", candidates);
	for (unsigned i = 0; i < candidates; i++) {
		uint8_t is[2];
		const char *problem = spmi_read(table[i].ppid, 0x04, is, 2);

		if (problem != NULL) {
			table[i].type = -1;
			silent++;
			printf("battery: ppid %#04x: %s\n", table[i].ppid, problem);
			continue;
		}
		table[i].type = is[0];
		table[i].subtype = is[1];
		answered++;
		tally[is[0]]++;
		if (is[0] == QG_TYPE || is[0] == SDAM_TYPE) {
			printf("battery: ppid %#04x (slave %u function %#02x) says type %#02x subtype %#02x, written by system %u%s\n",
			    table[i].ppid, table[i].ppid >> 8, table[i].ppid & 0xff, is[0], is[1], table[i].owner,
			    table[i].owner == ARB_SYSTEM ? " (ours)" : "");
		}
	}
	printf("battery: %u peripherals answered, %u did not; reads took up to %llu us\n", answered, silent,
	    bus.last_ns / 1000);
	/* the whole census, on as few lines as they fit: on the first run every one is news */
	line[0] = 0;
	for (int t = 0; t < 256; t++) {
		int added;

		if (tally[t] == 0) {
			continue;
		}
		added = snprintf(line + used, sizeof(line) - used, " type %#02x: %u", t, tally[t]);
		if (used + (size_t)added >= sizeof(line) - 1) {
			printf("battery: census of the peripherals:%s\n", line);
			used = 0;
			line[0] = 0;
			added = snprintf(line, sizeof(line), " type %#02x: %u", t, tally[t]);
		}
		used += (size_t)added;
	}
	if (used > 0) {
		printf("battery: census of the peripherals:%s\n", line);
	}
	for (unsigned pass = 0; pass < 2; pass++) {
		uint8_t want = pass == 0 ? QG_TYPE : SDAM_TYPE;
		uint16_t *pick = pass == 0 ? &gauge : &storage;

		for (unsigned i = 0; i < candidates; i++) {
			if (table[i].type != (int16_t)want) {
				continue;
			}
			if (*pick == 0) {
				*pick = table[i].ppid;
			}
			/* the PMIH010X's one is believed first of all: the device tree's charger chip */
			if ((table[i].ppid >> 8) == PMIH_SLAVE) {
				*pick = table[i].ppid;
				break;
			}
		}
	}
	if (gauge == 0) {
		printf("battery: no peripheral answered the fuel gauge's type %#02x: nothing can be read\n", QG_TYPE);
	}
	if (storage == 0) {
		printf("battery: no peripheral answered the SDAM's type %#02x: the firmware's numbers will be missing\n", SDAM_TYPE);
	}
}

/* the gauge's registers (qg-reg.h, relative to the gauge function's base) */
#define QG_STATUS1              0x08            /* [7] the gauge is up, [0] it sees a battery */
#define QG_STATUS2              0x09            /* [3] it says a battery is missing */
#define QG_INT_RT_STS           0x10
#define QG_AVG_V                0x80            /* two raw bytes each: the last averaged sample */
#define QG_AVG_I                0x82
#define QG_LAST_V               0xc0            /* ... and the last single ADC conversion */
#define QG_LAST_I               0xc2

/* the SDAM's, relative to its function's base (qg-reg.h again) */
#define SDAM_VALID              0x46            /* 1 when the firmware has filled the table */
#define SDAM_SOC                0x47            /* whole per cent */
#define SDAM_TEMP               0x48            /* tenths of a degree, signed */
#define SDAM_OCV                0x4c            /* four bytes, uV */
#define SDAM_IBAT               0x50            /* four bytes, uA, signed */
#define SDAM_CYCLES             0x58            /* two bytes */
#define SDAM_LEARNED            0x68            /* two bytes, mAh */

#define FIFO_I_RESET            0x8000          /* "never measured" (qg-defs.h) */

static int32_t gauge_subtype;                   /* which current scale the gauge uses */

/* uV and uA out of the gauge's raw samples (qg-defs.h, qg_util's iraw_to_ua) */
static int64_t
volts_raw_to_uv(uint16_t raw)
{
	return 194637ll * raw / 1000;
}

static int64_t
current_raw_to_ua(int16_t raw)
{
	if (gauge_subtype == 0x04) {              /* the 10 A range */
		return 305176ll * raw / 1000;
	}
	if (gauge_subtype != 0x03) {
		printf("battery: the gauge's subtype is %#x, which the vendor driver gives no current scale for; "
		    "using the 5 A one\n", gauge_subtype);
		gauge_subtype = 0x03;
	}
	return 152588ll * raw / 1000;
}

/* the reading of one look */
struct reading {
	bool ok, present;
	int64_t v_uv;                             /* what the gauge measured */
	int64_t i_ua;
	bool sdam_valid;
	int sdam_soc, sdam_temp_dc;
	int64_t sdam_ocv_uv, sdam_ibat_ua;
	int sdam_cycles, sdam_learned_mah;
};

static bool
read_gauge(struct reading *r)
{
	uint8_t b[8];
	uint16_t raw;
	const char *problem;

	__bzero(r, sizeof(*r));
	problem = spmi_read(gauge, QG_STATUS1, b, 4);     /* status1, status2, status3 and 4: qg-reg.h */
	if (problem != NULL) {
		printf("battery: reading the gauge's status: %s\n", problem);
		return false;
	}
	r->ok = b[0] & 0x80;
	r->present = (b[0] & 0x01) && !(b[1] & 0x08);
	printf("battery: gauge %#04x: status1 %#02x (%s, gauge %s), status2 %#02x%s, status3 %#02x, status4 %#02x\n", gauge, b[0],
	    r->present ? "battery seen" : "NO BATTERY", r->ok ? "up" : "not up yet", b[1], (b[1] & 0x02) ? ", ocv good" : "", b[2], b[3]);
	if (!(r->ok && r->present)) {
		return false;
	}
	if (spmi_read(gauge, QG_AVG_V, b, 2) != NULL) {
		printf("battery: reading the gauge's voltage failed\n");
		return false;
	}
	raw = (uint16_t)(b[1] << 8 | b[0]);
	if (raw == 0 || raw == 0xffff) {
		if (spmi_read(gauge, QG_LAST_V, b, 2) != NULL) {
			printf("battery: reading the gauge's last voltage failed as well\n");
			return false;
		}
		raw = (uint16_t)(b[1] << 8 | b[0]);
		printf("battery: no averaged voltage (raw %#x): the last single conversion instead\n", raw);
	}
	r->v_uv = volts_raw_to_uv(raw);
	if (spmi_read(gauge, QG_AVG_I, b, 2) != NULL) {
		printf("battery: reading the gauge's current failed\n");
		return false;
	}
	raw = (uint16_t)(b[1] << 8 | b[0]);
	if (raw == FIFO_I_RESET) {
		if (spmi_read(gauge, QG_LAST_I, b, 2) != NULL) {
			printf("battery: reading the gauge's last current failed as well\n");
			return false;
		}
		raw = (uint16_t)(b[1] << 8 | b[0]);
		printf("battery: the current FIFO never filled: the last single conversion instead\n");
	}
	if (raw == FIFO_I_RESET) {
		printf("battery: still no current: publishing without one\n");
		r->i_ua = 0;
	} else {
		r->i_ua = current_raw_to_ua((int16_t)raw);
	}
	return true;
}

static void
read_sdam(struct reading *r)
{
	uint8_t a[8], b[8];

	if (storage == 0) {
		return;
	}
	/* the arbiter answers eight bytes at the most: two reads cover 0x46 to 0x53 */
	if (spmi_read(storage, SDAM_VALID, a, 8) != NULL || spmi_read(storage, SDAM_OCV, b, 8) != NULL) {
		printf("battery: the SDAM %#04x did not answer\n", storage);
		return;
	}
	r->sdam_valid = a[0] == 1;
	r->sdam_soc = a[1];
	r->sdam_temp_dc = (int16_t)(a[3] << 8 | a[2]);
	r->sdam_ocv_uv = (int64_t)(uint32_t)(b[3] << 24 | b[2] << 16 | b[1] << 8 | b[0]);
	r->sdam_ibat_ua = (int64_t)(int32_t)(uint32_t)(b[7] << 24 | b[6] << 16 | b[5] << 8 | b[4]);
	if (spmi_read(storage, SDAM_CYCLES, a, 2) == NULL) {
		r->sdam_cycles = a[1] << 8 | a[0];
	}
	if (spmi_read(storage, SDAM_LEARNED, a, 2) == NULL) {
		r->sdam_learned_mah = (int16_t)(a[1] << 8 | a[0]);
	}
	printf("battery: sdam %#04x: valid %u, charge %d%%, temperature %d.%d C, ocv %lld uV, current %lld uA, "
	    "%d cycles, learned %d mAh\n", storage, r->sdam_valid, r->sdam_soc, r->sdam_temp_dc / 10,
	    r->sdam_temp_dc < 0 ? -(r->sdam_temp_dc % 10) : r->sdam_temp_dc % 10, r->sdam_ocv_uv, r->sdam_ibat_ua,
	    r->sdam_cycles, r->sdam_learned_mah);
	if (!r->sdam_valid) {
		printf("battery: ... which the firmware does not call valid (its byte reads %u, not 1): its numbers are not published\n",
		    a[0]);
	}
}

static void
publish(const struct reading *r)
{
	struct redwood_battery_state s;

	if (!r->present) {
		printf("battery: not publishing: the gauge says there is no battery\n");
		return;
	}
	if (!r->sdam_valid) {
		/* the report's percent is clamped 0 to 100 and cannot say "unknown": the sysctls
		 * are to keep saying -1 until the firmware's estimate is there to publish */
		printf("battery: not publishing: the SDAM holds no valid charge estimate (yet)\n");
		return;
	}
	__bzero(&s, sizeof(s));
	s.size = sizeof(s);
	s.present = 1;
	s.voltage_uv = (int32_t)(r->v_uv ? r->v_uv : r->sdam_ocv_uv);
	s.current_ua = (int32_t)r->sdam_ibat_ua;
	s.percent_hundredths = r->sdam_soc * 100;
	s.temperature_hundredths = r->sdam_temp_dc * 10;
	s.full_uah = r->sdam_learned_mah > 0 ? r->sdam_learned_mah * 1000 : 0;
	s.cycles = r->sdam_cycles;
	/*
	 * The charge status, guessed from the current -- the cable's own story is in the
	 * charger, which hangs off PMIC-GLINK and is not read here (see the top).
	 */
	if (s.current_ua > 50000) {
		s.status = 1;                             /* POWER_SUPPLY_STATUS_CHARGING */
		s.external = 1;
	} else if (r->sdam_soc >= 99) {
		s.status = 4;                             /* ..._FULL */
	} else {
		s.status = 2;                             /* ..._DISCHARGING */
	}
	redwood_battery_report(&s);
	printf("battery: published: %d.%d%% at %d mV, %d mA (%s), %d.%d C%s\n", s.percent_hundredths / 100,
	    (s.percent_hundredths / 10) % 10, s.voltage_uv / 1000, s.current_ua / 1000,
	    s.status == 1 ? "charging" : s.status == 4 ? "full" : "discharging", s.temperature_hundredths / 100,
	    (s.temperature_hundredths / 10) % 10, s.external ? ", charger (guessed from the current)" : "");
}

static void
battery_thread(void *parameter, __unused wait_result_t result)
{
	uint32_t steps = (uint32_t)(uintptr_t)parameter, version, channels;
	struct reading r;
	bool have;

	printf("battery: step 1: map the PMIC arbiter (%#lx, %#lx, %#lx) and read its version\n", ARB_CORE_PHYS, ARB_CNFG_PHYS,
	    ARB_OBSRV_PHYS);
	arb_core = ml_io_map(ARB_CORE_PHYS, ARB_CORE_SIZE);
	arb_cnfg = ml_io_map(ARB_CNFG_PHYS, ARB_CNFG_SIZE);
	arb_obsrvr = ml_io_map(ARB_OBSRV_PHYS, ARB_OBSRV_SIZE);
	if (arb_core == 0 || arb_cnfg == 0 || arb_obsrvr == 0) {
		printf("battery: the arbiter's windows cannot be mapped: no battery\n");
		goto out;
	}
	version = rd(arb_core + ARB_VERSION);
	channels = rd(arb_core + ARB_FEATURES) & 0x7ff;
	if (version < ARB_VERSION_7) {
		printf("battery: arbiter version %#x is not version 7: this phone's numbers stop here\n", version);
		goto out;
	}
	if (channels == 0 || channels > APID_MAX) {
		channels = APID_MAX;
	}
	if (steps < 2) {
		goto out;
	}

	printf("battery: step 2: what the arbiter's table says is on the bus\n");
	dump_table(channels);
	if (steps < 3) {
		goto out;
	}

	printf("battery: step 3: what every peripheral says it is\n");
	ask_everyone();
	if (gauge == 0) {
		goto out;
	}
	for (unsigned i = 0; i < candidates; i++) {
		if (table[i].ppid == gauge) {
			gauge_subtype = table[i].subtype;
		}
	}
	if (steps < 4) {
		goto out;
	}

	printf("battery: step 4: what the gauge and the SDAM say now\n");
	have = read_gauge(&r);
	read_sdam(&r);
	if (!have) {
		printf("battery: nothing the gauge would say: %s\n", r.present ? "it is not up yet" : "no battery seen");
	}
	if (steps < 5) {
		goto out;
	}

	printf("battery: step 5: watching it, every 5 s\n");
	for (unsigned tick = 1;; tick++) {
		delay_for_interval(5000, NSEC_PER_MSEC);
		have = read_gauge(&r);
		read_sdam(&r);
		publish(&r);
		if (tick % 12 == 0) {
			printf("battery: a minute of looks: %llu reads of the bus, %llu failed\n", bus.reads, bus.failed);
		}
		if (bus.failed >= 100 && bus.failed * 2 > bus.reads) {
			printf("battery: more than half of %llu reads have failed: giving up\n", bus.reads);
			break;
		}
	}
out:
	if (steps < 5) {
		printf("battery: stopping here (battery_hv_steps=%u)\n", steps);
	}
	thread_terminate(current_thread());
}

void
redwood_battery_hv_init(void)
{
	uint32_t enabled = 0, steps = 5;
	thread_t thread;

	/* battery=1, the boot-arg bsd/dev/redwood_battery.c starts on as well; on this phone that
	 * one only says that xnuloader shared nothing and steps aside */
	if (!PE_parse_boot_argn("battery", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}
	PE_parse_boot_argn("battery_hv_steps", &steps, sizeof(steps));
	if (steps == 0) {
		return;
	}
	if (steps > 5) {
		steps = 5;
	}
	if (kernel_thread_start(battery_thread, (void *)(uintptr_t)steps, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	}
}
