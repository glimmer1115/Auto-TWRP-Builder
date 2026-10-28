#
# Copyright (C) 2026 The Android Open Source Project
# Copyright (C) 2026 SebaUbuntu's TWRP device tree generator
#
# SPDX-License-Identifier: Apache-2.0
#

# Inherit from those products. Most specific first.
$(call inherit-product, $(SRC_TARGET_DIR)/product/full_base_telephony.mk)

# Inherit some common Omni stuff.
$(call inherit-product, vendor/omni/config/common.mk)

# Inherit from gtaxladwifikx device
$(call inherit-product, device/samsung/gtaxladwifikx/device.mk)

PRODUCT_DEVICE := gtaxladwifikx
PRODUCT_NAME := omni_gtaxladwifikx
PRODUCT_BRAND := samsung
PRODUCT_MODEL := SM-T583
PRODUCT_MANUFACTURER := samsung

PRODUCT_GMS_CLIENTID_BASE := android-samsung

PRODUCT_BUILD_PROP_OVERRIDES += \
    PRIVATE_BUILD_DESC="gtaxladwifikx-user 8.0.0 R16NW T583KOU1BTG1 release-keys"

BUILD_FINGERPRINT := samsung/gtaxladwifikx/gtaxladwifikx:8.0.0/R16NW/T583KOU1BTG1:user/release-keys

# Recovery ADB must be root-capable and unauthenticated on this Oreo 8.1 build.
PRODUCT_DEFAULT_PROPERTY_OVERRIDES += \
    ro.adb.secure=0 \
    ro.debuggable=1 \
    ro.secure=0 \
    persist.sys.usb.config=adb
TARGET_RECOVERY_PIXEL_FORMAT := "ABGR_8888"
TW_BRIGHTNESS_PATH := "/sys/devices/14800000.dsim/backlight/panel/brightness"
TW_MAX_BRIGHTNESS := 150
TW_DEFAULT_BRIGHTNESS := 120
TW_NO_SCREEN_BLANK := true
TW_NO_SCREEN_TIMEOUT := true
RECOVERY_GRAPHICS_FORCE_USE_LINELENGTH := true
RECOVERY_GRAPHICS_FORCE_SINGLE_BUFFER := true
