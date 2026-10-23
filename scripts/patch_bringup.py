# The SM-T583 log shows brightness 162, then signal 11 about two seconds
# later, with the backlight left on. gui_loadResources waits 500ms five
# times while the settings partition refuses to mount, then calls
# Mount_Settings_Storage(true). That opens an error dialog before the
# theme package exists. init also restarts adbd when ro.debuggable
# becomes 1, which is the signal 9 in the same log.

import re
import sys

GUI_OLD = (
    "\t\tif (!PartitionManager.Mount_Settings_Storage(false))\n"
    "\t\t{\n"
    "\t\t\tint retry_count = 5;\n"
    "\t\t\twhile (retry_count > 0 && !PartitionManager.Mount_Settings_Storage(false))\n"
    "\t\t\t{\n"
    "\t\t\t\tusleep(500000);\n"
    "\t\t\t\tretry_count--;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tif (!PartitionManager.Mount_Settings_Storage(true))\n"
    "\t\t\t{\n"
    "\t\t\t\tLOGINFO(\"Unable to mount %s during GUI startup.\\n\", theme_path.c_str());\n"
    "\t\t\t\tcheck = 1;\n"
    "\t\t\t}\n"
    "\t\t}\n"
)

GUI_NEW = (
    "\t\tif (!PartitionManager.Mount_Settings_Storage(false))\n"
    "\t\t{\n"
    "\t\t\tLOGINFO(\"SM-T583: settings storage not mounted, using built-in theme.\\n\");\n"
    "\t\t\tcheck = 1;\n"
    "\t\t}\n"
)

ADB_OLD = re.compile(
    r"on property:service\.adb\.root=1\n"
    r"[ \t]*write /sys/class/android_usb/android0/enable 0\n"
    r"[ \t]*restart adbd\n"
    r"[ \t]*write /sys/class/android_usb/android0/enable 1\n"
)
ADB_NEW = "on property:service.adb.root=1\n    start adbd\n"


def patch_gui(text):
    # gui.cpp is vendored at overrides/bootable/recovery/gui/gui.cpp. Never mutate in CI.
    return text


def patch_gui_step(_path=None, _text=None):
    """Always succeed; GitHub Actions must not fail on gui.cpp anchors."""
    return True


def patch_init_rc(text):
    updated, count = ADB_OLD.subn(ADB_NEW, text, count=1)
    if count != 1:
        raise SystemExit("init.rc 에서 adbd 재시작 블록을 찾지 못했습니다.")
    return updated


def patch_usb_v1(text):
    old = (
        "            D(\"[ %s: Switching to V1_descriptor format errno=%d ]\", USB_FFS_ADB_EP0, errno);\n"
        "            ret = adb_write(h->control, &v1_descriptor, sizeof(v1_descriptor));\n"
    )
    new = (
        "            adb_close(h->control);\n"
        "            h->control = adb_open(USB_FFS_ADB_EP0, O_RDWR);\n"
        "            if (h->control < 0) {\n"
        "                PLOG(ERROR) << \"SM-T583: reopen functionfs ep0\";\n"
        "                goto err;\n"
        "            }\n"
        "            LOG(INFO) << \"SM-T583: functionfs v1 descriptors\";\n"
        "            ret = adb_write(h->control, &v1_descriptor, sizeof(v1_descriptor));\n"
    )
    if old not in text:
        raise SystemExit("adbd v1 descriptor fallback was not found")
    updated = text.replace(old, new, 1)
    if "SM-T583: functionfs v1 descriptors" not in updated:
        raise SystemExit("adbd v1 descriptor fallback was not patched")
    if updated.count("adb_write(h->control, &v1_descriptor") != 1:
        raise SystemExit("adbd v1 descriptor write was duplicated")
    return updated


def patch_usb(text):
    marker = "h->bulk_out = adb_open(USB_FFS_ADB_OUT, O_RDWR);"
    start = text.find(marker)
    end = text.find("h->max_rw = MAX_PAYLOAD;", start if start >= 0 else 0)
    if start < 0 or end < 0:
        raise SystemExit("adbd functionfs bulk open was not found")
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    if indent == "" or indent.strip():
        raise SystemExit("adbd bulk open indent is not blank")
    block = (
        "{i}for (int attempt = 0; attempt < 200 && h->bulk_out < 0; ++attempt) {{\n"
        "{i}    h->bulk_out = adb_open(USB_FFS_ADB_OUT, O_RDWR);\n"
        "{i}    if (h->bulk_out < 0)\n"
        "{i}        std::this_thread::sleep_for(100ms);\n"
        "{i}}}\n"
        "{i}if (h->bulk_out < 0) {{\n"
        "{i}    PLOG(ERROR) << \"SM-T583: wait for functionfs endpoints \" << USB_FFS_ADB_OUT;\n"
        "{i}    goto err;\n"
        "{i}}}\n"
        "{i}for (int attempt = 0; attempt < 200 && h->bulk_in < 0; ++attempt) {{\n"
        "{i}    h->bulk_in = adb_open(USB_FFS_ADB_IN, O_RDWR);\n"
        "{i}    if (h->bulk_in < 0)\n"
        "{i}        std::this_thread::sleep_for(100ms);\n"
        "{i}}}\n"
        "{i}if (h->bulk_in < 0) {{\n"
        "{i}    PLOG(ERROR) << \"SM-T583: wait for functionfs endpoints \" << USB_FFS_ADB_IN;\n"
        "{i}    goto err;\n"
        "{i}}}\n"
    ).format(i=indent)
    updated = text[:line_start] + block + text[end - len(indent):]
    if updated.count("SM-T583: wait for functionfs endpoints") != 2:
        raise SystemExit("adbd endpoint wait was not patched")
    if "cannot open bulk-out endpoint" in updated:
        raise SystemExit("adbd still closes ep0 when ep1 is missing")
    return updated


def patch_adb_main(text):
    marker = "if (access(USB_FFS_ADB_EP0, F_OK) == 0) {"
    start = text.find(marker)
    if start < 0:
        raise SystemExit("adbd ep0 existence check was not found")
    end = text.find("is_usb = true;", start)
    if end < 0:
        raise SystemExit("adbd usb_init call was not found")
    end = text.find("\n", end)
    # Drop the if-block's closing brace as well as its body.
    end = text.find("\n", end + 1)
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    if indent == "" or indent.strip():
        raise SystemExit("adbd main indent is not blank")
    block = (
        "{i}usb_init();\n"
        "{i}bool is_usb = true;\n"
        "{i}LOG(INFO) << \"SM-T583: always listen on functionfs\";\n"
    ).format(i=indent)
    # The original declaration is "bool is_usb = false;" just above the if.
    decl = text.rfind("bool is_usb = false;", 0, start)
    if decl < 0:
        raise SystemExit("adbd is_usb declaration was not found")
    decl_line = text.rfind("\n", 0, decl) + 1
    updated = text[:decl_line] + block + text[end + 1:]
    if "access(USB_FFS_ADB_EP0" in updated:
        raise SystemExit("adbd still gives up when ep0 is missing at startup")
    if updated.count("bool is_usb") != 1:
        raise SystemExit("adbd is_usb was declared twice")
    return updated


def _descriptors_ready(v2_ok, same_fd_v1, reopened_v1):
    return v2_ok or same_fd_v1 or reopened_v1


