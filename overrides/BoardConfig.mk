# overrides/BoardConfig.mk  (SM-T583 / exynos7870)
# Appended to the END of the generated BoardConfig.mk by the workflow -> last assignment wins.
# DEVICE_PATH is defined by the workflow right before this block.

TARGET_BOARD_PLATFORM := exynos5
TARGET_SOC := exynos7870
TARGET_BOOTLOADER_BOARD_NAME := universal7870
TARGET_NO_BOOTLOADER := true
TARGET_NO_RADIOIMAGE := true

TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_ABI2 :=
TARGET_CPU_VARIANT := cortex-a53
TARGET_2ND_ARCH := arm
# Android 11 rejects armv7-a-neon together with a cortex-a53 CPU variant (TARGET_linux-arm.mk:53)
TARGET_2ND_ARCH_VARIANT := armv8-a
TARGET_2ND_CPU_ABI := armeabi-v7a
TARGET_2ND_CPU_ABI2 := armeabi
TARGET_2ND_CPU_VARIANT := cortex-a53
TARGET_USES_64_BIT_BINDER := true
TARGET_KERNEL_ARCH := arm64

BOARD_VENDOR := samsung
BOARD_KERNEL_CMDLINE :=
BOARD_KERNEL_BASE := 0x10000000
BOARD_KERNEL_PAGESIZE := 2048
BOARD_KERNEL_OFFSET := 0x00008000
BOARD_RAMDISK_OFFSET := 0x01000000
BOARD_TAGS_OFFSET := 0x00000100
BOARD_KERNEL_IMAGE_NAME := Image
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/Image

# twrpdtgen may emit DTB / DTBO / header-v1+ settings that point to files we do not ship
# (prebuilt/dtb.img, dtbo.img ...) -> mkbootimg then fails with "No such file" (make Error 1/2).
# The stock DT is re-attached in the "Package for Odin" step, so clear them all here.
BOARD_INCLUDE_DTB_IN_BOOTIMG :=
BOARD_PREBUILT_DTBIMAGE_DIR :=
BOARD_PREBUILT_DTBOIMAGE :=
BOARD_INCLUDE_RECOVERY_DTBO :=
BOARD_BOOT_HEADER_VERSION := 0
BOARD_MKBOOTIMG_ARGS := --kernel_offset $(BOARD_KERNEL_OFFSET) --ramdisk_offset $(BOARD_RAMDISK_OFFSET) --tags_offset $(BOARD_TAGS_OFFSET)

BOARD_FLASH_BLOCK_SIZE := 131072
BOARD_BOOTIMAGE_PARTITION_SIZE := 33554432
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 39845888
BOARD_HAS_LARGE_FILESYSTEM := true
TARGET_USERIMAGES_USE_EXT4 := true
BOARD_SUPPRESS_SECURE_ERASE := true

TARGET_SCREEN_WIDTH := 1200
TARGET_SCREEN_HEIGHT := 1920
# If colours look wrong on screen try "RGBX_8888" or "BGRA_8888"
TARGET_RECOVERY_PIXEL_FORMAT := "ABGR_8888"
RECOVERY_GRAPHICS_USE_LINELENGTH := true
RECOVERY_SDCARD_ON_DATA := true

TW_THEME := portrait_hdpi
TW_BRIGHTNESS_PATH := "/sys/class/backlight/panel/brightness"
TW_MAX_BRIGHTNESS := 306
TW_DEFAULT_BRIGHTNESS := 162
TW_NO_SCREEN_BLANK := true
TW_NO_REBOOT_BOOTLOADER := true
TW_HAS_DOWNLOAD_MODE := true
TW_EXCLUDE_DEFAULT_USB_INIT := true
TW_EXCLUDE_VENDOR_BOOT_SERVICE := true
