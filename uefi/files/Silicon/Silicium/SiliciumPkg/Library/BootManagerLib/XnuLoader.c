/**
  XNU payload entry point for annibale.

  Runs when BDS finds nothing to boot. The boot shim has copied the payload
  (see XnuLoader.h) to a fixed reserved address; if it carries "loader.efi",
  the image is started straight from memory so the loader can be iterated on
  without rebuilding this firmware. XnuLoaderMain does not return: a missing,
  invalid or returned-from payload ends in a local CPU halt with no firmware,
  console or storage call, so a failed boot stays dark and recovery is the
  agreed physical power-cycle.

  SPDX-License-Identifier: BSD-2-Clause-Patent
**/

#include <Uefi.h>

#include <Library/BaseLib.h>
#include <Library/UefiBootServicesTableLib.h>

#include "XnuLoader.h"

// XNU-TERM-BEGIN -- tools/test-xnu-payload-terminal.py replaces exactly this
// block on the host; on the target the block below runs as written.
__attribute__((noreturn))
STATIC
VOID
EFIAPI
XnuTerminalHalt (
  VOID
  )
{
  //
  // Stops this CPU only: other cores, submitted DMA and the watchdog keep
  // running, so this is not a whole-system quiesce. Masking DAIF is a system
  // register write; no MMIO and no firmware call follow it and no path here
  // returns. A synchronous exception taken after the mask still depends on
  // the exception environment and is not converted by this loop.
  //
  __asm__ volatile ("msr daifset, #0xf" ::: "memory");

  for (;;) {
  }
}
// XNU-TERM-END

//
// The payload is a list of named blobs. If it carries "loader.efi", that is
// the real XNU loader: start it straight from memory, so the loader can be
// iterated on without rebuilding this firmware.
//
STATIC
VOID
StartPayloadLoader ()
{
  XNU_PAYLOAD_HEADER *Header = (XNU_PAYLOAD_HEADER *)XNU_PAYLOAD_BASE;
  UINT8              *Cursor;
  UINT8              *End;
  UINTN              PaddedSize;

  //
  // Header->Size is the only length source, so every later bound derives from
  // it and is compared by subtraction (remaining minus header size) instead
  // of pointer addition, which could overflow on a bogus size. The fixed base
  // being readable is a boot-shim contract this function cannot verify.
  //
  if ((Header->Magic != XNU_PAYLOAD_MAGIC) || (Header->Size > XNU_PAYLOAD_MAX_SIZE)) {
    return;
  }

  Cursor = (UINT8 *)(Header + 1);
  End    = Cursor + Header->Size;

  while ((UINTN)(End - Cursor) >= sizeof (XNU_PAYLOAD_ENTRY)) {
    XNU_PAYLOAD_ENTRY *Entry = (XNU_PAYLOAD_ENTRY *)Cursor;

    if (Entry->Name[0] == 0) {
      break;
    }

    PaddedSize = ALIGN_VALUE ((UINTN)Entry->Size, 16);

    if (PaddedSize > ((UINTN)(End - Cursor) - sizeof (XNU_PAYLOAD_ENTRY))) {
      //
      // The entry claims data past the end of the container: an invalid
      // payload, so no address goes to LoadImage.
      //
      return;
    }

    if (AsciiStrnCmp (Entry->Name, "loader.efi", sizeof (Entry->Name)) == 0) {
      EFI_HANDLE Handle;

      if (Entry->Size == 0) {
        return;
      }

      if (!EFI_ERROR (gBS->LoadImage (FALSE, gImageHandle, NULL, Entry + 1, Entry->Size, &Handle))) {
        gBS->StartImage (Handle, NULL, NULL);
      }

      return;
    }

    Cursor += sizeof (XNU_PAYLOAD_ENTRY) + PaddedSize;
  }
}

VOID
XnuLoaderMain ()
{
  StartPayloadLoader ();

  //
  // Reached when no payload image is started and when a started image
  // returned -- StartImage returning EFI_SUCCESS is included, because a
  // payload that keeps running never comes back here.
  //
  XnuTerminalHalt ();
}