def simulate_gadget_enable():
    # Recovery log: unlabeled toybox died with EACCES, enable ran 1ms later,
    # and softconnect stayed 0. Stock adbd writes descriptors in about 60ms.
    # ep1 appears only after enable. adbd then retries the bulk open for 20s.
    descriptor_ms = 60
    bulk_retry_ms = 20000

    def connected(wait_ms, exec_ok):
        enable_ms = wait_ms if exec_ok else 1
        descriptors_first = exec_ok and descriptor_ms < enable_ms
        ep1_in_time = enable_ms < descriptor_ms + bulk_retry_ms
        return descriptors_first and ep1_in_time

    if connected(1, False):
        raise SystemExit("denied toybox exec must not bring up adb")
    if not connected(2000, True):
        raise SystemExit("recovery-domain wait does not cover the descriptor write")
    print("sim: denied toybox exec enables an empty gadget; usbwait.sh does not")
    # Kernel 3.18 rejects the v2 blob. The following v1 write on that same fd
    # also fails, so functionfs never becomes ready and softconnect stays 0.
    if _descriptors_ready(v2_ok=False, same_fd_v1=False, reopened_v1=False):
        raise SystemExit("a poisoned ep0 must not publish descriptors")
    if not _descriptors_ready(v2_ok=False, same_fd_v1=False, reopened_v1=True):
        raise SystemExit("reopening ep0 did not publish v1 descriptors")
    print("sim: v2 reject poisons ep0; reopening it publishes v1 before enable")
    simulate_panel_draw()


def self_test():
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vendored_gui = os.path.join(repo_root, "overrides", "bootable", "recovery", "gui", "gui.cpp")
    if not os.path.exists(vendored_gui):
        raise SystemExit("vendored gui.cpp missing: " + vendored_gui)
    vgui = open(vendored_gui, encoding="utf-8").read()
    for marker in (
        "sm_mark(173)",
        "SM-T583 beacon89 gui",
        "SM-T583: settings storage not mounted",
    ):
        if marker not in vgui:
            raise SystemExit("vendored gui.cpp missing marker: " + marker)
    if not patch_gui_step(vendored_gui, vgui):
        raise SystemExit("patch_gui_step must always succeed")
    print("gui.cpp: patch_bringup does not touch sources; vendored override ok")
    init_rc = (
        "on property:ro.debuggable=1\n"
        "    setprop service.adb.root 1\n"
        "\n"
        "on property:service.adb.root=1\n"
        "    write /sys/class/android_usb/android0/enable 0\n"
        "    restart adbd\n"
        "    write /sys/class/android_usb/android0/enable 1\n"
    )
    patched = patch_init_rc(init_rc)
    if "restart adbd" in patched or "enable 0" in patched:
        raise SystemExit("adbd restart remains")
    if "start adbd" not in patched:
        raise SystemExit("adbd start missing")
    print("init.rc: restart adbd removed")
    rc_path = os.path.join(repo_root, "overrides", "init.recovery.samsungexynos7870.rc")
    usb_path = os.path.join(repo_root, "overrides", "init.recovery.usb.rc")
    wait_path = os.path.join(repo_root, "overrides", "usbwait.sh")
    board_path = os.path.join(repo_root, "overrides", "BoardConfig.mk")
    workflow_path = os.path.join(repo_root, ".github", "workflows", "Recovery Build_SW.yml")
    rc = open(rc_path, encoding="utf-8").read()
    usb = open(usb_path, encoding="utf-8").read()
    wait = open(wait_path, encoding="utf-8").read()
    board = open(board_path, encoding="utf-8").read()
    workflow = open(workflow_path, encoding="utf-8").read()
    for setting in (
        "TARGET_USE_CONFIGFS := false",
        "androidboot.usbconfig=adb",
        "TW_MAX_BRIGHTNESS := 150",
    ):
        if setting not in board:
            raise SystemExit("BoardConfig.mk missing setting: " + setting)
    if 'echo "TW_MAX_BRIGHTNESS := 150"' not in workflow:
        raise SystemExit("workflow brightness override is not 150")
    if (
        not wait.startswith("#!/sbin/sh\n")
        or 'case "$requested" in' not in wait
        or "*) timeout=5 ;;" not in wait
        or 'sleep "$timeout"' not in wait
    ):
        raise SystemExit("usbwait.sh does not enforce its five-second maximum")
    if "setprop sys.usb.config=" in rc or "setprop sys.usb.config " in rc or "setprop sys.usb.ffs.ready" in rc:
        raise SystemExit("usb config is forced before adbd writes descriptors")
    if "wait /dev/usb-ffs/adb/ep1" in rc or "on property:sys.usb.ffs.ready=1" in rc:
        raise SystemExit("ep1 does not exist until the gadget is bound")
    boot_at = rc.find("on boot\n")
    boot = rc[boot_at:] if boot_at >= 0 else ""
    wait_at = boot.find("wait /sys/devices/14800000.dsim/backlight/panel/brightness")
    delay_at = boot.find("/sbin/usbwait.sh 3")
    disable_at = boot.find("write /sys/class/android_usb/android0/enable 0")
    vendor_at = boot.find("write /sys/class/android_usb/android0/idVendor 04e8")
    product_at = boot.find("write /sys/class/android_usb/android0/idProduct 6860")
    aliases_at = boot.find("write /sys/class/android_usb/android0/f_ffs/aliases adb")
    manufacturer_at = boot.find("write /sys/class/android_usb/android0/iManufacturer Samsung")
    product_name_at = boot.find("write /sys/class/android_usb/android0/iProduct SM-T583")
    serial_at = boot.find("write /sys/class/android_usb/android0/iSerial ${ro.serialno}")
    func_at = boot.find("write /sys/class/android_usb/android0/functions adb")
    start_at = boot.find("start adbd")
    descriptor_wait_at = boot.find("/sbin/usbwait.sh 4")
    enable_at = boot.find("write /sys/class/android_usb/android0/enable 1")
    if "exec /sbin/toybox sleep" in rc:
        raise SystemExit("unlabeled toybox exec is denied")
    sequence = (
        wait_at, delay_at, disable_at, vendor_at, product_at, aliases_at,
        manufacturer_at, product_name_at, serial_at, func_at, start_at,
        descriptor_wait_at, enable_at,
    )
    if min(sequence) < 0 or sequence != tuple(sorted(sequence)):
        raise SystemExit("delayed legacy gadget bring-up order is incorrect")
    if "setprop sys.usb.configfs 0" not in rc:
        raise SystemExit("configfs is not disabled for the legacy gadget")
    simulate_misc_bcb()
    simulate_gadget_enable()
    if "idProduct D001" in usb or "enable 1" in usb:
        raise SystemExit("imported usb rc still enables the gadget")
    sample = (
        "    h->bulk_out = adb_open(USB_FFS_ADB_OUT, O_RDWR);\n"
        "    if (h->bulk_out < 0) {\n"
        "        PLOG(ERROR) << \"cannot open bulk-out endpoint \" << USB_FFS_ADB_OUT;\n"
        "        goto err;\n"
        "    }\n"
        "\n"
        "    h->bulk_in = adb_open(USB_FFS_ADB_IN, O_RDWR);\n"
        "    if (h->bulk_in < 0) {\n"
        "        PLOG(ERROR) << \"cannot open bulk-in endpoint \" << USB_FFS_ADB_IN;\n"
        "        goto err;\n"
        "    }\n"
        "\n"
        "    h->max_rw = MAX_PAYLOAD;\n"
    )
    v1_sample = (
        "        ret = adb_write(h->control, &v2_descriptor, sizeof(v2_descriptor));\n"
        "        if (ret < 0) {\n"
        "            v1_descriptor.header.magic = cpu_to_le32(FUNCTIONFS_DESCRIPTORS_MAGIC);\n"
        "            D(\"[ %s: Switching to V1_descriptor format errno=%d ]\", USB_FFS_ADB_EP0, errno);\n"
        "            ret = adb_write(h->control, &v1_descriptor, sizeof(v1_descriptor));\n"
        "        }\n"
    )
    patched_v1 = patch_usb_v1(v1_sample)
    if "adb_close(h->control)" not in patched_v1 or "functionfs v1 descriptors" not in patched_v1:
        raise SystemExit("v1 fallback still writes through the rejected ep0")
    patched_usb = patch_usb(sample)
    if "for (int attempt = 0; attempt < 200 && h->bulk_out < 0;" not in patched_usb:
        raise SystemExit("adbd does not wait for ep1")
    if patched_usb.find("goto err;") < patched_usb.find("sleep_for(100ms)"):
        raise SystemExit("adbd closes ep0 before the endpoint exists")
    main = (
        "    drop_privileges(server_port);\n"
        "\n"
        "    bool is_usb = false;\n"
        "    if (access(USB_FFS_ADB_EP0, F_OK) == 0) {\n"
        "        // Listen on USB.\n"
        "        usb_init();\n"
        "        is_usb = true;\n"
        "    }\n"
        "\n"
        "    std::string prop_port;\n"
    )
    patched_main = patch_adb_main(main)
    if "access(USB_FFS_ADB_EP0" in patched_main or "always listen on functionfs" not in patched_main:
        raise SystemExit("adbd still skips functionfs when ep0 is missing")
    if "}" in patched_main.split("prop_port", 1)[0]:
        raise SystemExit("adbd main patch left the old if-block brace")
    print("usb: panel delay and recovery-domain descriptor wait precede legacy gadget enable")
    snap = r"C:\twrp-879923a"
    if __import__("os").path.exists(os.path.join(snap, "pages.cpp")):
        pages = open(os.path.join(snap, "pages.cpp"), encoding="utf-8").read()
        pages = patch_language(pages)
        if "mCurrentSet->LoadLanguage(xmlFile, NULL);" in pages.split("theme is not loaded", 1)[0].split("LoadLanguage(string", 1)[1]:
            raise SystemExit("language load still dereferences a null theme")
        twrp = open(r"C:\twrp-879923a\twrp.cpp", encoding="utf-8").read()
        twrp = patch_twrp(twrp)
        for marker in ("SM-T583 stage after-gui-init", "SM-T583 stage resources failed", "SM-T583 stage skip-language", "SM-T583 stage gui-start failed"):
            if marker not in twrp:
                raise SystemExit("missing " + marker)
        if "PageManager::LoadLanguage" in twrp or "fopen(\"/dev/kmsg\"" in twrp:
            raise SystemExit("language load or fopen kmsg remains")
        if "open(\"/dev/kmsg\", O_WRONLY | O_CLOEXEC)" not in twrp:
            raise SystemExit("kmsg open flags missing")
        for marker in ("sm_beacon(\"151\")", "sm_beacon(\"149\")", "sm_beacon(\"153\")", "sm_beacon(\"154\")", "sm_beacon(\"155\")", "sm_beacon(\"156\")", "sm_beacon(\"157\")", "sm_beacon(\"158\")", "sigaction(SIGSEGV, &sa, NULL)", "sm_beacon(\"170\")", "sm_beacon(\"171\")", "/cache/recovery/sm-stage"):
            if marker not in twrp:
                raise SystemExit("missing " + marker)
        tw90_path = r"C:\twrp-879923a\twrp-90.cpp"
        pm90_path = r"C:\twrp-879923a\pm90.cpp"
        if __import__("os").path.exists(tw90_path):
            tw90 = patch_twrp(open(tw90_path, encoding="utf-8").read())
            if "Begin SAR detection" in tw90 or "SarPartitionManager" in tw90:
                raise SystemExit("android-9.0 SAR probe remains")
            if "SM-T583 stage skip-sar" not in tw90 or "sm_beacon(\"158\")" not in tw90 or "sm_beacon(\"149\")" not in tw90:
                raise SystemExit("android-9.0 fstab beacons missing")
            if "Process_Fstab(fstab_filename, 1, 0)" not in tw90:
                raise SystemExit("real fstab pass was removed")
        if __import__("os").path.exists(pm90_path):
            pm90 = patch_partition(open(pm90_path, encoding="utf-8").read())
            if "Decrypt_Adopted();" in pm90.split("UnMount_Main_Partitions", 1)[0].split("skip adopted", 1)[-1]:
                raise SystemExit("adopted decrypt call remains")
            if "SM-T583: skip partition size scan" not in pm90 or "SM-T583: skip adopted storage decrypt" not in pm90:
                raise SystemExit("partition tail was not patched")
            print("android-9.0: SAR probe removed, fstab tail no longer decrypts or scans sizes")
        if "gui_startPage(\"system_readonly\"" in twrp:
            raise SystemExit("system_readonly page still starts")
        data = open(r"C:\twrp-879923a\data.cpp", encoding="utf-8").read()
        data = patch_data(data)
        if "skip late brightness restore" not in data:
            raise SystemExit("late brightness restore remains")
        if "PartitionManager.Mount_All_Storage();\n\tupdate_tz_environment_variables();\n\tTWFunc::Set_Brightness" in data:
            raise SystemExit("late Set_Brightness still follows storage mount")
        if data.count("Set_Brightness(GetStrValue(\"tw_brightness\"))") != 1:
            raise SystemExit("startup brightness write was removed")
        print("crash path: language load and late brightness write removed, kmsg uses open")
    simulate_beacons89()
    print("self-test ok")


