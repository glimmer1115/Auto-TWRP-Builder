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
    if GUI_OLD not in text:
        raise SystemExit("gui.cpp 에서 설정 파티션 재시도 블록을 찾지 못했습니다.")
    updated = text.replace(GUI_OLD, GUI_NEW, 1)
    if "Mount_Settings_Storage(true)" in updated.split("gui_loadResources", 1)[-1].split("gui_loadCustomResources", 1)[0]:
        raise SystemExit("gui_loadResources 에 오류 창을 띄우는 마운트가 남아 있습니다.")
    return updated


def patch_init_rc(text):
    updated, count = ADB_OLD.subn(ADB_NEW, text, count=1)
    if count != 1:
        raise SystemExit("init.rc 에서 adbd 재시작 블록을 찾지 못했습니다.")
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


def self_test():
    gui = "int gui_loadResources(void)\n{\n" + GUI_OLD + "\n}\nextern \"C\" int gui_loadCustomResources(void)\n"
    got = patch_gui(gui)
    body = got.split("gui_loadResources", 1)[1].split("gui_loadCustomResources", 1)[0]
    if "usleep(500000)" in body or "Mount_Settings_Storage(true)" in body:
        raise SystemExit("retry loop still compiled")
    if "SM-T583: settings storage not mounted" not in body:
        raise SystemExit("replacement log missing")
    # Five 500ms sleeps, then the error dialog. The patched path does neither.
    print("old path: 5 x 500ms then Mount_Settings_Storage(true)")
    print("new path: one failed mount, built-in theme, no error dialog")
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
    rc_path = r"c:\Auto-TWRP-Builder\overrides\init.recovery.samsungexynos7870.rc"
    usb_path = r"c:\Auto-TWRP-Builder\overrides\init.recovery.usb.rc"
    wait_path = r"c:\Auto-TWRP-Builder\overrides\usbwait.sh"
    rc = open(rc_path, encoding="utf-8").read()
    usb = open(usb_path, encoding="utf-8").read()
    wait = open(wait_path, encoding="utf-8").read()
    if not wait.startswith("#!/sbin/sh\n") or "\nsleep 2\n" not in wait:
        raise SystemExit("usbwait.sh is not the recovery-domain sleep")
    if "setprop sys.usb.config=" in rc or "setprop sys.usb.config " in rc or "setprop sys.usb.ffs.ready" in rc:
        raise SystemExit("usb config is forced before adbd writes descriptors")
    if "wait /dev/usb-ffs/adb/ep1" in rc or "on property:sys.usb.ffs.ready=1" in rc:
        raise SystemExit("ep1 does not exist until the gadget is bound")
    func_at = rc.find("write /sys/class/android_usb/android0/functions adb")
    start_at = rc.find("start adbd")
    sleep_at = rc.find("exec u:r:recovery:s0 root root -- /sbin/usbwait.sh")
    enable_at = rc.find("write /sys/class/android_usb/android0/enable 1")
    chmod_at = rc.find("chmod 0666 /dev/usb-ffs/adb/ep0")
    if "exec /sbin/toybox sleep 2" in rc:
        raise SystemExit("unlabeled toybox exec is denied and enables the gadget immediately")
    if min(chmod_at, func_at, start_at, sleep_at, enable_at) < 0 or not (chmod_at < func_at < start_at < sleep_at < enable_at):
        raise SystemExit("gadget enable is not after adbd has had time to write descriptors")
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
    print("usb: recovery-domain usbwait blocks, then init enables 04e8:6860")
    gui_path = r"C:\twrp-879923a\gui-90.cpp"
    if not __import__("os").path.exists(gui_path):
        gui_path = r"C:\twrp-879923a\gui.cpp"
    gui = open(gui_path, encoding="utf-8").read() if __import__("os").path.exists(gui_path) else ""
    if gui:
        got = patch_gui_cpp(gui)
        if "TW_SCREEN_BLANK_ON_BOOT" in gui and "TW_SCREEN_BLANK_ON_BOOT" in got.split("int gui_init(void)", 1)[1].split("int gui_loadResources", 1)[0]:
            raise SystemExit("android-9.0 splash block was not patched")
        body = got.split("std::string gui_lookup", 1)[1].split("gui_init", 1)[0]
        if "GetResources() == NULL" not in body or body.find("== NULL") > body.find("FindString"):
            raise SystemExit("lookup null check missing")
        init_body = got.split("int gui_init(void)", 1)[1].split("int gui_loadResources", 1)[0]
        if "LoadPackage(\"splash\"" in init_body or "flip();" in init_body:
            raise SystemExit("splash render remains")
        for marker in ("write(fd, \"150\", 3)", "write(fd, \"152\", 3)", "write(fd, \"159\", 3)", "SM-T583 skip splash"):
            if marker not in init_body:
                raise SystemExit("missing " + marker)
        pages = open(r"C:\twrp-879923a\pages.cpp", encoding="utf-8").read()
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
        for marker in ("sm_beacon(\"151\")", "sm_beacon(\"149\")", "sm_beacon(\"153\")", "sm_beacon(\"154\")", "sm_beacon(\"155\")", "sm_beacon(\"156\")", "sm_beacon(\"157\")", "sm_beacon(\"158\")", "signal(SIGSEGV, sm_fault)", "/cache/recovery/sm-stage"):
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
    print("self-test ok")


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
    if LOOKUP_OLD not in text:
        raise SystemExit("gui_lookup 을 찾지 못했습니다.")
    return text.replace(LOOKUP_OLD, LOOKUP_NEW, 1)


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
        "static void sm_fault(int sig) {\n"
        "\t(void)sig;\n"
        "\tint fd = open(sm_bright, O_WRONLY | O_CLOEXEC);\n"
        "\tif (fd >= 0) {\n"
        "\t\twrite(fd, \"100\", 3);\n"
        "\t\tclose(fd);\n"
        "\t}\n"
        "\tfor (;;)\n"
        "\t\tsleep(30);\n"
        "}\n"
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
        text = text.replace(anchor, helper + anchor, 1)
    replacements = (
        ("\tsignal(SIGPIPE, SIG_IGN);\n", "\tsignal(SIGPIPE, SIG_IGN);\n\tsignal(SIGSEGV, sm_fault);\n\tsignal(SIGBUS, sm_fault);\n\tsignal(SIGABRT, sm_fault);\n"),
        ("\tgui_init();\n", "\tgui_init();\n\tsm_beacon(\"151\");\n\tsm_kmsg(\"SM-T583 stage after-gui-init\");\n"),
        (
            "\tgui_loadResources();\n",
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
    return mark_fstab(text)


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
    if GUI_INIT_90 in text:
        return text.replace(GUI_INIT_90, GUI_INIT_NEW, 1)
    if GUI_INIT_81 in text:
        return text.replace(GUI_INIT_81, GUI_INIT_NEW, 1)
    raise SystemExit("gui_init 의 스플래시 화면을 찾지 못했습니다.")


def patch_gui_cpp(text):
    return patch_gui_init(patch_lookup(text))


def patch_crash(root):
    import os
    files = {
        os.path.join(root, "gui", "gui.cpp"): patch_gui_cpp,
        os.path.join(root, "gui", "pages.cpp"): patch_language,
        os.path.join(root, "twrp.cpp"): patch_twrp,
        os.path.join(root, "data.cpp"): patch_data,
        os.path.join(root, "partitionmanager.cpp"): patch_partition,
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
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_gui(text))
        print("설정 파티션 오류 창을 테마 로드 전에 띄우지 않습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--init-rc":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_init_rc(text))
        print("debuggable 가 adbd 를 죽이지 않게 했습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--usb-ffs":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_usb(text))
        print("adbd 가 ep1 생성 전에 ep0 를 닫지 않습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--adb-main":
        path = sys.argv[2]
        text = open(path, encoding="utf-8").read()
        open(path, "w", encoding="utf-8", newline="\n").write(patch_adb_main(text))
        print("adbd 가 ep0 가 없을 때 USB 를 포기하지 않습니다.")
    elif len(sys.argv) == 3 and sys.argv[1] == "--crash":
        patch_crash(sys.argv[2])
    else:
        sys.exit("사용법: patch_bringup.py --self-test | --gui FILE | --init-rc FILE | --usb-ffs FILE | --adb-main FILE | --crash RECOVERY_ROOT")
