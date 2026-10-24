from twrpdtgen.device_tree import DeviceTree
import argparse
import os
import re
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Auto-Twrp-Builder")
    parser.add_argument('-i', '--input', required=True, help='Recovery/Boot Image')
    parser.add_argument('-o', '--output', required=True, help='Output path')
    args = parser.parse_args()
    image_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    if not image_path.is_file():
        parser.error('input image does not exist or is not a file: {}'.format(image_path))

    device_tree = DeviceTree(image_path)
    manufacturer = str(device_tree.device_info.manufacturer or '')
    codename = str(device_tree.device_info.codename or '')
    safe_component = re.compile(r'[A-Za-z0-9_-]+')
    if any(
        not safe_component.fullmatch(value)
        for value in (manufacturer, codename)
    ):
        parser.error('device metadata contains an unsafe manufacturer or codename')

    output_path.mkdir(parents=True, exist_ok=True)
    device_tree.dump_to_folder(output_path)

    tree_path = output_path / manufacturer / codename
    android_bp_path = tree_path / 'Android.bp'
    if not android_bp_path.is_file():
        parser.error('generated Android.bp was not found: {}'.format(android_bp_path))

    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as output_file:
            output_file.write('DEVICE_NAME={}\n'.format(manufacturer))
            output_file.write('MAKEFILE_NAME=omni_{}\n'.format(codename))
            output_file.write('DEVICE_PATH={}\n'.format(tree_path.as_posix()))

    print(android_bp_path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