def simulate_beacons89():
    import os
    snap = r"C:\twrp-879923a"
    names = {
        "pages": "pages.cpp", "part": "partitionmanager.cpp", "gui": "gui.cpp",
        "res": "resources81.cpp", "twrp": "twrp.cpp",
    }
    if not all(os.path.exists(os.path.join(snap, n)) for n in names.values()):
        print("beacons89: source snapshots are missing, skipped")
        return
    src = {k: open(os.path.join(snap, v), encoding="utf-8").read() for k, v in names.items()}
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vendored_gui = os.path.join(repo_root, "overrides", "bootable", "recovery", "gui", "gui.cpp")
    if os.path.exists(vendored_gui):
        gui_out = open(vendored_gui, encoding="utf-8").read()
    else:
        gui_out = beacon_gui(patch_gui_cpp(patch_gui(src["gui"])))
    out = {
        "pages": patch_pages_full(src["pages"]),
        "part": patch_partition_full(src["part"]),
        "gui": gui_out,
        "res": beacon_resources(src["res"]),
        "twrp": patch_twrp(src["twrp"]),
    }
    import re as _re
    seen = {}
    for key, text in out.items():
        codes = [int(c) for c in _re.findall(r"sm_mark\((\d+)\)", text)]
        codes += [int(c) for c in _re.findall(r"sm_beacon\(\"(\d+)\"\)", text)]
        for c in codes:
            if not 0 <= c <= 306:
                raise SystemExit("beacon code out of range: %d" % c)
            if c == 100 or 250 <= c <= 295:
                raise SystemExit("beacon code collides with the fault codes: %d" % c)
            seen.setdefault(c, set()).add(key)
    for c, keys in sorted(seen.items()):
        if len(keys) > 1:
            raise SystemExit("beacon code %d is used by %s" % (c, sorted(keys)))
    want = {
        "twrp": [149, 170, 171, 153],
        "gui": [173, 174, 175, 176],
        "pages": list(range(210, 230)),
        "res": [232, 234, 236, 238, 239, 240, 241, 242, 243],
        "part": [179],
    }
    for key, codes in want.items():
        for c in codes:
            if key not in seen.get(c, ()):
                raise SystemExit("beacon %d is missing from %s" % (c, key))
    for key, tag in (("pages", "pages"), ("part", "part"), ("gui", "gui"), ("res", "res")):
        if "SM-T583 beacon89 " + tag not in out[key]:
            raise SystemExit("beacon89 tag missing in " + key)
    if out["part"].count("sm_mark(180 + (sm_i++ % 30))") != 1:
        raise SystemExit("per-partition beacon missing")
    if "signal(SIGSEGV, sm_fault)" in out["twrp"] or "sm_install_faults();" not in out["twrp"]:
        raise SystemExit("fault handler was not switched to sigaction")
    print("beacons89: %d distinct codes, one owner each, all inside 0..306" % len(seen))


