/*
 * The GPU of the REDMI K90 (SM8750, "annibale"), an Adreno 830 of the gen8 ("a8xx") family,
 * woken step by step as far as proof that its power processor runs, answers questions, and gives
 * the 3D core its power. Nothing here is touched unless a boot-arg asks for it.
 *
 * Why this file is not named redwood_*. The redwood_ prefix marks a driver of the POCO X5 Pro's
 * board; what is done here is the SoC's and the IP's -- the Adreno 8xx generation and its GMU --
 * and the file is written to be reusable on any machine with a gen8 Adreno. So: adreno8.c, with
 * boot-args adreno8=N. (Naming ruling, task board: machine-level drivers say redwood_/annibale_,
 * SoC- and IP-level drivers do not.)
 *
 * The set of files. The workspace keeps a new code file under 800 lines, and the stepper is split
 * accordingly. This is adreno8.c, the trunk: the culture helpers every part shares (the announced,
 * paused, logged accesses), the dispatcher that walks the ladder under the boot-arg's order, and
 * what stops and what is left at the end. adreno8_cx.c is the outside and the CX half: the fuses,
 * the RPMh roster, the clock controller, the CX domain, the RSCC, the clocks (steps 1 to 5).
 * adreno8_smmu.c is the SMMU the GMU speaks through: its layout, and the GMU's address space and
 * context (step 8, and the machinery steps 9 to 16 live inside it). adreno8_gmu.c is the GMU's own
 * world: its memory, the firmware file and its load, the RSC wake and the start of its processor
 * (steps 6, 7, 9, 10, 11) and the post-mortem of a firmware that did not start. adreno8_hfi.c is
 * the messages: the queues, the performance and bandwidth tables, the 3D core's own firmware and
 * its power (steps 12 to 16). adreno8_private.h carries the shared world -- the register maps, the
 * mapped blocks, the state one step hands the next -- under adreno8_-prefixed, non-static names,
 * because the ladder's variables (cx_was_on, gmu_space, the queue table) cross the part lines:
 * the steps on either side of every candidate seam own them together. *
 * The steps are the ladder of proof, in the culture of bsd/dev/redwood_adreno.c, which this
 * file's register access, announcement discipline and memory machinery follow line for line:
 * adreno8=N performs steps 1 to N and stops; a step says what it is about to touch before it
 * touches it and what it found after; reads come before writes; and a step that does not find
 * what it needs ends the sequence instead of going on blind. Where redwood and this SoC differ
 * the difference is said at the place, and in the list "what is not settled" at the bottom.
 *
 *   1  reads   what feeds the GPU from outside: qfprom's speed bin, which says which of this
 *              SoC's two speed tables to use; the GPU's memory clock in the global clock
 *              controller; the boot loader's list of what RPMh can switch, and the levels the
 *              GPU's supply rails know
 *   2  reads   the GPU's own clock controller as the boot loader left it: the CX power switch,
 *              the thirteen branches, the two clock sources, the PLL area (read only)
 *   3  writes  the "CX" power switch on: the half of the GPU block that is not the 3D core --
 *              the power processor's shell, the bus interface, the GPU's SMMU. Unlike redwood a
 *              switch the boot loader left on is accepted, not fought: on this phone the AOP and
 *              the loader keep CX up for the vendor's preemption logic
 *   4  reads   that the CX half is awake: the warm/cold tell, the number of 3D slices, the fuse
 *              word, and the GMU's own channel to RPMh (the "rscc")
 *   5  writes  the clocks of that half on: the crystal's, the GMU's (left on the crystal's 19.2
 *              MHz -- the GMU runs its firmware at TCXO rate unless adreno8_gmuclk=1 asks), the
 *              bus and hub taps, and the GPU-clock-controller clock of the GMU itself
 *   6  reads   the first words from the GMU itself: its resets, versions, power state, message
 *              queue state, interrupts and fence -- the whole room, looked at, not touched; only
 *              an all-ones answer fails
 *   7  writes  two words into the GMU's data memory, read back, and the originals put back:
 *              the way its firmware goes in. A GMU whose firmware is already running is left be
 *   8  reads   the SMMU in front of the GPU: its ID registers and layout, the stream table and
 *              the first contexts, as the loader and hypervisor left them
 *   9  writes  the memory the GMU's firmware asks for (fw/gen80200_gmu.bin names the addresses),
 *              the queue table of the message protocol, page tables of ours, and the SMMU: a
 *              translation context (1) with those tables, the GMU's stream (5) sent through it
 *  10  writes  the firmware itself: its code and data memories through the GMU's registers while
 *              its processor is held in reset, the rest into the memory just made, read back
 *  11  writes  the GMU's configuration, and lets its processor out of reset: the firmware runs
 *  12  writes  the GMU's message queues ("HFI") start, and the GMU answers on them
 *  13  talks   the speed tables over: the GPU's frequencies with their supply-rail votes (the
 *              "TABLE" message, the gen8 form), and the memory-bandwidth table of RPMh commands
 *  14  talks   the 3D core powered: idle-power feature on (tolerated), the core-firmware start,
 *              the start, and the GMU's power state read until the 3D core has power
 *  15  talks   the first GX words: a bandwidth-and-speed vote at the chosen speed, the core's
 *              aperture aimed at slice 0, and the 3D core's always-on counter seen counting
 *  16  reads   a last look: did the SMMU refuse anything, what do the interrupts say, is the
 *              slice count still there, is the power still up -- and if all yes, the goal is met
 *
 * When the steps are over, what they started is stopped again unless adreno8_keep=1 asks that it
 * be left running: the message queues quieted, the GMU's processor back in reset, its stream
 * sent nowhere, the context off. (Its SMMU tables point into this kernel's memory; adreno8_keep
 * is for the next loader session on the same boot, and the tables stay the kernel's problem.)
 *
 * More boot-args:
 *   adreno8_only=K     step K alone (what a step needs it checks for itself: within one session
 *                      of the loader, power and clocks stay as an earlier run left them)
 *   adreno8_skip=MASK  leave out the steps whose bits are set (bit 0 = step 1, bit 1 = step 2, ...)
 *   adreno8_pause=MS   how long the newest step waits after announcing a group of accesses
 *                      (default 2200): the loader, watching from another core, writes the end of
 *                      our log to storage every two seconds, and after a silent reset that note
 *                      is all there is
 *   adreno8_fine=1     the newest step waits like that before every single access
 *   adreno8_watch=S    after the last step, say once a second for S seconds (default 20) that
 *                      the machine is still alive, and what was written last
 *   adreno8_keep=1     leave the GMU running and the CX half on when the steps are over
 *   adreno8_speed=N    step 15 asks for the 3D core's Nth speed (1 lowest; default: the top of
 *                      the speed table the fuses chose)
 *   adreno8_gmuclk=1   step 5 asks for the GMU's clock from the GPU PLL instead of the crystal
 *                      (a status unknown: see TAYCAN below; without it, zero PLL writes)
 *   adreno8_return=1   when all is over, go back to the loader instead of starting the system
 *
 * The firmware is the phone's own: /vendor/firmware_mnt/image/{gen80200_gmu.bin,gen80200_sqe.fw,
 * gen80200_aqe.fw} on the phone; userland's firmware-gen8/ puts gmu.bin into the small image's
 * fw/ directory. Only gmu.bin is needed this far; the two microcodes are for the 3D core's own
 * start, which is past this ladder.
 *
 * Addresses are from the phone's own device tree ("dt:"): qcom,kgsl-3d0@3d00000, qcom,gmu@3d37000
 * (reg 0x3d37000 length 0x68000, second window "gmu_ao_blk_dec0" at 0x3d40000), rscc 0x3d50000,
 * gbif 0x3d0f000, cx_misc 0x3d9e000, clock-controller@3d90000, gcc@100000, kgsl-smmu@3da0000
 * (0x40000), qfprom@221c8000 (gpu_speed_bin@138, bits 9:0), pwrlevels-0/-1, reserved-memory
 * cmd_db@81c60000. Register layouts from Linux v6.19: drivers/gpu/drm/msm/adreno/{a6xx_gmu.c,
 * a6xx_hfi.c, adreno_gen8.xml with a6xx_gmu.xml}, drivers/clk/qcom/gpucc-sm8750.c, the gen80200
 * entry of adreno_gen8_catalog.c, drivers/iommu/arm/arm-smmu/, drivers/soc/qcom/cmd-db.c and the
 * RPMh tcs command format. The xml's numbers are dword indices in the GPU block's byte space
 * based at 0x3d00000; the GMU's window at 0x3d37000 therefore starts at dword 0xd000, and every
 * register below is spelled GMU(index) = 4*index - 0x37000 -- exactly Linux's GMU_BYTE_OFFSET.
 *
 * What is not settled, and where the file says so (each one also gets a comment at its place):
 *   the TAYCAN_ELU PLL's register offsets (gpucc 0x0..): values known, offsets not verified, so
 *     the GMU's clock stays on the crystal and adreno8_gmuclk=1 only reads the PLL area;
 *   the RCG update bit: clk-rcg2 uses bit 31; redwood found bit 0 on its SoC -- bit 31 here,
 *     readback decides;
 *   the SMMU's context-bank layout: the formulas from redwood's working run are kept and the
 *     layout is printed, not enforced; the readbacks after the first write to context 1 are the
 *     real test;
 *   the stream-mask cell 0x400, which every "iommus" of this SMMU repeats;
 *   the bandwidth levels: upstream derives them from the OPPs; here the vendor's nine static
 *     levels are used, with the list printed;
 *   whether the IFPC message is only wanted with an idle level (upstream gates it): sent, and
 *     its failure tolerated; the ACD table does not exist for gen80200, so no ACD message;
 *   the speed bin's mapping to the device tree's two "qcom,sku-codes": bin 0 is the fast table,
 *     bins 2 and 3 the capped one, anything else falls back to the capped one with a line said.
 */
