/*
 * adreno8_smmu.c -- the SMMU the GMU speaks through: its layout, the GMU's address space and its translation context: step 8 and the machinery steps 9 to 16 live in
 * The ladder, the boot-args and what is not settled: see adreno8.c; the shared world: adreno8_private.h.
 */
#include "adreno8_private.h"

#define printf adreno8_say

/* ---- step 8: the SMMU in front of the GPU ---------------------------------------------------------------- */

/*
 * dt: kgsl-smmu@3da0000 (qcom,qsmmu-v500: an Arm MMU-500), 0x40000 long. Its streams (dt: the
 * "iommus" of qcom,kgsl-iommu and qcom,gmu): 0 the 3D core, 5 the GMU, both with mask cell 0x400
 * -- the mask no document here explains; every user of this SMMU repeats it, so it is repeated.
 * The context banks: the vendor's device tree names none of them with a reg of its own, so the
 * layout is computed from the ID registers with the formulas redwood ran successfully with -- and
 * printed, not enforced: the readbacks after the first write to context 1 are the test. (Offsets:
 * arm-smmu.h.)
 */

static struct adreno8_block adreno8_smmu = { "smmu", 0x03da0000, 0x40000 };

static uint32_t adreno8_smmu_page, adreno8_smmu_pages, adreno8_smmu_contexts, adreno8_smmu_matches;

/* The layout of the register space from the ID registers: printed; only the context's room is required. */

static bool
adreno8_smmu_layout(void)
{
	uint32_t idr0 = adreno8_get(&adreno8_smmu, "idr0", SMMU_IDR0), idr1 = adreno8_get(&adreno8_smmu, "idr1", SMMU_IDR1);

	adreno8_smmu_page = idr1 >> 31 ? 0x10000 : 0x1000;
	adreno8_smmu_pages = 2u << (idr1 >> 28 & 7);
	adreno8_smmu_contexts = idr1 & 0xff;
	adreno8_smmu_matches = idr0 & 0xff;
	printf("adreno8: %u: SMMU: %u stream match registers, %u contexts, %u pages of %#x before the contexts' own\n", adreno8_step,
	    adreno8_smmu_matches, adreno8_smmu_contexts, adreno8_smmu_pages, adreno8_smmu_page);
	if (2 * adreno8_smmu_pages * adreno8_smmu_page != adreno8_smmu.size) {
		printf("adreno8: %u: that makes %#x of registers, the device tree says %#x: believed anyway (redwood's formula, an open question here)\n",
		    adreno8_step, 2 * adreno8_smmu_pages * adreno8_smmu_page, adreno8_smmu.size);
	}
	if ((adreno8_smmu_pages + 2) * adreno8_smmu_page > adreno8_smmu.size) {
		printf("adreno8: %u: context 1 would sit at %#x, past the mapping: it cannot be used\n", adreno8_step,
		    (adreno8_smmu_pages + 1) * adreno8_smmu_page);
		return false;
	}
	return true;
}