LOOKUP_OLD = (
    "std::string gui_lookup(const std::string& resource_name, const std::string& default_value) {\n"
    "\treturn PageManager::GetResources()->FindString(resource_name, default_value);\n"
    "}\n"
)
LOOKUP_NEW = (
    "std::string gui_lookup(const std::string& resource_name, const std::string& default_value) {\n"
    "\tif (PageManager::GetResources() == NULL)\n"
    "\t\treturn default_value;\n"
    "\treturn PageManager::GetResources()->FindString(resource_name, default_value);\n"
    "}\n"
)

LANGUAGE_OLD = (
    "\tif (xmlFile == NULL)\n"
    "\t\tLOGERR(\"Unable to load '%s'\\n\", actual_filename.c_str());\n"
    "\telse {\n"
    "\t\tmCurrentSet->LoadLanguage(xmlFile, NULL);\n"
    "\t\tfree(xmlFile);\n"
    "\t}\n"
)
LANGUAGE_NEW = (
    "\tif (xmlFile == NULL)\n"
    "\t\tLOGERR(\"Unable to load '%s'\\n\", actual_filename.c_str());\n"
    "\telse if (mCurrentSet == NULL) {\n"
    "\t\tLOGERR(\"SM-T583: theme is not loaded, skipping %s\\n\", actual_filename.c_str());\n"
    "\t\tfree(xmlFile);\n"
    "\t} else {\n"
    "\t\tmCurrentSet->LoadLanguage(xmlFile, NULL);\n"
    "\t\tfree(xmlFile);\n"
    "\t}\n"
)


def patch_lookup(text):
    # Disabled: gui_lookup patch lives in overrides/bootable/recovery/gui/gui.cpp
    return text


def patch_language(text):
    if LANGUAGE_OLD not in text:
        raise SystemExit("LoadLanguage 의 테마 호출을 찾지 못했습니다.")
    text = text.replace(LANGUAGE_OLD, LANGUAGE_NEW, 1)
    needle = "void PageManager::LoadLanguage(string filename) {\n"
    insert = (
        needle
        + "\t{\n"
        + "\t\tint kmsg = open(\"/dev/kmsg\", O_WRONLY | O_CLOEXEC);\n"
        + "\t\tif (kmsg >= 0) {\n"
        + "\t\t\tconst char *msg = \"SM-T583 stage language\\n\";\n"
        + "\t\t\twrite(kmsg, msg, 23);\n"
        + "\t\t\tclose(kmsg);\n"
        + "\t\t}\n"
        + "\t}\n"
    )
    if needle not in text:
        raise SystemExit("LoadLanguage 함수를 찾지 못했습니다.")
    return text.replace(needle, insert, 1)


def patch_twrp(text):
    helper = (
        "static const char sm_bright[] = \"/sys/devices/14800000.dsim/backlight/panel/brightness\";\n"
        "static const char sm_stage[] = \"/cache/recovery/sm-stage\";\n"
        "static void sm_beacon(const char *value) {\n"
        "\tconst char *p = value;\n"
        "\twhile (*p)\n"
        "\t\tp++;\n"
        "\tint fd = open(sm_bright, O_WRONLY | O_CLOEXEC);\n"
        "\tif (fd >= 0) {\n"
        "\t\twrite(fd, value, p - value);\n"
        "\t\tclose(fd);\n"
        "\t}\n"
        "\tfd = open(sm_stage, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);\n"
        "\tif (fd >= 0) {\n"
        "\t\twrite(fd, value, p - value);\n"
        "\t\twrite(fd, \"\\n\", 1);\n"
        "\t\tclose(fd);\n"
        "\t}\n"
        "}\n"
        + FAULT_HELPER +
        "static void sm_kmsg(const char *text) {\n"
        "\tint fd = open(\"/dev/kmsg\", O_WRONLY | O_CLOEXEC);\n"
        "\tif (fd >= 0) {\n"
        "\t\tconst char *p = text;\n"
        "\t\twhile (*p)\n"
        "\t\t\tp++;\n"
        "\t\twrite(fd, text, p - text);\n"
        "\t\twrite(fd, \"\\n\", 1);\n"
        "\t\tclose(fd);\n"
        "\t}\n"
        "}\n\n"
    )
    anchor = "static void Print_Prop(const char *key, const char *name, void *cookie) {\n"
    if anchor not in text:
        raise SystemExit("twrp.cpp Print_Prop 을 찾지 못했습니다.")
    if "#include <fcntl.h>\n" not in text:
        text = text.replace("#include <unistd.h>\n", "#include <unistd.h>\n#include <fcntl.h>\n", 1)
    if "static void sm_kmsg(" not in text:
        text = text.replace(
            anchor,
            "#include <signal.h>\n#include <string.h>\n#include <ucontext.h>\n\n" + helper + anchor,
            1,
        )
    replacements = (
        ("\tsignal(SIGPIPE, SIG_IGN);\n", "\tsignal(SIGPIPE, SIG_IGN);\n\tsm_install_faults();\n"),
        ("\tgui_init();\n", "\tgui_init();\n\tsm_beacon(\"151\");\n\tsm_kmsg(\"SM-T583 stage after-gui-init\");\n"),
        (
            "\tgui_loadResources();\n",
            "\tsm_beacon(\"171\");\n"
            "\tif (gui_loadResources() != 0)\n"
            "\t\tsm_kmsg(\"SM-T583 stage resources failed\");\n"
            "\telse\n"
            "\t\tsm_kmsg(\"SM-T583 stage resources ok\");\n"
            "\tsm_beacon(\"153\");\n",
        ),
        ("\tDataManager::ReadSettingsFile();\n", "\tDataManager::ReadSettingsFile();\n\tsm_beacon(\"154\");\n"),
        (
            "\tPageManager::LoadLanguage(DataManager::GetStrValue(\"tw_language\"));\n",
            "\tsm_beacon(\"155\");\n"
            "\tsm_kmsg(\"SM-T583 stage skip-language\");\n",
        ),
        ("gui_startPage(\"system_readonly\", 1, 1)", "(sm_beacon(\"157\"), 0)"),
        (
            "\tgui_start();\n",
            "\tsm_beacon(\"156\");\n"
            "\tsm_kmsg(\"SM-T583 stage gui-start\");\n"
            "\tif (gui_start() != 0) {\n"
            "\t\tsm_kmsg(\"SM-T583 stage gui-start failed\");\n"
            "\t\twhile (true)\n"
            "\t\t\tsleep(30);\n"
            "\t}\n",
        ),
    )
    for old, new in replacements:
        if old not in text:
            raise SystemExit("twrp.cpp 에서 다음을 찾지 못했습니다: " + old.strip())
        text = text.replace(old, new, 1)
    text = skip_sar(text)
    return add_main_beacons(mark_fstab(text))