#include "adreno8_private.h"

uint32_t adreno8_skip_steps, adreno8_pause_ms = 2200, adreno8_fine, adreno8_watch_seconds = 20;

uint32_t adreno8_speed_asked;                  /* adreno8_speed: 1 lowest (step 15) */

uint32_t adreno8_gmu_from_pll;                 /* adreno8_gmuclk=1 */

uint32_t adreno8_step;                         /* the one being performed */

uint32_t adreno8_newest_step;                  /* the highest one asked for: the one that has not been seen to work */

char adreno8_last_write[96] = "nothing written";

bool adreno8_quiet;                            /* repeating accesses that have been announced and seen to work: say nothing */

int
adreno8_say(const char *format, ...)
{
	char line[400];
	va_list list;

	va_start(list, format);
	vsnprintf(line, sizeof(line), format, list);
	va_end(list);
	for (const char *c = line; *c != 0; c++) {
		loader_log_putc(*c);
	}
	return printf("%s", line);
}

#define printf adreno8_say

void
adreno8_sleep_us(uint32_t us)
{
	delay_for_interval(us, NSEC_PER_USEC);
}

void
adreno8_sleep_ms(uint32_t ms)
{
	delay_for_interval(ms, NSEC_PER_MSEC);
}

/* ---- register access ------------------------------------------------------------------------------- */

