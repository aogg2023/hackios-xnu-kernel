  INF MdeModulePkg/Core/Dxe/DxeMain.inf
  INF MdeModulePkg/Universal/PCD/Dxe/Pcd.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/EnvDxe/EnvDxe.inf

  INF MdeModulePkg/Universal/ReportStatusCodeRouter/RuntimeDxe/ReportStatusCodeRouterRuntimeDxe.inf
  INF MdeModulePkg/Universal/StatusCodeHandler/RuntimeDxe/StatusCodeHandlerRuntimeDxe.inf
  INF ArmPkg/Drivers/CpuDxe/CpuDxe.inf
  INF MdeModulePkg/Core/RuntimeDxe/RuntimeDxe.inf
  INF MdeModulePkg/Universal/SecurityStubDxe/SecurityStubDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ShmBridgeDxe/ShmBridgeDxeLA.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/TzDxe/ScmDxeCompat.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/TzDxe/TzDxeLA.inf

  INF MdeModulePkg/Universal/WatchdogTimerDxe/WatchdogTimer.inf
  INF MdeModulePkg/Universal/CapsuleRuntimeDxe/CapsuleRuntimeDxe.inf
  INF MdeModulePkg/Universal/Variable/RuntimeDxe/VariableRuntimeDxe.inf
  INF EmbeddedPkg/EmbeddedMonotonicCounter/EmbeddedMonotonicCounter.inf
  INF MdeModulePkg/Universal/ResetSystemRuntimeDxe/ResetSystemRuntimeDxe.inf
  INF EmbeddedPkg/RealTimeClockRuntimeDxe/RealTimeClockRuntimeDxe.inf
  INF EmbeddedPkg/MetronomeDxe/MetronomeDxe.inf
  INF MdeModulePkg/Universal/HiiDatabaseDxe/HiiDatabaseDxe.inf
  INF ArmPkg/Drivers/ArmGicDxe/ArmGicDxe.inf
  INF ArmPkg/Drivers/TimerDxe/TimerDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ChipInfoDxe/ChipInfoDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/IPCCDxe/IPCCDxe.inf
  #INF Binaries/songyuan/QcomPkg/Drivers/GLinkDxe/GLinkDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PlatformInfoDxe/PlatformInfoDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/DALSYSDxe/DALSYSDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/HALIOMMUDxe/HALIOMMUDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/HWIODxe/HWIODxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/I2CDxe/I2CDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/TLMMDxe/TLMMDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PmicDxe/PmicDxeLa.inf

  INF MdeModulePkg/Universal/DevicePathDxe/DevicePathDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/MdeModulePkg/Universal/Disk/DiskIoDxe/DiskIoDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/MdeModulePkg/Universal/Disk/PartitionDxe/PartitionDxe.inf

  INF FatPkg/EnhancedFatDxe/Fat.inf
  INF MdeModulePkg/Universal/Disk/UnicodeCollation/EnglishDxe/EnglishDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UFSDxe/UFSDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/CmdDbDxe/CmdDbDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PwrUtilsDxe/PwrUtilsDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/RpmhDxe/RpmhDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/NpaDxe/NpaDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ULogDxe/ULogDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/VcsDxe/VcsDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ClockDxe/ClockDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/SPMIDxe/SPMIDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ICBDxe/ICBDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/SmemDxe/SmemDxe.inf

  INF MdeModulePkg/Universal/BdsDxe/BdsDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/RscDxe/RscDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/OGaugeAuthDxe/OGaugeAuth.inf

  INF EmbeddedPkg/SimpleTextInOutSerial/SimpleTextInOutSerial.inf
  INF MdeModulePkg/Universal/PrintDxe/PrintDxe.inf
  INF MdeModulePkg/Universal/Console/ConPlatformDxe/ConPlatformDxe.inf
  INF MdeModulePkg/Universal/Console/ConSplitterDxe/ConSplitterDxe.inf
  INF MdeModulePkg/Universal/Console/GraphicsConsoleDxe/GraphicsConsoleDxe.inf

!if $(USE_CUSTOM_DISPLAY_DRIVER) == 1
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/CPRDxe/CPRDxe.inf
!endif
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/GpiDxe/GpiDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/SPIDxe/SPIDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/SdccDxe/SdccDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/FeatureEnablerDxe/FeatureEnablerDxe.inf

!if $(USE_CUSTOM_DISPLAY_DRIVER) == 1
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/DisplayDxe/DisplayDxe.inf
!else
  INF SiliciumPkg/Drivers/SimpleFbDxe/SimpleFbDxe.inf
!endif

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ButtonsDxe/ButtonsDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/ChargerExDxe/ChargerExDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/OcdtDxe/OplusProject.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PmicGlinkDxe/PmicGlinkDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/QcomChargerDxe/QcomChargerDxeLA.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbPwrCtrlDxe/UsbPwrCtrlDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/TsensDxe/TsensDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/LimitsDxe/LimitsDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/DDRInfoDxe/DDRInfoDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/XhciPciEmulationDxe/XhciPciEmulationDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/XhciDxe/XhciDxe.inf

  INF MdeModulePkg/Bus/Usb/UsbBusDxe/UsbBusDxe.inf
  INF MdeModulePkg/Bus/Usb/UsbKbDxe/UsbKbDxe.inf
  INF MdeModulePkg/Bus/Usb/UsbMassStorageDxe/UsbMassStorageDxe.inf

# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbMsdDxe/UsbMsdDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbDeviceDxe/UsbDeviceDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbConfigDxe/UsbConfigDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UsbInitDxe/UsbInitDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/UCDxe/UCDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/RNGDxe/RngDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PILDxe/PILDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/PILProxyDxe/PILProxyDxe.inf
# minimal-driver + payload experiment: INF Binaries/songyuan/QcomPkg/Drivers/SPSSDxe/SPSSDxe.inf
