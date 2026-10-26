from twrpdtgen.device_tree import DeviceTree
import argparse
import os
import re
from pathlib import Path


def install_first_api_level_fallback(first_api_level):
    from sebaubuntu_libs.libandroid import device_info

    original_get_first_prop = device_info.DeviceInfo.get_first_prop
    first_api_level_props = tuple(device_info.FIRST_API_LEVEL)

    def get_first_prop(self, props, data_type=str, default=None, raise_exception=True):
        if tuple(props) == first_api_level_props:
            value = original_get_first_prop(
                self,
                props,
                data_type=data_type,
                default=None,
                raise_exception=False,
            )
            if value is not None:
                return value
            return data_type(str(first_api_level))

        return original_get_first_prop(
            self,
            props,
            data_type=data_type,
            default=default,
            raise_exception=raise_exception,
        )

    device_info.DeviceInfo.get_first_prop = get_first_prop

def main():
    parser = argparse.ArgumentParser(description="Auto-Twrp-Builder")
    parser.add_argument('-i', '--input', required=True, help='Recovery/Boot Image')
    parser.add_argument('-o', '--output', required=True, help='Output path')
    parser.add_argument(
        '--first-api-level',
        type=int,
        help='Use this API level only when ro.product.first_api_level is missing',
    )
    args = parser.parse_args()
    if args.first_api_level is not None:
        if args.first_api_level < 1:
            parser.error('--first-api-level must be a positive integer')
        install_first_api_level_fallback(args.first_api_level)

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