/*
 * One access is one plain load or store of a general register. Some of what is touched here (the
 * SMMU at least) is not the hardware but the hypervisor's imitation of it: the access traps and
 * is carried out on our behalf, which works for the simplest instruction forms only. So the
 * compiler does not get to choose the instruction. (redwood_adreno.c, unchanged.)
 */

uint32_t __attribute__((noinline))
adreno8_rd(vm_offset_t address)
{
	uint32_t value;

	__asm__ volatile ("ldr %w0, [%1]" : "=r" (value) : "r" (address) : "memory");
	return value;
}

uint32_t __attribute__((noinline))
adreno8_rd8(vm_offset_t address)
{
	uint32_t value;

	__asm__ volatile ("ldrb %w0, [%1]" : "=r" (value) : "r" (address) : "memory");
	return value;
}

void __attribute__((noinline))
adreno8_wr(vm_offset_t address, uint32_t value)
{
	__asm__ volatile ("str %w0, [%1]" : : "r" (value), "r" (address) : "memory");
}

/* A stretch of registers; mapped when a step first needs it. */

void
adreno8_need(struct adreno8_block *b)
{
	if (b->base == 0) {
		b->base = ml_io_map(b->phys, b->size);
	}
}

/* Time for the loader to note what has just been said, where it is not yet known to be harmless. */

