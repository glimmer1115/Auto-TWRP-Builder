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
    print("self-test ok")


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
    else:
        sys.exit("사용법: patch_bringup.py --self-test | --gui FILE | --init-rc FILE")
