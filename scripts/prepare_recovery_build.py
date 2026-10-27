import argparse
from pathlib import Path


PATCHES = (
    (
        Path("bootable/recovery/prebuilt/Android.mk"),
        "$(warning vendor_hw: $(LOCAL_POST_INSTALL_CMD))",
        "$(info vendor_hw relink command: $(LOCAL_POST_INSTALL_CMD))",
    ),
    (
        Path("build/make/core/tasks/tools/compatibility.mk"),
        "$(warning $(t) has no license metadata)",
        "$(if $(filter 1,$(ORANGEFOX_RECOVERY_IMAGE_ONLY)),,$(warning $(t) has no license metadata))",
    ),
)


def replace_once(text, old, new, path):
    count = text.count(old)
    if count != 1:
        raise ValueError("expected one diagnostic in {}, found {}".format(path, count))
    return text.replace(old, new, 1)


def prepare(workspace):
    for relative_path, old, new in PATCHES:
        path = workspace / relative_path
        if not path.is_file():
            raise FileNotFoundError("required OrangeFox source file is missing: {}".format(path))
        original = path.read_text(encoding="utf-8")
        updated = replace_once(original, old, new, relative_path)
        path.write_text(updated, encoding="utf-8", newline="\n")
        print("Updated recovery-build diagnostic in {}".format(relative_path))


def main():
    parser = argparse.ArgumentParser(description="Prepare OrangeFox source for an image-only recovery build")
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.workspace)


if __name__ == "__main__":
    main()