void
adreno8_hold(bool single_access)
{
	if (adreno8_step == adreno8_newest_step && adreno8_pause_ms && (!single_access || adreno8_fine)) {
		adreno8_sleep_ms(adreno8_pause_ms);
	}
}

/* What a step is about to do, in a line. */

void
adreno8_announce(const char *format, ...)
{
	char line[400];
	va_list list;

	va_start(list, format);
	vsnprintf(line, sizeof(line), format, list);
	va_end(list);
	printf("adreno8: step %u: %s\n", adreno8_step, line);
	adreno8_hold(false);
}

uint32_t
adreno8_get(struct adreno8_block *b, const char *name, uint32_t offset)
{
	uint32_t value;

	adreno8_need(b);
	if (adreno8_quiet) {
		return adreno8_rd(b->base + offset);
	}
	if (adreno8_step == adreno8_newest_step) {
		printf("adreno8: %u: about to read %s %s at %#x\n", adreno8_step, b->name, name, b->phys + offset);
		adreno8_hold(true);
	}
	value = adreno8_rd(b->base + offset);
	printf("adreno8: %u: %s %s (%#x) = %#x\n", adreno8_step, b->name, name, b->phys + offset, value);
	return value;
}

void
adreno8_put(struct adreno8_block *b, const char *name, uint32_t offset, uint32_t value)
{
	adreno8_need(b);
	if (!adreno8_quiet) {
		printf("adreno8: %u: about to write %s %s at %#x: %#x\n", adreno8_step, b->name, name, b->phys + offset, value);
		snprintf(adreno8_last_write, sizeof(adreno8_last_write), "step %u wrote %#x to %s %s at %#x", adreno8_step, value, b->name, name,
		    b->phys + offset);
		adreno8_hold(true);
	}
	adreno8_wr(b->base + offset, value);
}

/* Set bits of a register unless they are set. Returns what the register holds afterwards. */

uint32_t
adreno8_set(struct adreno8_block *b, const char *name, uint32_t offset, uint32_t bits)
{
	uint32_t was = adreno8_get(b, name, offset);

	if ((was & bits) == bits) {
		return was;
	}
	adreno8_put(b, name, offset, was | bits);
	return adreno8_get(b, name, offset);
}

/* Look at a register that has been read before until the masked bits have the value, for so many milliseconds. */

bool
adreno8_wait_for(struct adreno8_block *b, uint32_t offset, uint32_t mask, uint32_t value, uint32_t ms)
{
	for (uint32_t i = 0; i < ms * 10; i++) {
		if ((adreno8_rd(b->base + offset) & mask) == value) {
			return true;
		}
		adreno8_sleep_us(100);
	}
	return (adreno8_rd(b->base + offset) & mask) == value;
}

uint32_t
adreno8_get8(struct adreno8_block *b, const char *name, uint32_t offset)
{
	uint32_t value;

	adreno8_need(b);
	if (adreno8_step == adreno8_newest_step) {
		printf("adreno8: %u: about to read %s %s at %#x (a byte)\n", adreno8_step, b->name, name, b->phys + offset);
		adreno8_hold(true);
	}
	value = adreno8_rd8(b->base + offset);
	printf("adreno8: %u: %s %s (%#x) = %#x\n", adreno8_step, b->name, name, b->phys + offset, value);
	return value;
}

/* ---- leaving --------------------------------------------------------------------------------------------- */

bool adreno8_gpu8_keep, adreno8_gpu8_return;

/*
 * Everything back as it was found, or as it was found plus sleep: the core's firmware asked to
 * get ready (tolerated: worst case it keeps the core powered until the next restart), the GMU's
 * processor back into reset, its fence cleared, our RSC wake dropped, its translation off, the
 * CX collapsed only if we were the ones who raised it, and our kernel memory back.
 */