bool
adreno8_step_smmu_read(void)
{
	if (!adreno8_cx_ready()) {
		return false;
	}
	adreno8_announce("read the ID registers of the GPU's SMMU (0x3da0000): global registers, which the hypervisor answers for");
	if (!adreno8_smmu_layout()) {
		return false;
	}
	adreno8_get(&adreno8_smmu, "idr2", SMMU_IDR2);
	adreno8_get(&adreno8_smmu, "idr7", SMMU_IDR7);
	adreno8_get(&adreno8_smmu, "scr0", SMMU_SCR0);
	adreno8_get(&adreno8_smmu, "sgfsr", SMMU_SGFSR);

	adreno8_announce("read the SMMU's stream table and the kinds of its contexts, as the boot loader and hypervisor left them");
	for (uint32_t i = 0; i < adreno8_smmu_matches && i < 16; i++) {
		adreno8_get(&adreno8_smmu, "smr", SMMU_SMR(i));
		adreno8_get(&adreno8_smmu, "s2cr", SMMU_S2CR(i));
	}
	for (uint32_t i = 0; i < adreno8_smmu_contexts && i < 16; i++) {
		adreno8_get(&adreno8_smmu, "cbar", adreno8_smmu_page + SMMU_CBAR(i));
	}

	adreno8_announce("read the first words of the two contexts we might use: 0, the 3D core's, as the loader left it, and 1, which the GMU will get");
	for (uint32_t cb = 0; cb < 2; cb++) {
		uint32_t base = (adreno8_smmu_pages + cb) * adreno8_smmu_page;

		adreno8_get(&adreno8_smmu, "cb sctlr", base + SMMU_CB_SCTLR);
		adreno8_get(&adreno8_smmu, "cb ttbr0, low", base + SMMU_CB_TTBR0);
		adreno8_get(&adreno8_smmu, "cb ttbr0, high", base + SMMU_CB_TTBR0 + 4);
		adreno8_get(&adreno8_smmu, "cb tcr", base + SMMU_CB_TCR);
		adreno8_get(&adreno8_smmu, "cb fsr", base + SMMU_CB_FSR);
	}
	return true;
}

/* ---- the GMU's translation context ------------------------------------------------------------------------ */

/*
 * A context bank of ours with three-level 39-bit tables of our own memory in it (Linux: the
 * arm-smmu driver with qcom,use-3-lvl-tables). Page tables are carved from one page of kernel
 * memory at a time, as redwood's were; every entry that goes in is a 4K page of the GMU's
 * requested memory at that same 4K address -- the firmware's addresses are used where it asked,
 * which spares any translation of its own scheme.
 */

struct adreno8_gpu_space adreno8_gmu_space = { "gmu", 1 };

