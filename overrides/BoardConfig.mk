# overrides/BoardConfig.mk  (SM-T583 / gtaxladwifikx / exynos7870)
# Appended to the END of the generated BoardConfig.mk by the workflow.
# Last assignment wins. DEVICE_PATH is defined by the workflow just above this block.
#
# The SoC can run AArch64, but this firmware does not:
#   ro.zygote=zygote32
#   ro.product.cpu.abi=armeabi-v7a
#   ro.product.cpu.abilist64=        (empty)
# Recovery userspace (init, adbd, recovery) is ELF32 EM_ARM.
# Android 11 rejects armv7-a-neon together with cortex-a53, so the 32-bit
# variant is armv8-a.

TARGET_BOARD_PLATFORM := exynos5
TARGET_SOC := exynos7870
TARGET_BOOTLOADER_BOARD_NAME := universal7870
TARGET_NO_BOOTLOADER := true
TARGET_NO_RADIOIMAGE := true

TARGET_ARCH := arm
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := armeabi-v7a
TARGET_CPU_ABI2 := armeabi
TARGET_CPU_VARIANT := cortex-a53
TARGET_2ND_ARCH :=
TARGET_2ND_ARCH_VARIANT :=
TARGET_2ND_CPU_ABI :=
TARGET_2ND_CPU_ABI2 :=
TARGET_2ND_CPU_VARIANT :=
# 3.18 binder on this platform is the 64-bit ABI even for a 32-bit zygote.
TARGET_USES_64_BIT_BINDER := true
TARGET_KERNEL_ARCH := arm

BOARD_VENDOR := samsung
BOARD_KERNEL_CMDLINE := androidboot.selinux=permissive
BOARD_KERNEL_BASE := 0x10000000
BOARD_KERNEL_PAGESIZE := 2048
BOARD_KERNEL_OFFSET := 0x00008000
BOARD_RAMDISK_OFFSET := 0x01000000
BOARD_TAGS_OFFSET := 0x00000100
# 32-bit product makefile looks up zImage. The workflow stores the stock
# kernel bytes at that path (this firmware's blob is what the bootloader
# already accepts; the Odin step copies those same bytes back in).
BOARD_KERNEL_IMAGE_NAME := zImage
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/zImage
# Stock recovery ramdisk is gzip. twrp-11 emits LZ4, and this Oreo
# bootloader stops with "invalid ramdisk" on anything else.
BOARD_RAMDISK_USE_LZ4 := false

# twrpdtgen may emit DTB / DTBO / header-v1+ settings that point to files we
# do not ship. Stock DTBH is re-attached in the Odin packaging step.
BOARD_INCLUDE_DTB_IN_BOOTIMG :=
BOARD_PREBUILT_DTBIMAGE_DIR :=
BOARD_PREBUILT_DTBOIMAGE :=
BOARD_INCLUDE_RECOVERY_DTBO :=
BOARD_BOOT_HEADER_VERSION := 0
BOARD_MKBOOTIMG_ARGS := --kernel_offset $(BOARD_KERNEL_OFFSET) --ramdisk_offset $(BOARD_RAMDISK_OFFSET) --tags_offset $(BOARD_TAGS_OFFSET) --board SRPRA23C001RU

BOARD_FLASH_BLOCK_SIZE := 131072
BOARD_BOOTIMAGE_PARTITION_SIZE := 33554432
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 39845888
BOARD_HAS_LARGE_FILESYSTEM := true
TARGET_USERIMAGES_USE_EXT4 := true
BOARD_SUPPRESS_SECURE_ERASE := true

TARGET_SCREEN_WIDTH := 1200
TARGET_SCREEN_HEIGHT := 1920
TARGET_RECOVERY_PIXEL_FORMAT := "ABGR_8888"
RECOVERY_GRAPHICS_USE_LINELENGTH := true
RECOVERY_SDCARD_ON_DATA := true

TW_THEME := portrait_hdpi
TW_BRIGHTNESS_PATH := "/sys/class/backlight/panel/brightness"
TW_MAX_BRIGHTNESS := 306
TW_DEFAULT_BRIGHTNESS := 162
# Leave blanking enabled. true keeps the panel lit for the whole session.
TW_NO_SCREEN_BLANK :=
TW_NO_REBOOT_BOOTLOADER := true
TW_HAS_DOWNLOAD_MODE := true
TW_EXCLUDE_DEFAULT_USB_INIT := true
TW_EXCLUDE_VENDOR_BOOT_SERVICE := true