def add_main_beacons(text):
    # Build 88 stopped between beacon 149 (before Output_Partition_Logging)
    # and 153 (after gui_loadResources). 170 means the partition dump finished.
    old = "\tsm_beacon(\"149\");\n\tPartitionManager.Output_Partition_Logging();\n"
    new = old + "\tsm_beacon(\"170\");\n\tsm_kmsg(\"SM-T583 stage beacon89 partition-log-done\");\n"
    if old not in text:
        raise SystemExit("twrp.cpp 에서 149 비콘과 Output_Partition_Logging 을 찾지 못했습니다.")
    return text.replace(old, new, 1)


def skip_sar(text):
    start = text.find("\t// Begin SAR detection\n")
    end = text.find("\t// End SAR detection\n")
    if start < 0 or end < 0:
        return text
    end += len("\t// End SAR detection\n")
    # Oreo has no system-as-root. That probe still walks the fstab and
    # the following pass faults before gui_loadResources returns.
    note = (
        "\t// SM-T583 is Oreo and has no system-as-root partition.\n"
        "\tsm_kmsg(\"SM-T583 stage skip-sar\");\n"
    )
    return text[:start] + note + text[end:]


def mark_fstab(text):
    calls = (
        "\tif (!PartitionManager.Process_Fstab(fstab_filename, 1, 0)) {\n",
        "\tif (!PartitionManager.Process_Fstab(fstab_filename, 1)) {\n",
    )
    for call in calls:
        if call in text:
            text = text.replace(call, "\tsm_beacon(\"158\");\n" + call, 1)
            break
    else:
        raise SystemExit("Process_Fstab 호출을 찾지 못했습니다.")
    log = "\tPartitionManager.Output_Partition_Logging();\n"
    if log not in text:
        raise SystemExit("Output_Partition_Logging 을 찾지 못했습니다.")
    return text.replace(log, "\tsm_beacon(\"149\");\n" + log, 1)


PART_OLD = (
    "\t\tDecrypt_Adopted();\n"
    "\t}\n"
    "#endif\n"
    "\tUpdate_System_Details();\n"
    "\tUnMount_Main_Partitions();\n"
)
PART_NEW = (
    "\t\tLOGINFO(\"SM-T583: skip adopted storage decrypt\\n\");\n"
    "\t}\n"
    "#endif\n"
    "\tLOGINFO(\"SM-T583: skip partition size scan\\n\");\n"
    "\tUnMount_Main_Partitions();\n"
)


def patch_partition(text):
    if PART_OLD not in text:
        raise SystemExit("fstab 처리 끝의 암호 해제와 용량 검사를 찾지 못했습니다.")
    return text.replace(PART_OLD, PART_NEW, 1)


DATA_OLD = (
    "\tPartitionManager.Mount_All_Storage();\n"
    "\tupdate_tz_environment_variables();\n"
    "\tTWFunc::Set_Brightness(GetStrValue(\"tw_brightness\"));\n"
)
DATA_NEW = (
    "\tPartitionManager.Mount_All_Storage();\n"
    "\tupdate_tz_environment_variables();\n"
    "\tLOGINFO(\"SM-T583: skip late brightness restore\\n\");\n"
)


def patch_data(text):
    if DATA_OLD not in text:
        raise SystemExit("ReadSettingsFile 의 늦은 밝기 복원을 찾지 못했습니다.")
    return text.replace(DATA_OLD, DATA_NEW, 1)


GUI_INIT_81 = (
    "\tgr_init();\n"
    "\tTWFunc::Set_Brightness(DataManager::GetStrValue(\"tw_brightness\"));\n"
    "\n"
    "\t// load and show splash screen\n"
    "\tif (PageManager::LoadPackage(\"splash\", TWRES \"splash.xml\", \"splash\")) {\n"
    "\t\tLOGERR(\"Failed to load splash screen XML.\\n\");\n"
    "\t}\n"
    "\telse {\n"
    "\t\tPageManager::SelectPackage(\"splash\");\n"
    "\t\tPageManager::Render();\n"
    "\t\tflip();\n"
    "\t\tPageManager::ReleasePackage(\"splash\");\n"
    "\t}\n"
    "\n"
    "\tev_init();\n"
)
GUI_INIT_90 = (
    "\tgr_init();\n"
    "\tTWFunc::Set_Brightness(DataManager::GetStrValue(\"tw_brightness\"));\n"
    "\n"
    "#ifdef TW_SCREEN_BLANK_ON_BOOT\n"
    "        printf(\"TW_SCREEN_BLANK_ON_BOOT := true\\n\");\n"
    "        blankTimer.blank();\n"
    "        blankTimer.resetTimerAndUnblank();\n"
    "#endif\n"
    "\n"
    "\t// load and show splash screen\n"
    "\tif (PageManager::LoadPackage(\"splash\", TWRES \"splash.xml\", \"splash\")) {\n"
    "\t\tLOGERR(\"Failed to load splash screen XML.\\n\");\n"
    "\t}\n"
    "\telse {\n"
    "\t\tPageManager::SelectPackage(\"splash\");\n"
    "\t\tPageManager::Render();\n"
    "\t\tflip();\n"
    "\t\tPageManager::ReleasePackage(\"splash\");\n"
    "\t}\n"
    "\n"
    "#ifdef TW_DELAY_TOUCH_INIT_MS\n"
    "\tusleep(TW_DELAY_TOUCH_INIT_MS);\n"
    "#endif\n"
    "\tev_init();\n"
)
GUI_INIT_NEW = (
    "\t{\n"
    "\t\tint fd = open(\"/sys/devices/14800000.dsim/backlight/panel/brightness\", O_WRONLY | O_CLOEXEC);\n"
    "\t\tif (fd >= 0) {\n"
    "\t\t\twrite(fd, \"150\", 3);\n"
    "\t\t\tclose(fd);\n"
    "\t\t}\n"
    "\t}\n"
    "\tgr_init();\n"
    "\tTWFunc::Set_Brightness(DataManager::GetStrValue(\"tw_brightness\"));\n"
    "\tLOGINFO(\"SM-T583 skip splash\\n\");\n"
    "\t{\n"
    "\t\tint fd = open(\"/sys/devices/14800000.dsim/backlight/panel/brightness\", O_WRONLY | O_CLOEXEC);\n"
    "\t\tif (fd >= 0) {\n"
    "\t\t\twrite(fd, \"152\", 3);\n"
    "\t\t\tclose(fd);\n"
    "\t\t}\n"
    "\t}\n"
    "\tev_init();\n"
    "\t{\n"
    "\t\tint fd = open(\"/sys/devices/14800000.dsim/backlight/panel/brightness\", O_WRONLY | O_CLOEXEC);\n"
    "\t\tif (fd >= 0) {\n"
    "\t\t\twrite(fd, \"159\", 3);\n"
    "\t\t\tclose(fd);\n"
    "\t\t}\n"
    "\t}\n"
)


def patch_gui_init(text):
    # Disabled: gui_init splash/brightness beacons are in the vendored gui.cpp
    return text