vm_offset_t
adreno8_space_new_table(struct adreno8_gpu_space *space, unsigned level, uint32_t index)
{
	struct adreno8_space_table *t;

	if (space->table_count == 1024) {
		printf("adreno8: %u: out of page tables for the GMU's context\n", adreno8_step);
		return 0;
	}
	t = &space->tables[space->table_count++];
	if (kmem_alloc(kernel_map, (vm_offset_t *)&t->table, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA,
	    VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("adreno8: %u: no kernel memory for a page table\n", adreno8_step);
		t->table = 0;
		return 0;
	}
	t->phys = kvtophys(t->table);
	t->level = level;
	t->index = index;
	if (level > 0) {
		/* hang it under the level above */
		struct adreno8_space_table *above = NULL;
		uint32_t want = level == 1 ? index >> 9 : index >> 9;

		for (unsigned i = 0; i < space->table_count; i++) {
			if (space->tables[i].level == level - 1 && space->tables[i].index == want) {
				above = &space->tables[i];
			}
		}
		if (above == NULL) {
			printf("adreno8: %u: internal: no table above the new one\n", adreno8_step);
			return 0;
		}
		((uint64_t *)above->table)[index & 0x1ff] = (t->phys & PTE_ADDRESS) | PTE_TABLE;
	}
	return t->table;
}

/* Point the GMU's address at this page of our memory. */

bool
adreno8_space_map(struct adreno8_gpu_space *space, uint32_t address, uint64_t phys)
{
	struct adreno8_space_table *l2 = NULL, *l3 = NULL;
	uint32_t i0 = address >> 30, i1 = address >> 21 & 0x1ff, i2 = address >> 12 & 0x1ff;

	for (unsigned i = 0; i < space->table_count && l2 == NULL; i++) {
		if (space->tables[i].level == 1 && space->tables[i].index == i0) {
			l2 = &space->tables[i];
		}
	}
	if (l2 == NULL) {
		vm_offset_t t = adreno8_space_new_table(space, 1, i0);

		if (t == 0) {
			return false;
		}
		for (unsigned i = 0; i < space->table_count; i++) {
			if (space->tables[i].level == 1 && space->tables[i].index == i0) {
				l2 = &space->tables[i];
			}
		}
	}
	for (unsigned i = 0; i < space->table_count && l3 == NULL; i++) {
		if (space->tables[i].level == 2 && space->tables[i].index == (i0 << 9 | i1)) {
			l3 = &space->tables[i];
		}
	}
	if (l3 == NULL) {
		if (adreno8_space_new_table(space, 2, i0 << 9 | i1) == 0) {
			return false;
		}
		for (unsigned i = 0; i < space->table_count; i++) {
			if (space->tables[i].level == 2 && space->tables[i].index == (i0 << 9 | i1)) {
				l3 = &space->tables[i];
			}
		}
	}
	((uint64_t *)l3->table)[i2] = (phys & PTE_ADDRESS) | PTE_PAGE;
	return true;
}

/* The whole GMU view of one of its memory blocks: entry per 4K page, one address each. */

bool
adreno8_space_map_block(struct adreno8_gpu_space *space, const struct adreno8_gpu_block *b)
{
	unsigned pages = (b->size + PAGE_MASK) / PAGE_SIZE;

	for (unsigned i = 0; i < pages; i++) {
		if (!adreno8_space_map(space, b->address + i * PAGE_SIZE, kvtophys(b->memory + i * PAGE_SIZE))) {
			return false;
		}
	}
	printf("adreno8: %u: %u pages of %#x mapped to themselves in the GMU's context (%s)\n", adreno8_step, pages, b->address,
	    b->name);
	return true;
}

/*
 * Turn the context on, in the order the arm-smmu driver and redwood use: the tables out of our
 * caches first, then the context off, the addresses in, the fault bits cleared, the old entries
 * out of the translation caches, and only then translation on -- and last the stream table, so a
 * translated request can only come after everything it needs is in place.
 */

bool
adreno8_space_on(struct adreno8_gpu_space *space, uint32_t stream, uint32_t match)
{
	uint64_t root;
	uint32_t cb, control;

	adreno8_announce("start the GMU's translation context: its tables, its registers, and its stream through it");
	if (!adreno8_smmu_layout()) {
		return false;
	}
	for (unsigned i = 0; i < space->table_count; i++) {
		adreno8_to_memory(space->tables[i].table, PAGE_SIZE);
	}
	root = space->tables[0].phys;
	cb = (adreno8_smmu_pages + space->context) * adreno8_smmu_page;
	control = 0;

	adreno8_put(&adreno8_smmu, "cb sctlr", cb + SMMU_CB_SCTLR, 0);
	adreno8_put(&adreno8_smmu, "cb a2r", adreno8_smmu_page + SMMU_CBA2R(space->context), 1);
	adreno8_put(&adreno8_smmu, "cb bar", adreno8_smmu_page + SMMU_CBAR(space->context), STAGE_1);
	adreno8_put(&adreno8_smmu, "cb tcr2", cb + SMMU_CB_TCR2, TCR2_39_BITS);
	adreno8_put(&adreno8_smmu, "cb tcr", cb + SMMU_CB_TCR, TCR_39_BITS);
	adreno8_put(&adreno8_smmu, "cb ttbr0, low", cb + SMMU_CB_TTBR0, (uint32_t)root);
	adreno8_put(&adreno8_smmu, "cb ttbr0, high", cb + SMMU_CB_TTBR0 + 4, (uint32_t)(root >> 32) | space->context << 16);
	adreno8_put(&adreno8_smmu, "cb ttbr1, low", cb + SMMU_CB_TTBR1, 0);
	adreno8_put(&adreno8_smmu, "cb ttbr1, high", cb + SMMU_CB_TTBR1 + 4, 0);
	adreno8_put(&adreno8_smmu, "cb mair0", cb + SMMU_CB_MAIR0, MAIR0_NORMAL);
	adreno8_put(&adreno8_smmu, "cb fsr", cb + SMMU_CB_FSR, FSR_ALL);
	adreno8_put(&adreno8_smmu, "cb tlb asid", cb + SMMU_CB_TLBASID, SMR_VALID | space->context);
	adreno8_put(&adreno8_smmu, "cb tlb invalidate", cb + SMMU_CB_TLBIALL, 1);
	adreno8_put(&adreno8_smmu, "cb tlb sync", cb + SMMU_CB_TLBSYNC, 1);
	if (!adreno8_wait_for(&adreno8_smmu, cb + SMMU_CB_TLBSTATUS, 1, 0, 100)) {
		printf("adreno8: %u: the SMMU is still syncing its caches after 100 ms\n", adreno8_step);
		return false;
	}
	adreno8_put(&adreno8_smmu, "cb sctlr", cb + SMMU_CB_SCTLR, SCTLR_ON | control);
	if (adreno8_get(&adreno8_smmu, "cb sctlr", cb + SMMU_CB_SCTLR) != (SCTLR_ON | control) ||
	    adreno8_get(&adreno8_smmu, "cb ttbr0, low", cb + SMMU_CB_TTBR0) != (uint32_t)root ||
	    adreno8_get(&adreno8_smmu, "cb bar", adreno8_smmu_page + SMMU_CBAR(space->context)) != STAGE_1) {
		printf("adreno8: %u: context %u does not hold what was written into it: the layout above must be wrong\n", adreno8_step,
		    space->context);
		return false;
	}
	adreno8_put(&adreno8_smmu, "s2cr", SMMU_S2CR(match), space->context);
	adreno8_put(&adreno8_smmu, "smr", SMMU_SMR(match), SMR_VALID | SMR_MASK << 16 | stream);
	if ((adreno8_get(&adreno8_smmu, "s2cr", SMMU_S2CR(match)) & 0x300ff) != space->context ||
	    adreno8_get(&adreno8_smmu, "smr", SMMU_SMR(match)) != (SMR_VALID | SMR_MASK << 16 | stream)) {
		printf("adreno8: %u: stream %u does not come to context %u\n", adreno8_step, stream, space->context);
		return false;
	}
	space->is_on = true;
	printf("adreno8: %u: THE GMU'S TRANSLATION IS UP: stream %u, context %u, tables at %#llx\n", adreno8_step, stream,
	    space->context, root);
	return true;
}

void
adreno8_space_off(struct adreno8_gpu_space *space, uint32_t match)
{
	uint32_t cb = (adreno8_smmu_pages + space->context) * adreno8_smmu_page;

	if (!space->is_on) {
		return;
	}
	adreno8_put(&adreno8_smmu, "smr", SMMU_SMR(match), 0);
	adreno8_put(&adreno8_smmu, "s2cr", SMMU_S2CR(match), 2u << 16);       /* fault: a request now answers instead of wandering */
	adreno8_put(&adreno8_smmu, "cb sctlr", cb + SMMU_CB_SCTLR, 0);
	space->is_on = false;
}

bool
adreno8_space_faulted(struct adreno8_gpu_space *space)
{
	uint32_t cb = (adreno8_smmu_pages + space->context) * adreno8_smmu_page, fsr;

	fsr = adreno8_rd(adreno8_smmu.base + cb + SMMU_CB_FSR) & FSR_ALL;
	if (fsr == 0) {
		return false;
	}
	printf("adreno8: %u: the GMU's context refused something: fault %#x at %#x:%#x, reason %#x\n", adreno8_step, fsr,
	    adreno8_rd(adreno8_smmu.base + cb + SMMU_CB_FAR_HI), adreno8_rd(adreno8_smmu.base + cb + SMMU_CB_FAR_LO),
	    adreno8_rd(adreno8_smmu.base + cb + SMMU_CB_FSYNR0));
	return true;
}
