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
    gui = open(r"C:\twrp-879923a\gui.cpp", encoding="utf-8").read() if __import__("os").path.exists(r"C:\twrp-879923a\gui.cpp") else ""
    if gui:
        got = patch_lookup(gui)
        body = got.split("std::string gui_lookup", 1)[1].split("gui_init", 1)[0]
        if "GetResources() == NULL" not in body or body.find("== NULL") > body.find("FindString"):
            raise SystemExit("lookup null check missing")
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
        ("\tgui_init();\n", "\tgui_init();\n\tsm_kmsg(\"SM-T583 stage after-gui-init\");\n"),
        (
            "\tgui_loadResources();\n",
            "\tif (gui_loadResources() != 0)\n"
            "\t\tsm_kmsg(\"SM-T583 stage resources failed\");\n"
            "\telse\n"
            "\t\tsm_kmsg(\"SM-T583 stage resources ok\");\n",
        ),
        (
            "\tPageManager::LoadLanguage(DataManager::GetStrValue(\"tw_language\"));\n",
            "\tsm_kmsg(\"SM-T583 stage skip-language\");\n",
        ),
        (
            "\tgui_start();\n",
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
    return text


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


def patch_crash(root):
    import os
    files = {
        os.path.join(root, "gui", "gui.cpp"): patch_lookup,
        os.path.join(root, "gui", "pages.cpp"): patch_language,
        os.path.join(root, "twrp.cpp"): patch_twrp,
        os.path.join(root, "data.cpp"): patch_data,
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
    elif len(sys.argv) == 3 and sys.argv[1] == "--crash":
        patch_crash(sys.argv[2])
    else:
        sys.exit("사용법: patch_bringup.py --self-test | --gui FILE | --init-rc FILE | --crash RECOVERY_ROOT")