static void
adreno8_gpu8_block_stop(void)
{
	printf("adreno8: stop: putting everything back\n");
	if (adreno8_gmu_talks) {
		if (!adreno8_hfi_send(33, NULL, 0, "get ready to sleep")) {
			printf("adreno8: stop: no answer, or a refusal; going on: at worst the firmware keeps the core powered\n");
		}
	}
	if (adreno8_gmu.base != 0) {
		adreno8_wr(adreno8_gmu.base + GMU_GMU2HOST_MASK, ~0u);     /* nothing it says can wake anyone after us */
		adreno8_wr(adreno8_gmu.base + GMU_AHB_FENCE_CLR, ~0u);
		if (adreno8_gmu_runs) {
			adreno8_wr(adreno8_gmu.base + GMU_CM3_SYSRESET, 1);
			printf("adreno8: stop: the GMU's processor back in reset; its firmware stops with its memory\n");
		}
		adreno8_gmu_runs = adreno8_gmu_talks = false;
		if (adreno8_rsc_woken_by_us) {
			adreno8_wr(adreno8_gmu.base + GMU_RSCC_CONTROL_REQ, adreno8_rd(adreno8_gmu.base + GMU_RSCC_CONTROL_REQ) & ~2u);
			printf("adreno8: stop: our RSC wake request dropped; it may sleep again\n");
			adreno8_rsc_woken_by_us = false;
		}
	}
	if (adreno8_gmu_space.is_on) {
		adreno8_quiet = true;                               /* the last few writes say nothing: stop is meant to be short */
		adreno8_space_off(&adreno8_gmu_space, GMU_MATCH);
		adreno8_quiet = false;
		printf("adreno8: stop: the GMU's translation context off again\n");
	}
	for (unsigned i = 0; i < adreno8_gmu_space.table_count; i++) {
		if (adreno8_gmu_space.tables[i].table != 0) {
			kmem_free(kernel_map, adreno8_gmu_space.tables[i].table, PAGE_SIZE);
		}
	}
	adreno8_gmu_space.table_count = 0;
	if (!adreno8_cx_was_on && adreno8_gpucc.base != 0) {
		adreno8_put(&adreno8_gpucc, "cx gdscr: back down, as we found it", GPUCC_CX_GDSCR, GDSC_SW_COLLAPSE);
	}
	for (unsigned i = 0; i < adreno8_gmu_demand_count; i++) {
		if (adreno8_gmu_demands[i].memory != 0) {
			kmem_free(kernel_map, adreno8_gmu_demands[i].memory, (adreno8_gmu_demands[i].size + PAGE_MASK) & ~PAGE_MASK);
			adreno8_gmu_demands[i].memory = 0;
		}
	}
	adreno8_gmu_demand_count = 0;
	for (unsigned i = 0; i < 4; i++) {
		struct adreno8_gpu_block *b = i == 0 ? &adreno8_gmu_log : i == 1 ? &adreno8_gmu_hfi : i == 2 ? &adreno8_gmu_debug : &adreno8_gmu_dummy;

		if (b->memory != 0) {
			kmem_free(kernel_map, b->memory, (b->size + PAGE_MASK) & ~PAGE_MASK);
			b->memory = 0;
		}
	}
	/* the uncached views of the log and queues stay mapped: ml_io_map_wcomb has no undo, and boot is once */
	printf("adreno8: stop: done; whatever the boot loader had left of the GPU is left, and our own part of it is gone\n");
}

/* ---- the walk ----------------------------------------------------------------------------------------------- */

/*
 * The sixteen steps, one after the other, up to the number the boot arg asked for. Culture from
 * redwood's dispatcher unchanged: pauses after each step to let the serial reader keep up, the
 * first failure ending the walk with its "not attempted" line, the watch loop for keeping the
 * phone running with the GMU answering for a while, and keep/return for the two ways of ending
 * that mean "leave it running" and "boot continues from inside here".
 */