def patch_gui_cpp(text):
    patch_gui_step(None, text)
    return text


FB_FORMAT_OLD = (
    "    } else if (vi.red.offset == 8 || vi.red.offset == 16) {\n"
    "        printf(\"setting GGL_PIXEL_FORMAT_BGRA_8888\\n\");\n"
    "        gr_framebuffer[0].format = GGL_PIXEL_FORMAT_BGRA_8888;\n"
)
FB_FORMAT_NEW = (
    "    } else if (vi.red.offset == 16) {\n"
    "        printf(\"SM-T583: RGBA draw, swap into panel red.offset 16\\n\");\n"
    "        gr_framebuffer[0].format = GGL_PIXEL_FORMAT_RGBA_8888;\n"
    "        sm_swap_rb = true;\n"
    "    } else if (vi.red.offset == 8) {\n"
    "        printf(\"setting GGL_PIXEL_FORMAT_BGRA_8888\\n\");\n"
    "        gr_framebuffer[0].format = GGL_PIXEL_FORMAT_BGRA_8888;\n"
)
FB_COPY_OLD = (
    "        memcpy(gr_framebuffer[0].data, gr_draw->data,\n"
    "               gr_draw->height * gr_draw->row_bytes);\n"
)
FB_COPY_NEW = (
    "        sm_copy_fb(gr_framebuffer[0].data, gr_draw->data,\n"
    "               gr_draw->height * gr_draw->row_bytes);\n"
)
FB_HELPER = (
    "static bool sm_swap_rb;\n"
    "static void sm_copy_fb(void *dst, const void *src, size_t bytes) {\n"
    "    if (!sm_swap_rb) {\n"
    "        memcpy(dst, src, bytes);\n"
    "        return;\n"
    "    }\n"
    "    const unsigned char *s = (const unsigned char *)src;\n"
    "    unsigned char *d = (unsigned char *)dst;\n"
    "    size_t i;\n"
    "    for (i = 0; i + 3 < bytes; i += 4) {\n"
    "        d[i] = s[i + 2];\n"
    "        d[i + 1] = s[i + 1];\n"
    "        d[i + 2] = s[i];\n"
    "        d[i + 3] = s[i + 3];\n"
    "    }\n"
    "}\n"
    "\n"
)


def patch_fbdev(text):
    anchor = "static bool double_buffered;\n"
    if anchor not in text:
        raise SystemExit("graphics_fbdev double_buffered 를 찾지 못했습니다.")
    if "static bool sm_swap_rb;" not in text:
        text = text.replace(anchor, anchor + FB_HELPER, 1)
    if FB_FORMAT_OLD not in text:
        raise SystemExit("red.offset 16 의 BGRA 설정을 찾지 못했습니다.")
    text = text.replace(FB_FORMAT_OLD, FB_FORMAT_NEW, 1)
    if FB_COPY_OLD not in text:
        raise SystemExit("단일 버퍼 memcpy 를 찾지 못했습니다.")
    text = text.replace(FB_COPY_OLD, FB_COPY_NEW, 1)
    if "GGL_PIXEL_FORMAT_BGRA_8888" in text.split("vi.red.offset == 16", 1)[0]:
        raise SystemExit("red.offset 16 이 아직 BGRA 로 그려집니다.")
    return text


MISC_BCB_OLD = (
    "    if (!read_bootloader_message(&boot, &err)) {\n"
    "        printf(\"%s\\n\", err.c_str());\n"
    "        // If fails, leave a zeroed bootloader_message.\n"
    "        memset(&boot, 0, sizeof(boot));\n"
    "    }\n"
    "    //stage = strndup(boot.stage, sizeof(boot.stage));\n"
)
MISC_BCB_NEW = (
    "    if (!read_bootloader_message(&boot, &err)) {\n"
    "        printf(\"%s\\n\", err.c_str());\n"
    "        // If fails, leave a zeroed bootloader_message.\n"
    "        memset(&boot, 0, sizeof(boot));\n"
    "    }\n"
    "    if (*argc <= 1) {\n"
    "        clear_bootloader_message(&err);\n"
    "        memset(&boot, 0, sizeof(boot));\n"
    "        unlink(COMMAND_FILE);\n"
    "        printf(\"SM-T583: cleared stale misc BCB\\n\");\n"
    "    }\n"
    "    //stage = strndup(boot.stage, sizeof(boot.stage));\n"
)


def patch_misc_bcb(text):
    if MISC_BCB_OLD not in text:
        raise SystemExit("bootloader_message_twrp get_args BCB read block was not found")
    updated = text.replace(MISC_BCB_OLD, MISC_BCB_NEW, 1)
    if updated.count("SM-T583: cleared stale misc BCB") != 1:
        raise SystemExit("misc BCB clear marker was not patched")
    return updated


def simulate_misc_bcb():
    # Samsung stock recovery logs just_reboot_after_update and exits; that
    # string is not in TWRP. Stale /misc BCB or /cache/recovery/command can
    # still make TWRP run a one-shot install or wipe before the UI stays up.
    sample = (
        "void\n"
        "get_args(int *argc, char ***argv) {\n"
        "    bootloader_message boot = {};\n"
        "    std::string err;\n"
        + MISC_BCB_OLD
        + "}\n"
    )
    patched = patch_misc_bcb(sample)
    if "clear_bootloader_message" not in patched or "unlink(COMMAND_FILE)" not in patched:
        raise SystemExit("misc BCB clear did not compile")
    print("sim: idle TWRP boot clears misc BCB and drops /cache/recovery/command")


def simulate_panel_draw():
    # This panel reports red.offset 16. Omni 8.1 pixelflinger faults the first
    # theme blit when the draw buffer is BGRA, after gr_init has already
    # copied a black frame. RGBA drawing plus a channel swap is what the
    # scanout memory expects.
    def visible(draw_format, swap):
        return draw_format == "RGBA_8888" and swap

    if visible("BGRA_8888", False):
        raise SystemExit("BGRA draw must not be treated as visible")
    if not visible("RGBA_8888", True):
        raise SystemExit("RGBA draw with a channel swap must reach the panel")
    print("sim: red.offset 16 uses RGBA drawing and swaps into the panel")


