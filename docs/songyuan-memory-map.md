# songyuan (SM8850) memory map — draft

Generated from the phone's live device tree (`.scratch/songyuan-fdt.dtb`, dumped from
Android's `/sys/firmware/fdt`) plus the boot conventions the annibale port uses.

## What is already known to match annibale

| Item | songyuan | annibale |
|---|---|---|
| splash / display region | `0xFC800000 + 0x02B00000` | `0xFC800000 + 0x02B00000` |
| GIC distributor | `0x17000000` | `0x16000000` |
| GIC redistributors | `0x17080000`, stride `0x40000` | `0x16080000` |
| CPU MPIDR set | 0,0x100..0x500,0x10000,0x10100 | same |
| timer | 19.2 MHz | 19.2 MHz |
| UEFI load region / payload window | **TODO: verify** | `0xA7100000 + 0x300000` / `0xF0000000` |

## DT reserved-memory regions (48, verbatim from the live DT)

```c
  {"GUNYAH_HYP_REGION   ", 0x80000000, 0x00E00000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"RESERVED_REGION     ", 0x80E00000, 0x00400000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CPUCP_PDP_REGION    ", 0x81200000, 0x00200000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"XBL_DTLOG_REGION    ", 0x81A00000, 0x00060000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"AOP_IMAGE_REGION    ", 0x81C00000, 0x00060000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"AOP_CMD_DB_REGION   ", 0x81C60000, 0x00020000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"AOP_TME_UEFI_MERGED_", 0x81C80000, 0x00074000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SMEM_REGION         ", 0x81D00000, 0x00200000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"PDP_NS_SHARED_REGION", 0x81F00000, 0x00100000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CPUCP_SCANDUMP_REGIO", 0x82000000, 0x00380000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"ADSP_MHI_REGION     ", 0x82380000, 0x00020000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SOCCP_SDI_REGION    ", 0x823A0000, 0x00040000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"PMIC_MINII_DUMP_REGI", 0x823E0000, 0x00080000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"QUP_FW_REGION       ", 0x82460000, 0x00018000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"PVM_FW_REGION       ", 0x824A0000, 0x00100000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"TZ_STAT_REGION      ", 0x82700000, 0x00100000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"QDSS_APPS_REGION    ", 0x82800000, 0x02000000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"DSM_PARTITION_1_REGI", 0x84A00000, 0x05500000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"DSM_PARTITION_2_REGI", 0x89F00000, 0x00A80000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"MPSS_REGION         ", 0x8AA00000, 0x0EB00000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"Q6_MPSS_DTB_REGION  ", 0x99500000, 0x00080000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"IPA_FW_REGION       ", 0x99580000, 0x00010000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"IPA_GSI_REGION      ", 0x99590000, 0x0000A000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"GPU_MICROCODE_REGION", 0x9959A000, 0x00002000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CAMERA_REGION       ", 0x99600000, 0x00800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CAMERA_2_REGION     ", 0x99E00000, 0x00800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"VIDEO_REGION        ", 0x9A600000, 0x00800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CVP_REGION          ", 0x9AE00000, 0x00700000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"CDSP_REGION         ", 0x9B500000, 0x01900000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"Q6_CDSP_DTB_REGION  ", 0x9CE00000, 0x00080000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"DCP_DTB_REGION      ", 0xA0050000, 0x00080000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"DCP_REGION          ", 0xA00D0000, 0x00300000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SOCCP_REGION        ", 0xA03D0000, 0x00500000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SOCCP_DTB_REGION    ", 0xA08D0000, 0x00040000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"Q6_ADSP_DTB_REGION  ", 0xA1380000, 0x00080000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"ADSPSLPI_REGION     ", 0xA1400000, 0x05580000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"HYPERVISOR_EXTENDED_", 0xC3DD0000, 0x00A30000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"XBL_RAMDUMP_REGION  ", 0xD7800000, 0x00200000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"GLOBAL_SYNC_REGION  ", 0xD7A00000, 0x00200000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"TZ_MERGED_REGION    ", 0xD8000000, 0x00800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"TRUST_UI_VM_REGION  ", 0xF3800000, 0x05000000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"OEM_VM_REGION       ", 0xF8800000, 0x03800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"DUMP_DISPLAY_REGION ", 0xFF3FF800, 0x00000800, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"RAMOOPS_REGION      ", 0xFF400000, 0x00200000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"LLCC_LPI_REGION     ", 0xFF800000, 0x00800000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"LCP_DARE_REGION     ", 0x880000000, 0x06000000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SHM                 ", 0xFC200000, 0x00400000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"SPLASH_REGION       ", 0xFC800000, 0x02B00000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
```

## Method to finish the map

1. For every DT reserved region, map it `Reserv` at its real size (shifted and merged
   only where the DT overlaps).
2. Conventional memory = the `/memory` ranges minus those reserved regions; the
   UEFI FD region, the kernel reservation and the XNUP payload window are carved out
   of it the way `uefi/annibale/files/Platforms/Xiaomi/annibalePkg/Library/MemoryMapLib`
   does (verify every address against this DT before trusting it).
3. Device MMIO: start from annibale's list, but move `APSS_HM` to `0x17000000` and add
   the UFS/ICE windows the loader's storage survey needs.
4. The display region is the same as annibale, so the 1200x2608 panel path can keep
   `DIRECT_PHONE_PLAT` and the loader's GOP-less behaviour.
