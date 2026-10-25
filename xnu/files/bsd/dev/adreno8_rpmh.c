/*
 * Apps DRV2 ARC read: fixed MiCode rpmh-rsc.c v3 register layout and trigger order,
 * https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/f66f8c8bcbe38044fab61d44ea7f7c224f82b3ea/drivers/soc/qcom/rpmh-rsc.c
 * plus upstream read protocol from edbafe65eef2b58625db1e113fbbfb1fe10c0291:
 * https://git.kernel.org/linus/edbafe65eef2b58625db1e113fbbfb1fe10c0291
 * Read requests use RESP_REQ, omit WRITE and CMD_DATA, and return CMD_RESP_DATA.
 * RSC v3.8 uses an eight-byte message; the four-byte change applies from v4.5.
 */
#include "adreno8_rpmh_private.h"

#define printf adreno8_say
#define ARC_IRQ_ENABLE 0xd00u
#define ARC_IRQ_STATUS 0xd04u
#define ARC_IRQ_CLEAR 0xd08u
#define ARC_WAIT 0x1260u
#define ARC_CONTROL 0x1264u
#define ARC_STATUS 0x1268u
#define ARC_ENABLE 0x126cu
#define ARC_MSGID 0x1274u
#define ARC_ADDRESS 0x1278u
#define ARC_COMMAND_STATUS 0x1280u
#define ARC_RESPONSE 0x1284u
#define ARC_TCS_BIT (1u << 2)
#define ARC_AMC_ENABLE (1u << 16)
#define ARC_AMC_TRIGGER (1u << 24)
#define ARC_COMMAND_DONE ((1u << 8) | (1u << 16))

static struct adreno8_block arc_rsc = { "apps-rsc-drv2", 0x16520000, 0x10000 };
static bool arc_attempted;

static uint32_t
arc_read(const char *name, uint32_t offset)
{
	uint32_t value = adreno8_get(&arc_rsc, name, offset);
	adreno8_settle();
	return value;
}

static bool
arc_write(const char *name, uint32_t offset, uint32_t value)
{
	adreno8_settle();
	adreno8_put(&arc_rsc, name, offset, value);
	adreno8_settle();
	if (arc_read(name, offset) == value)
		return true;
	printf("adreno8: ARC read register verification failed at %#x; retain TCS2 until reset\n", offset);
	return false;
}