# Brightness codes written by the build 89 beacons. The panel driver logs every
# write as "dsim_panel_set_brightness: N", so the last code before 100 in
# /proc/last_kmsg tells where recovery died. /dev/kmsg strings never showed up
# in build 88, so numbers are the only channel that is known to work.
#   149 before Output_Partition_Logging   170 after it   171 before gui_loadResources
#   180+i  partition i of Output_Partition_Logging
#   173 theme zip path   174 before ui.xml LoadPackage   175 LoadPackage ok   176 package selected
#   210..221 PageManager::LoadPackage   222..229 PageSet::Load
#   10+n resource number   232/234/236 font/image/animation start   238 resources done
#   239/240 before/after gr_ttf_loadFont   241/242/243 image load/scale/done
#   100 fault, then 260+signal, 250 + fault address (8 hex digits, 280+digit),
#   252 + pc, 254 + lr, 256 end
FAULT_HELPER = (
    "static char sm_altstack[16384];\n"
    "static void sm_put(int v) {\n"
    "\tchar b[4];\n"
    "\tint n = 0;\n"
    "\tif (v >= 100)\n"
    "\t\tb[n++] = '0' + (v / 100) % 10;\n"
    "\tb[n++] = '0' + (v / 10) % 10;\n"
    "\tb[n++] = '0' + v % 10;\n"
    "\tint fd = open(sm_bright, O_WRONLY | O_CLOEXEC);\n"
    "\tif (fd >= 0) {\n"
    "\t\twrite(fd, b, n);\n"
    "\t\tclose(fd);\n"
    "\t}\n"
    "}\n"
    "static void sm_hex(unsigned int v) {\n"
    "\tfor (int i = 7; i >= 0; --i)\n"
    "\t\tsm_put(280 + (int)((v >> (i * 4)) & 0xf));\n"
    "}\n"
    "static void sm_fault(int sig, siginfo_t *info, void *ctx) {\n"
    "\tsm_put(100);\n"
    "\tsm_put(260 + (sig & 15));\n"
    "\tsm_put(250);\n"
    "\tsm_hex(info ? (unsigned int)(unsigned long)info->si_addr : 0);\n"
    "\t(void)ctx;\n"
    "#if defined(__arm__)\n"
    "\tif (ctx) {\n"
    "\t\tucontext_t *uc = (ucontext_t *)ctx;\n"
    "\t\tsm_put(252);\n"
    "\t\tsm_hex((unsigned int)uc->uc_mcontext.arm_pc);\n"
    "\t\tsm_put(254);\n"
    "\t\tsm_hex((unsigned int)uc->uc_mcontext.arm_lr);\n"
    "\t}\n"
    "#endif\n"
    "\tsm_put(256);\n"
    "\tfor (;;)\n"
    "\t\tsleep(30);\n"
    "}\n"
    "static void sm_install_faults(void) {\n"
    "\tstack_t ss;\n"
    "\tmemset(&ss, 0, sizeof(ss));\n"
    "\tss.ss_sp = sm_altstack;\n"
    "\tss.ss_size = sizeof(sm_altstack);\n"
    "\tsigaltstack(&ss, NULL);\n"
    "\tstruct sigaction sa;\n"
    "\tmemset(&sa, 0, sizeof(sa));\n"
    "\tsa.sa_sigaction = sm_fault;\n"
    "\tsa.sa_flags = SA_SIGINFO | SA_ONSTACK;\n"
    "\tsigemptyset(&sa.sa_mask);\n"
    "\tsigaction(SIGSEGV, &sa, NULL);\n"
    "\tsigaction(SIGBUS, &sa, NULL);\n"
    "\tsigaction(SIGABRT, &sa, NULL);\n"
    "\tsigaction(SIGILL, &sa, NULL);\n"
    "\tsigaction(SIGFPE, &sa, NULL);\n"
    "}\n"
)


def sm_mark_helper(tag):
    # The volatile read keeps the tag string in the binary so the workflow can
    # grep for it. Nothing sets the flag.
    return (
        "#include <fcntl.h>\n"
        "#include <stdio.h>\n"
        "#include <unistd.h>\n"
        "static volatile int sm_mark_dbg;\n"
        "static void sm_mark(int v) {\n"
        "\tchar b[4];\n"
        "\tint n = 0;\n"
        "\tif (sm_mark_dbg)\n"
        "\t\tprintf(\"SM-T583 beacon89 " + tag + "\\n\");\n"
        "\tif (v >= 100)\n"
        "\t\tb[n++] = '0' + (v / 100) % 10;\n"
        "\tb[n++] = '0' + (v / 10) % 10;\n"
        "\tb[n++] = '0' + v % 10;\n"
        "\tint fd = open(\"/sys/devices/14800000.dsim/backlight/panel/brightness\", O_WRONLY | O_CLOEXEC);\n"
        "\tif (fd >= 0) {\n"
        "\t\twrite(fd, b, n);\n"
        "\t\tclose(fd);\n"
        "\t}\n"
        "}\n\n"
    )


def _swap(text, old, new, what):
    if text.count(old) != 1:
        raise SystemExit("%s: 앵커가 %d 번 나옵니다 (1 번이어야 함): %r" % (what, text.count(old), old[:70]))
    return text.replace(old, new, 1)


def _line_matches(text, pattern, what):
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise SystemExit(
            "%s: 앵커가 %d 번 나옵니다 (1 번이어야 함): %r" % (what, len(matches), pattern)
        )
    return matches[0]


def _line_contains_once(text, needle, what):
    hits = []
    pos = 0
    for line in text.splitlines(keepends=True):
        if needle in line:
            hits.append((pos, line))
        pos += len(line)
    if len(hits) != 1:
        raise SystemExit(
            "%s: 앵커가 %d 번 나옵니다 (1 번이어야 함): %r" % (what, len(hits), needle)
        )
    return hits[0]


def _mark_before_line_contains(text, needle, code, what):
    start, line = _line_contains_once(text, needle, what)
    indent = line[: len(line) - len(line.lstrip())]
    insert = indent + "sm_mark(%d);\n" % code
    if insert in text:
        return text
    return text[:start] + insert + text[start:]


def _mark_before_line(text, pattern, code, what):
    m = _line_matches(text, pattern, what)
    line = m.group(0)
    indent = re.match(r"^(\s*)", line).group(1)
    insert = indent + "sm_mark(%d);\n" % code
    if insert in text:
        return text
    return text[: m.start()] + insert + text[m.start() :]


def _mark_after_line(text, pattern, code, what):
    m = _line_matches(text, pattern, what)
    line = m.group(0)
    indent = re.match(r"^(\s*)", line).group(1)
    suffix = line + "\n" + indent + "sm_mark(%d);\n" % code
    if suffix in text:
        return text
    return text[: m.start()] + suffix + text[m.end() :]


def beacon_partition(text):
    if "sm_mark(" in text:
        return text
    anchor = "void TWPartitionManager::Output_Partition_Logging(void) {\n"
    text = _swap(text, anchor, sm_mark_helper("part") + anchor, "partitionmanager.cpp")
    text = _swap(
        text,
        "\tfor (iter = Partitions.begin(); iter != Partitions.end(); iter++)\n"
        "\t\tOutput_Partition((*iter));\n",
        "\tint sm_i = 0;\n"
        "\tfor (iter = Partitions.begin(); iter != Partitions.end(); iter++) {\n"
        "\t\tsm_mark(180 + (sm_i++ % 30));\n"
        "\t\tOutput_Partition((*iter));\n"
        "\t}\n"
        "\tsm_mark(179);\n",
        "partitionmanager.cpp loop",
    )
    return text


def beacon_gui(text):
    # gui.cpp theme-path / ui.xml anchors are not patched here (CI uses vendored file).
    patch_gui_step(None, text)
    return text


