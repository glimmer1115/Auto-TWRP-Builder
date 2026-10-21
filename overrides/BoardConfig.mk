# overrides/BoardConfig.mk  (SM-T583 / gtaxladwifikx / exynos7870)
# Appended to the END of the generated BoardConfig.mk by the workflow.
# Last assignment wins. DEVICE_PATH is defined by the workflow just above this block.
#
# Stock recovery init, adbd, and recovery are static ELF32 (EM_ARM, no linker).
# A 64-bit ramdisk on this kernel resets every 5 seconds, so adb never enumerates.
# The boot kernel stays the stock ARM64 Image. Do not use the SM-T580 kernel
# or board id FPRPGTAX000RU.

TARGET_BOARD_PLATFORM := exynos5
TARGET_SOC := exynos7870
TARGET_BOOTLOADER_BOARD_NAME := universal7870
TARGET_NO_BOOTLOADER := true
TARGET_NO_RADIOIMAGE := true

TARGET_ARCH := arm
TARGET_ARCH_VARIANT := armv7-a-neon
TARGET_CPU_ABI := armeabi-v7a
TARGET_CPU_ABI2 := armeabi
TARGET_CPU_VARIANT := cortex-a53
TARGET_CPU_SMP := true
TARGET_2ND_ARCH :=
TARGET_2ND_ARCH_VARIANT :=
TARGET_2ND_CPU_ABI :=
TARGET_2ND_CPU_ABI2 :=
TARGET_2ND_CPU_VARIANT :=
TARGET_USES_64_BIT_BINDER := true
TARGET_KERNEL_ARCH := arm64
TARGET_KERNEL_HEADER_ARCH := arm64
TARGET_USES_UNCOMPRESSED_KERNEL := true

BOARD_VENDOR := samsung
BOARD_KERNEL_CMDLINE := androidboot.selinux=permissive
BOARD_KERNEL_BASE := 0x10000000
BOARD_KERNEL_PAGESIZE := 2048
BOARD_KERNEL_OFFSET := 0x00008000
BOARD_RAMDISK_OFFSET := 0x01000000
BOARD_TAGS_OFFSET := 0x00000100
# The makefile looks up Image. The workflow stores the stock kernel bytes
# there. The Odin step copies those same bytes back in.
BOARD_KERNEL_IMAGE_NAME := Image
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/Image
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
# FBIOGET on this panel: 32bpp, 1200x1920, red.offset 16, green.offset 8,
# blue.offset 0, line_length 4800, smem_len 18432000, yres_virtual 1920.
# smem_len is exactly two screens, so minui turns on double buffering and
# FBIOPUT_VSCREENINFO asks for yres_virtual 3840 unless single-buffer is
# compiled in. The 8.1 minui makefile never passed TW_NO_SCREEN_BLANK to
# the compiler, so fbdev_blank still issued FBIOBLANK. That powers this
# dsim panel down (the brightness-0 write before signal 11). The workflow
# adds only -DTW_NO_SCREEN_BLANK to libminuitwrp. Do not also pass the
# brightness macros there: with all three set, fbdev_blank writes "000".
TARGET_RECOVERY_PIXEL_FORMAT := "ABGR_8888"
RECOVERY_GRAPHICS_USE_LINELENGTH := true
RECOVERY_GRAPHICS_FORCE_USE_LINELENGTH := true
RECOVERY_GRAPHICS_FORCE_SINGLE_BUFFER := true
BOARD_SEPOLICY_DIRS += $(DEVICE_PATH)/sepolicy
RECOVERY_SDCARD_ON_DATA := true

TW_THEME := portrait_hdpi
# SM-T580 official TWRP uses the dsim device node, not /sys/class/backlight/panel.
# Stock max_brightness on this panel is 306. 255 never reaches full brightness.
TW_BRIGHTNESS_PATH := "/sys/devices/14800000.dsim/backlight/panel/brightness"
TW_MAX_BRIGHTNESS := 306
TW_DEFAULT_BRIGHTNESS := 162
# The recovery binary sees this flag. libminuitwrp sees it only after the
# workflow patches minui, and that patch must not add the brightness macros.
# With all three macros set, fbdev_blank writes 000 and this panel goes dark.
TW_NO_SCREEN_BLANK := true
TW_NO_REBOOT_BOOTLOADER := true
TW_HAS_DOWNLOAD_MODE := true
TW_EXCLUDE_DEFAULT_USB_INIT := true
TARGET_RECOVERY_INITRC :=
TW_EXCLUDE_VENDOR_BOOT_SERVICE := true
# Do not set TARGET_GLOBAL_* or HOST_GLOBAL_* here. Omni 8.1 ckati compares
# those lists with Soong and stops with "Soong variable check failed."
