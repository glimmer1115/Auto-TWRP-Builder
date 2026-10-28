import json

try:
    with open('ramdisk-analysis.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    print("=== [1] 단일 파일 삭제 시 압축 감소량 Top 10 ===")
    items = data.get('largest_file_single_removals', [])
    for item in sorted(items, key=lambda x: x.get('reduction_bytes', 0), reverse=True)[:10]:
        print("- {}: {:,} bytes 감소".format(item.get('path'), item.get('reduction_bytes', 0)))

    print("\n=== [2] 누적 삭제 시 예상 용량 (Headroom) ===")
    for item in data.get('largest_file_cumulative_removals', []):
        print("- {}개 삭제 시: {:,} bytes (Headroom: {:,})".format(
            item.get('largest_files_removed'),
            item.get('ramdisk_size_bytes', 0),
            item.get('headroom_bytes', 0)
        ))
        
    print("\n=== [3] 가장 큰 디렉토리 Top 5 ===")
    dirs = data.get('directories', [])
    for d in sorted(dirs, key=lambda x: x.get('payload_bytes', 0), reverse=True)[:5]:
        print("- {}: {:,} bytes".format(d.get('path'), d.get('payload_bytes', 0)))

except Exception as e:
    print("에러 발생:", e)