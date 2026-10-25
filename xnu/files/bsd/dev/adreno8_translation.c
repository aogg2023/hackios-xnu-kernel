/* Validate the complete secure mapping before MP_ASSIGN changes page ownership. */
#include "adreno8_memory_private.h"

#define printf adreno8_say

/* Resolve descriptors only to table allocations owned by this address space.
 * A descriptor with extra attributes is rejected: this mapper emits PA | 3. */
static const struct adreno8_space_table *
adreno8_translation_table(const struct adreno8_gpu_space *space, uint64_t descriptor,
    unsigned level, uint32_t index)
{
	for (unsigned i = 0; i < space->table_count; i++) {
		const struct adreno8_space_table *table = &space->tables[i];

		if (table->level == level && table->index == index && table->table != 0 &&
		    table->phys != 0 && table->phys < SMMU_PHYS_LIMIT &&
		    (table->phys & SMMU_GRANULE_MASK) == 0 &&
		    kvtophys(table->table) == table->phys && descriptor == (table->phys | PTE_TABLE)) {
			return table;
		}
	}
	return NULL;
}

/* The root and both intermediate descriptors are checked for each 4 KiB leaf.
 * Data pages can be physically discontiguous even within one allocation. */
bool
adreno8_secure_translation_walk(struct adreno8_gpu_space *space, uint32_t iova,
    vm_offset_t memory, uint32_t size, uint32_t prot)
{
	uint64_t attributes = 3ULL | 2ULL << 8 | 1ULL << 10 | 1ULL << 11;
	const struct adreno8_space_table *root;

	if (space == NULL || space->context != SECURE_CONTEXT || space->is_on ||
	    space->table_count == 0 || space->table_count > sizeof(space->tables) / sizeof(space->tables[0]) ||
	    !(prot & ADRENO8_MEM_READ) || (prot & ~15u) || memory == 0 || size == 0 ||
	    ((iova | memory | size) & SMMU_GRANULE_MASK) ||
	    (uint64_t)iova + size > (1ULL << 32) || memory + size < memory) {
		return false;
	}
	root = adreno8_translation_table(space, space->tables[0].phys | PTE_TABLE, 0, 0);
	if (root != &space->tables[0]) {
		return false;
	}
	if (!(prot & ADRENO8_MEM_WRITE)) attributes |= 1ULL << 7;
	if (!(prot & ADRENO8_MEM_PRIV)) attributes |= 1ULL << 6;
	if (!(prot & ADRENO8_MEM_EXEC)) attributes |= 3ULL << 53;
	for (uint64_t at = 0; at < size; at += SMMU_GRANULE) {
		uint32_t address = iova + at;
		uint64_t descriptor = ((const uint64_t *)root->table)[address >> 30];
		const struct adreno8_space_table *middle =
		    adreno8_translation_table(space, descriptor, 1, address >> 30);
		const struct adreno8_space_table *leaf = NULL;
		uint64_t pte = 0, phys = kvtophys(memory + at);

		if (middle != NULL) {
			descriptor = ((const uint64_t *)middle->table)[address >> 21 & 0x1ff];
			leaf = adreno8_translation_table(space, descriptor, 2, address >> 21);
		}
		if (leaf != NULL) {
			pte = ((const uint64_t *)leaf->table)[address >> 12 & 0x1ff];
		}
		if (leaf == NULL || phys == 0 || phys >= SMMU_PHYS_LIMIT ||
		    (phys & SMMU_GRANULE_MASK) || pte != (phys | attributes)) {
			printf("adreno8: secure translation walk failed at IOVA %#x: descriptor %#llx,"
			    " PTE %#llx, expected PA %#llx attributes %#llx\n",
			    address, descriptor, pte, phys, attributes);
			return false;
		}
	}
	printf("adreno8: secure translation walk verified %u bytes (%u leaves) from IOVA %#x"
	    " through root PA %#llx\n", size, size / SMMU_GRANULE, iova, root->phys);
	return true;
}