bool
adreno8_rpmh_arc_read(void)
{
	uint16_t levels[RPMH_LEVELS];
	unsigned count = 0;
	uint32_t asked = 0, touch = 0, address, id, solver, config, control, irq_before, irq, response;

	PE_parse_boot_argn("adreno8_arc_read", &asked, sizeof(asked));
	if (asked == 0)
		return true;
	if (asked != 1 || arc_attempted) {
		printf("adreno8: ARC read requires value 1 and permits one attempt per kernel\n");
		return false;
	}
	arc_attempted = true;
	if (!PE_parse_boot_argn("touchscreen", &touch, sizeof(touch)) || touch != 0) {
		printf("adreno8: ARC read requires explicit touchscreen=0 to exclude the other DRV2 writer\n");
		return false;
	}
	address = adreno8_rpmh_lookup("cx.lvl", levels, &count, NULL, NULL);
	if ((address >> 16) != 3 || (address & 3) != 0 || count == 0 || count > RPMH_LEVELS) {
		printf("adreno8: ARC read has no valid cx.lvl address and level table\n");
		return false;
	}
	id = arc_read("id", 0);
	solver = arc_read("solver config", 4);
	config = arc_read("parent child config", 0xc);
	control = arc_read("TCS2 control", ARC_CONTROL);
	irq_before = arc_read("irq enable", ARC_IRQ_ENABLE);
	if (((id >> 8) & 0xffff) != 0x308 || id == 0xffffffffu || solver == 0xffffffffu ||
	    (solver & (1u << 24)) != 0 || config == 0xffffffffu ||
	    ((config >> 12) & 0x3f) != 8 || (config >> 27) != 16 ||
	    irq_before == 0xffffffffu || (control & ~ARC_AMC_ENABLE) != 0 ||
	    arc_read("TCS0 status", 0xd28) != 1 || arc_read("TCS1 status", 0xfc8) != 1 ||
	    arc_read("TCS2 status", ARC_STATUS) != 1 ||
	    arc_read("TCS2 enable", ARC_ENABLE) != 0 || arc_read("TCS2 wait", ARC_WAIT) != 0 ||
	    (arc_read("irq status", ARC_IRQ_STATUS) & ARC_TCS_BIT) != 0) {
		printf("adreno8: ARC read prerequisites failed; no registers written\n");
		return false;
	}

	/* A failed write or an unanswered request has no verified cancellation path.
	 * The reset requirement precedes the first write and remains set after success. */
	adreno8_reset_required = true;
	if (!arc_write("TCS2 clear trigger", ARC_CONTROL, control & ~ARC_AMC_TRIGGER) ||
	    !arc_write("TCS2 clear enable", ARC_CONTROL, 0) ||
	    !arc_write("TCS2 read message", ARC_MSGID, 0x108) ||
	    !arc_write("TCS2 cx.lvl address", ARC_ADDRESS, address) ||
	    !arc_write("TCS2 command enable", ARC_ENABLE, 1))
		return false;
	irq = arc_read("irq enable before request", ARC_IRQ_ENABLE);
	if (irq != irq_before || !arc_write("irq enable TCS2", ARC_IRQ_ENABLE, irq | ARC_TCS_BIT) ||
	    !arc_write("TCS2 AMC enable", ARC_CONTROL, ARC_AMC_ENABLE))
		return false;
	adreno8_settle();
	adreno8_put(&arc_rsc, "TCS2 ARC read trigger", ARC_CONTROL, ARC_AMC_ENABLE | ARC_AMC_TRIGGER);
	adreno8_settle();

	/* Match the upstream ten-second completion bound without per-poll log output. */
	for (unsigned poll = 0; poll < 100000; poll++) {
		irq = adreno8_rd(arc_rsc.base + ARC_IRQ_STATUS);
		adreno8_settle();
		if (irq & ARC_TCS_BIT)
			break;
		adreno8_sleep_us(100);
	}
	irq = arc_read("irq status after request", ARC_IRQ_STATUS);
	if (!(irq & ARC_TCS_BIT) ||
	    (arc_read("TCS2 command status", ARC_COMMAND_STATUS) & ARC_COMMAND_DONE) != ARC_COMMAND_DONE ||
	    arc_read("TCS2 completion status", ARC_STATUS) != 1) {
		printf("adreno8: ARC read completion unconfirmed; retain TCS2 and IRQ state until reset\n");
		return false;
	}
	response = arc_read("TCS2 cx.lvl response", ARC_RESPONSE);
	printf("adreno8: ARC cx.lvl response=%#x, levels=%u\n", response, count);
	if (response >= count) {
		printf("adreno8: ARC read response is outside the CMD DB table; retain TCS2 until reset\n");
		return false;
	}
	irq = arc_read("TCS2 control after completion", ARC_CONTROL);
	if ((irq & ~(ARC_AMC_ENABLE | ARC_AMC_TRIGGER)) != 0 ||
	    !arc_write("TCS2 completed clear trigger", ARC_CONTROL, irq & ~ARC_AMC_TRIGGER) ||
	    !arc_write("TCS2 completed clear enable", ARC_CONTROL, 0) ||
	    !arc_write("TCS2 completed command disable", ARC_ENABLE, 0))
		return false;
	adreno8_settle();
	adreno8_put(&arc_rsc, "TCS2 completed IRQ clear", ARC_IRQ_CLEAR, ARC_TCS_BIT);
	adreno8_settle();
	if ((arc_read("irq status after clear", ARC_IRQ_STATUS) & ARC_TCS_BIT) != 0 ||
	    !arc_write("TCS2 restore initial control", ARC_CONTROL, control))
		return false;
	irq = arc_read("irq enable before restore", ARC_IRQ_ENABLE);
	if (!arc_write("irq restore TCS2 bit", ARC_IRQ_ENABLE, irq & ~(ARC_TCS_BIT & ~irq_before)))
		return false;
	printf("adreno8: ARC cx.lvl inherited selector %u, level %u; one read completed, host cold reset required\n",
	    response, levels[response]);
	return true;
}