void
adreno8_init(void)
{
	static bool (*const each[])(void) = {
		adreno8_step_outside, adreno8_step_gpucc, adreno8_step_cx_power, adreno8_step_cx_awake, adreno8_step_clocks,
		adreno8_step_first_words, adreno8_step_gmu_memory, adreno8_step_smmu_read, adreno8_step_gmu_space, adreno8_step_firmware,
		adreno8_step_gmu_start, adreno8_step_queues, adreno8_step_tables, adreno8_step_core_fw, adreno8_step_gx_power, adreno8_step_last_look
	};
	const unsigned count = sizeof(each) / sizeof(each[0]);
	uint32_t asked = 0, only = 0, one = 0;

	if (!PE_parse_boot_argn("adreno8", &asked, sizeof(asked)) || asked == 0) {
		return;
	}
	PE_parse_boot_argn("adreno8_only", &only, sizeof(only));
	PE_parse_boot_argn("adreno8_pause", &adreno8_pause_ms, sizeof(adreno8_pause_ms));
	PE_parse_boot_argn("adreno8_watch", &adreno8_watch_seconds, sizeof(adreno8_watch_seconds));
	PE_parse_boot_argn("adreno8_speed", &adreno8_speed_asked, sizeof(adreno8_speed_asked));
	PE_parse_boot_argn("adreno8_gmuclk", &adreno8_gmu_from_pll, sizeof(adreno8_gmu_from_pll));
	adreno8_gpu8_keep = PE_parse_boot_argn("adreno8_keep", &one, sizeof(one)) && one != 0;
	one = 0;
	adreno8_gpu8_return = PE_parse_boot_argn("adreno8_return", &one, sizeof(one)) && one != 0;
	one = 0;
	adreno8_fine = PE_parse_boot_argn("adreno8_fine", &one, sizeof(one)) && one != 0;
	PE_parse_boot_argn("adreno8_skip", &adreno8_skip_steps, sizeof(adreno8_skip_steps));
	if (asked > count) {
		asked = count;
	}
	printf("adreno8: the Adreno 830 of SM8750, step by step: up to step %u of %u asked for%s%s (only, skip, pause, fine, watch, keep, speed, gmuclk, return all hang off the same name)\n",
	    asked, count, only != 0 ? " -- but only one step will run" : "", adreno8_gpu8_keep ? " -- and everything stays up" : "");
	for (unsigned i = 0; i < count; i++) {
		unsigned number = i + 1;

		adreno8_step = number;
		if (only != 0 ? number != only : number > asked || (adreno8_skip_steps >> i & 1) != 0) {
			continue;
		}
		if (!each[i]()) {
			printf("adreno8: %u: FAILED; the steps after it are not attempted\n", number);
			break;
		}
		if (number > adreno8_newest_step) {
			adreno8_newest_step = number;
		}
		adreno8_hold(false);
	}
	if (adreno8_watch_seconds != 0 && adreno8_newest_step >= 11 && adreno8_gmu.base != 0) {
		for (unsigned s = 1; s <= adreno8_watch_seconds; s++) {
			adreno8_sleep_ms(1000);
			printf("adreno8: alive %u s after step %u: the GMU's counter %#x%08x, its log %u lines, its interrupts %#x, the power word %#x\n",
			    s, adreno8_newest_step, adreno8_rd(adreno8_gmu.base + GMU_ALWAYS_ON_HI), adreno8_rd(adreno8_gmu.base + GMU_ALWAYS_ON_LO),
			    adreno8_rd(adreno8_gmu.base + GMU_GENERAL_9) & 0xffff, adreno8_rd(adreno8_gmu.base + GMU_AO_INTERRUPT_STATUS),
			    adreno8_rd(adreno8_gmu.base + GMU_PWR_CLK_STATUS));
		}
	}
	if (adreno8_gpu8_keep) {
		printf("adreno8: keeping everything up (adreno8_keep): the GMU's firmware goes on running and answering\n");
	} else if (adreno8_newest_step >= 3) {
		adreno8_gpu8_block_stop();
	}
	printf("adreno8: over; the system goes on starting\n");
	if (adreno8_gpu8_return) {
		printf("adreno8: adreno8_return: the boot goes on from in here, as redwood's adreno_return does\n");
		loader_return();
	}
}

/*
 * The boot is about to hand memory out again (loader_return): make sure nothing of the GPU's can
 * still write into it or wake anyone afterwards. Cheap and guarded: on a walk that went well the
 * stop already did all of this.
 */
void
adreno8_quiesce(void)
{
	if (adreno8_gmu.base != 0 && adreno8_gmu_runs) {
		adreno8_wr(adreno8_gmu.base + GMU_GMU2HOST_MASK, ~0u);
		adreno8_wr(adreno8_gmu.base + GMU_AHB_FENCE_CLR, ~0u);
		adreno8_wr(adreno8_gmu.base + GMU_CM3_SYSRESET, 1);
		adreno8_gmu_runs = false;
	}
	if (adreno8_gmu_space.is_on) {
		adreno8_quiet = true;
		adreno8_space_off(&adreno8_gmu_space, GMU_MATCH);
		adreno8_quiet = false;
	}
}