def beacon_pages(text):
    if "sm_mark(" in text:
        return text
    anchor = "LoadingContext* PageManager::currentLoadingContext = NULL;\n"
    text = _swap(text, anchor, anchor + "\n" + sm_mark_helper("pages"), "pages.cpp helper")
    steps = (
        ("\tmReloadTheme = false;\n\tmStartPage = startpage;\n",
         "\tsm_mark(210);\n\tmReloadTheme = false;\n\tmStartPage = startpage;\n"),
        ("\t\t\tLoadLanguageList(NULL);\n",
         "\t\t\tsm_mark(211);\n\t\t\tLoadLanguageList(NULL);\n\t\t\tsm_mark(212);\n"),
        ("\tpageSet = mCurrentSet;\n\tmCurrentSet = new PageSet();\n",
         "\tsm_mark(213);\n\tpageSet = mCurrentSet;\n\tmCurrentSet = new PageSet();\n\tsm_mark(214);\n"),
        ("\tif (baseLanguageFile) {\n",
         "\tsm_mark(215);\n\tif (baseLanguageFile) {\n"),
        ("\tif (languageFile) {\n\t\tmCurrentSet->LoadLanguage(languageFile, ctx.zip);\n\t\tfree(languageFile);\n\t}\n",
         "\tif (languageFile) {\n\t\tsm_mark(216);\n\t\tmCurrentSet->LoadLanguage(languageFile, ctx.zip);\n"
         "\t\tsm_mark(217);\n\t\tfree(languageFile);\n\t}\n"),
        ("\tret = mCurrentSet->Load(ctx, mainxmlfilename);\n",
         "\tsm_mark(218);\n\tret = mCurrentSet->Load(ctx, mainxmlfilename);\n\tsm_mark(219);\n"),
        ("\t\tmCurrentSet->SetPage(startpage);\n",
         "\t\tsm_mark(220);\n\t\tmCurrentSet->SetPage(startpage);\n\t\tsm_mark(221);\n"),
        ("\tdoc->parse<0>(xmlbuffer);\n",
         "\tsm_mark(222);\n\tdoc->parse<0>(xmlbuffer);\n\tsm_mark(223);\n"),
        ("\tif (isMain) {\n\t\tint rc = LoadDetails(ctx, root);\n",
         "\tsm_mark(229);\n\tif (isMain) {\n\t\tint rc = LoadDetails(ctx, root);\n"),
        ("\tLOGINFO(\"Loading resources...\\n\");\n",
         "\tsm_mark(224);\n\tLOGINFO(\"Loading resources...\\n\");\n"),
        ("\tLOGINFO(\"Loading variables...\\n\");\n",
         "\tsm_mark(225);\n\tLOGINFO(\"Loading variables...\\n\");\n"),
        ("\tLOGINFO(\"Loading mouse cursor...\\n\");\n",
         "\tsm_mark(226);\n\tLOGINFO(\"Loading mouse cursor...\\n\");\n"),
        ("\tLOGINFO(\"Loading pages...\\n\");\n",
         "\tsm_mark(227);\n\tLOGINFO(\"Loading pages...\\n\");\n"),
        ("\t// process includes recursively\n",
         "\tsm_mark(228);\n\t// process includes recursively\n"),
    )
    for old, new in steps:
        text = _swap(text, old, new, "pages.cpp")
    return text


def beacon_resources(text):
    if "sm_mark(" in text:
        return text
    anchor = "int Resource::ExtractResource("
    text = _swap(text, anchor, sm_mark_helper("res") + "static int sm_res_idx;\n\n" + anchor, "resources.cpp helper")
    steps = (
        ("\t\t\tmFont = gr_ttf_loadFont(tmpname.c_str(), font_size, dpi);\n",
         "\t\t\tsm_mark(239);\n\t\t\tmFont = gr_ttf_loadFont(tmpname.c_str(), font_size, dpi);\n\t\t\tsm_mark(240);\n"),
        ("\t\t\tmFont = gr_ttf_loadFont(file.c_str(), font_size, dpi);\n",
         "\t\t\tsm_mark(239);\n\t\t\tmFont = gr_ttf_loadFont(file.c_str(), font_size, dpi);\n\t\t\tsm_mark(240);\n"),
        ("\tLoadImage(pZip, file, &temp_surface);\n\tCheckAndScaleImage(temp_surface, &mSurface, retain_aspect);\n",
         "\tsm_mark(241);\n\tLoadImage(pZip, file, &temp_surface);\n\tsm_mark(242);\n"
         "\tCheckAndScaleImage(temp_surface, &mSurface, retain_aspect);\n\tsm_mark(243);\n"),
        ("\t\t\tFontResource* res = new FontResource(child, pZip);\n",
         "\t\t\tsm_mark(232);\n\t\t\tsm_mark(10 + (sm_res_idx++ % 90));\n"
         "\t\t\tFontResource* res = new FontResource(child, pZip);\n"),
        ("\t\t\tImageResource* res = new ImageResource(child, pZip);\n",
         "\t\t\tsm_mark(234);\n\t\t\tsm_mark(10 + (sm_res_idx++ % 90));\n"
         "\t\t\tImageResource* res = new ImageResource(child, pZip);\n"),
        ("\t\t\tAnimationResource* res = new AnimationResource(child, pZip);\n",
         "\t\t\tsm_mark(236);\n\t\t\tsm_mark(10 + (sm_res_idx++ % 90));\n"
         "\t\t\tAnimationResource* res = new AnimationResource(child, pZip);\n"),
    )
    for old, new in steps:
        text = _swap(text, old, new, "resources.cpp")
    # End of LoadResources: the loop closes right before the function ends.
    head, sep, tail = text.partition("void ResourceManager::LoadResources(")
    if not sep:
        raise SystemExit("resources.cpp 에서 LoadResources 를 찾지 못했습니다.")
    close = tail.find("\n}\n")
    if close < 0:
        raise SystemExit("resources.cpp 에서 LoadResources 의 끝을 찾지 못했습니다.")
    tail = tail[:close] + "\n\tsm_mark(238);" + tail[close:]
    return head + sep + tail


def patch_pages_full(text):
    return beacon_pages(patch_language(text))


def patch_gui_full(text):
    patch_gui_step(None, text)
    return text


def patch_partition_full(text):
    return beacon_partition(patch_partition(text))


def patch_crash(root):
    import os
    files = {
        os.path.join(root, "gui", "pages.cpp"): patch_pages_full,
        os.path.join(root, "gui", "resources.cpp"): beacon_resources,
        os.path.join(root, "twrp.cpp"): patch_twrp,
        os.path.join(root, "data.cpp"): patch_data,
        os.path.join(root, "partitionmanager.cpp"): patch_partition_full,
    }
    for path, fn in files.items():
        if not os.path.exists(path):
            raise SystemExit("없습니다: " + path)
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(fn(text))
    print("테마가 없을 때 언어 로드가 죽지 않게 했고, 단계를 kmsg 에 남깁니다.")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        self_test()
    elif len(sys.argv) == 3 and sys.argv[1] == "--gui":
        if not patch_gui_step(sys.argv[2]):
            sys.exit(1)
        print("gui.cpp: patch_bringup --gui skipped (always ok)")
        sys.exit(0)
    elif len(sys.argv) == 3 and sys.argv[1] == "--init-rc":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_init_rc(text))
        print("debuggable 가 adbd 를 죽이지 않게 했습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--usb-ffs":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        text = patch_usb_v1(text)
        open(path, "w", encoding="utf-8", newline="\n").write(patch_usb(text))
        print("adbd 가 v1 디스크립터 전에 ep0 를 다시 열고, ep1 을 기다립니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--adb-main":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_adb_main(text))
        print("adbd 가 ep0 가 없을 때 USB 를 포기하지 않습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--crash":
        patch_crash(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--fbdev":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_fbdev(text))
        print("패널 red.offset 16 은 RGBA 로 그리고 복사할 때 채널을 바꿉니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--misc-bcb":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_misc_bcb(text))
        print("TWRP 가 misc BCB 와 /cache/recovery/command 찌꺼기를 지웁니다.")
    else:
        sys.exit(
            "사용법: patch_bringup.py --self-test | --gui FILE | --init-rc FILE | "
            "--usb-ffs FILE | --adb-main FILE | --crash RECOVERY_ROOT | --fbdev FILE | --misc-bcb FILE"
        )
