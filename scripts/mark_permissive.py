# Mark init, recovery, and adbd permissive inside a kernel sepolicy.
#
# The SM-T583 bootloader forces androidboot.selinux=enforcing and the
# Exynos kernel reports success for a write to /sys/fs/selinux/enforce
# without leaving enforcing mode. Domain permissive bits in the loaded
# /sepolicy are the only switch that sticks. BOARD_SEPOLICY_DIRS did
# not land in the policy that actually booted.
#
# Layout matches libsepol from android-8.1.0_r29 (policy version 30):
# magic, "SE Linux", version/config/sym_num/ocon_num, policycaps
# ebitmap, permissive ebitmap, then symbol tables. The permissive
# ebitmap is indexed by the type value. Growing it is safe because the
# Odin packer recompresses the cpio and writes the new ramdisk length.

import struct
import sys

SELINUX_MAGIC = 0xF97CFF8C
POLICY_STRING = b'SE Linux'
MAPSIZE = 64
CEXPR_NOT = 1
CEXPR_AND = 2
CEXPR_OR = 3
CEXPR_ATTR = 4
CEXPR_NAMES = 5
CEXPR_XTARGET = 16
TYPEDATUM_PROPERTY_ATTRIBUTE = 0x0002
TARGETS = (b'init', b'recovery', b'adbd')


class PolicyError(Exception):
    pass


class _Cur(object):
    def __init__(self, data):
        self.data = data
        self.off = 0

    def need(self, n):
        if n < 0 or self.off + n > len(self.data):
            raise PolicyError('정책이 잘렸습니다. 위치 %d 길이 %d' % (self.off, len(self.data)))

    def u32(self):
        self.need(4)
        val = struct.unpack_from('<I', self.data, self.off)[0]
        self.off += 4
        return val

    def u64(self):
        self.need(8)
        val = struct.unpack_from('<Q', self.data, self.off)[0]
        self.off += 8
        return val

    def raw(self, n):
        self.need(n)
        chunk = self.data[self.off:self.off + n]
        self.off += n
        return chunk


def _read_ebitmap(cur):
    mapsize = cur.u32()
    highbit = cur.u32()
    count = cur.u32()
    if mapsize != MAPSIZE:
        raise PolicyError('ebitmap mapsize %d' % mapsize)
    nodes = []
    if highbit == 0:
        if count != 0:
            raise PolicyError('빈 ebitmap 의 count 가 0이 아닙니다.')
        return nodes
    if highbit & (MAPSIZE - 1):
        raise PolicyError('ebitmap highbit 정렬이 아닙니다: %d' % highbit)
    if count == 0:
        raise PolicyError('ebitmap highbit 가 있는데 노드가 없습니다.')
    prev = -1
    for _ in range(count):
        startbit = cur.u32()
        mapping = cur.u64()
        if startbit & (MAPSIZE - 1):
            raise PolicyError('ebitmap startbit 정렬이 아닙니다: %d' % startbit)
        if startbit > highbit - MAPSIZE:
            raise PolicyError('ebitmap startbit 가 범위를 넘습니다: %d' % startbit)
        if mapping == 0:
            raise PolicyError('ebitmap 노드가 비어 있습니다.')
        if startbit <= prev:
            raise PolicyError('ebitmap 노드 순서가 아닙니다.')
        prev = startbit
        nodes.append((startbit, mapping))
    if nodes[-1][0] + MAPSIZE != highbit:
        raise PolicyError('ebitmap highbit 가 마지막 노드와 다릅니다.')
    return nodes


def _write_ebitmap(nodes):
    if not nodes:
        return struct.pack('<III', MAPSIZE, 0, 0)
    highbit = nodes[-1][0] + MAPSIZE
    out = [struct.pack('<III', MAPSIZE, highbit, len(nodes))]
    for startbit, mapping in nodes:
        out.append(struct.pack('<IQ', startbit, mapping))
    return b''.join(out)


def _set_bit(nodes, bit):
    if bit <= 0 or bit > 0xFFFF:
        raise PolicyError('type 값이 이상합니다: %d' % bit)
    startbit = bit & ~(MAPSIZE - 1)
    mask = 1 << (bit & (MAPSIZE - 1))
    for index, (start, mapping) in enumerate(nodes):
        if start == startbit:
            nodes[index] = (start, mapping | mask)
            return
        if start > startbit:
            nodes.insert(index, (startbit, mask))
            return
    nodes.append((startbit, mask))


def _skip_type_set(cur):
    _read_ebitmap(cur)
    _read_ebitmap(cur)
    cur.u32()


def _skip_constraints(cur, count, allow_xtarget, version):
    for _ in range(count):
        cur.u32()
        nexpr = cur.u32()
        if nexpr > 64:
            raise PolicyError('constraint 식이 너무 많습니다.')
        for _expr in range(nexpr):
            expr_type = cur.u32()
            attr = cur.u32()
            cur.u32()
            if expr_type == CEXPR_NAMES:
                if not allow_xtarget and (attr & CEXPR_XTARGET):
                    raise PolicyError('constraint 에 xtarget 이 있습니다.')
                _read_ebitmap(cur)
                if version >= 29:
                    _skip_type_set(cur)
            elif expr_type not in (CEXPR_NOT, CEXPR_AND, CEXPR_OR, CEXPR_ATTR):
                raise PolicyError('알 수 없는 constraint 식 %d' % expr_type)


def _skip_perm(cur):
    # perm_read pulls the length and the value, then the name bytes.
    length = cur.u32()
    cur.u32()
    if length == 0 or length > 1024:
        raise PolicyError('권한 이름이 이상합니다.')
    cur.raw(length)


def _read_symbols(cur, version):
    # commons
    cur.u32()
    nel = cur.u32()
    if nel > 100000:
        raise PolicyError('common 항목이 너무 많습니다.')
    for _ in range(nel):
        length = cur.u32()
        cur.u32()
        cur.u32()
        nperm = cur.u32()
        if length == 0 or length > 1024 or nperm > 100000:
            raise PolicyError('common 헤더가 이상합니다.')
        cur.raw(length)
        for _perm in range(nperm):
            _skip_perm(cur)

    # classes
    cur.u32()
    nel = cur.u32()
    if nel > 100000:
        raise PolicyError('class 항목이 너무 많습니다.')
    for _ in range(nel):
        length = cur.u32()
        length2 = cur.u32()
        cur.u32()
        cur.u32()
        nperm = cur.u32()
        ncons = cur.u32()
        if length == 0 or length > 1024 or length2 > 1024 or nperm > 100000 or ncons > 100000:
            raise PolicyError('class 헤더가 이상합니다.')
        cur.raw(length)
        if length2:
            cur.raw(length2)
        for _perm in range(nperm):
            _skip_perm(cur)
        _skip_constraints(cur, ncons, False, version)
        if version >= 19:
            ntrans = cur.u32()
            if ntrans > 100000:
                raise PolicyError('validatetrans 가 너무 많습니다.')
            _skip_constraints(cur, ntrans, True, version)
        if version >= 27:
            cur.u32()
            cur.u32()
            cur.u32()
        if version >= 28:
            cur.u32()

    # roles
    cur.u32()
    nel = cur.u32()
    if nel > 100000:
        raise PolicyError('role 항목이 너무 많습니다.')
    for _ in range(nel):
        length = cur.u32()
        cur.u32()
        if version >= 24:
            cur.u32()
        if length == 0 or length > 1024:
            raise PolicyError('role 이름이 이상합니다.')
        cur.raw(length)
        _read_ebitmap(cur)
        _read_ebitmap(cur)

    # types
    cur.u32()
    nel = cur.u32()
    if nel > 100000:
        raise PolicyError('type 항목이 너무 많습니다.')
    found = {}
    for _ in range(nel):
        if version < 24:
            raise PolicyError('policy version %d 는 type 경계 필드가 없습니다.' % version)
        length = cur.u32()
        value = cur.u32()
        properties = cur.u32()
        cur.u32()
        if length == 0 or length > 1024:
            raise PolicyError('type 이름이 이상합니다.')
        name = cur.raw(length)
        if properties & TYPEDATUM_PROPERTY_ATTRIBUTE:
            continue
        if name in TARGETS and name not in found:
            found[name] = value
    return found


def _parse(blob):
    cur = _Cur(blob)
    magic = cur.u32()
    if magic != SELINUX_MAGIC:
        raise PolicyError('sepolicy 매직이 아닙니다: %#08x' % magic)
    length = cur.u32()
    if length == 0 or length > 32:
        raise PolicyError('정책 문자열 길이가 이상합니다: %d' % length)
    text = cur.raw(length)
    if text != POLICY_STRING:
        raise PolicyError('정책 문자열이 SE Linux 가 아닙니다: %r' % (text,))
    version = cur.u32()
    cur.u32()
    sym_num = cur.u32()
    cur.u32()
    if version < 29 or version > 30:
        raise PolicyError('policy version %d 는 8.1 형식(29..30)이 아닙니다.' % version)
    if sym_num < 4:
        raise PolicyError('심볼 테이블 수가 부족합니다: %d' % sym_num)
    _read_ebitmap(cur)
    perm_start = cur.off
    nodes = _read_ebitmap(cur)
    perm_end = cur.off
    found = _read_symbols(cur, version)
    return perm_start, perm_end, nodes, found


def mark_policy(blob):
    perm_start, perm_end, nodes, found = _parse(blob)
    missing = [name for name in TARGETS if name not in found]
    if missing:
        raise PolicyError('정책에서 타입을 찾지 못했습니다: %s' % ', '.join(item.decode() for item in missing))
    for name in TARGETS:
        _set_bit(nodes, found[name])
    written = _write_ebitmap(nodes)
    updated = blob[:perm_start] + written + blob[perm_end:]
    again_start, again_end, again_nodes, again_found = _parse(updated)
    if again_found != found:
        raise PolicyError('수정 후 type 값이 달라졌습니다.')
    if again_end - again_start != len(written):
        raise PolicyError('수정 후 permissive 맵 길이가 다릅니다.')
    if updated[again_end:] != blob[perm_end:]:
        raise PolicyError('수정 후 정책 뒷부분이 달라졌습니다.')
    for name in TARGETS:
        bit = found[name]
        startbit = bit & ~(MAPSIZE - 1)
        mask = 1 << (bit & (MAPSIZE - 1))
        hit = False
        for start, mapping in again_nodes:
            if start == startbit and mapping & mask:
                hit = True
        if not hit:
            raise PolicyError('%s permissive 비트가 기록되지 않았습니다.' % name)
    print('sepolicy permissive', ' '.join('%s=%d' % (name.decode(), found[name]) for name in TARGETS),
          '맵', perm_end - perm_start, '->', len(written))
    return updated


def _align4(value):
    return (value + 3) & ~3


def _iter_cpio(raw):
    offset = 0
    while offset + 110 <= len(raw):
        magic = raw[offset:offset + 6]
        if magic not in (b'070701', b'070702'):
            if raw[offset:].strip(b'\x00') == b'':
                return
            raise PolicyError('cpio 헤더가 아닙니다. 위치 %d' % offset)
        header = raw[offset:offset + 110]
        fields = [int(header[6 + i:6 + i + 8], 16) for i in range(0, 104, 8)]
        filesize = fields[6]
        namesize = fields[11]
        if namesize == 0 or namesize > 4096 or filesize > len(raw):
            raise PolicyError('cpio 항목 크기가 이상합니다.')
        name_at = offset + 110
        if name_at + namesize > len(raw):
            raise PolicyError('cpio 이름이 잘렸습니다.')
        name = raw[name_at:name_at + namesize]
        data_at = _align4(name_at + namesize)
        if data_at + filesize > len(raw):
            raise PolicyError('cpio 데이터가 잘렸습니다.')
        data = raw[data_at:data_at + filesize]
        yield header, name, data
        if name.split(b'\x00', 1)[0] == b'TRAILER!!!':
            return
        offset = _align4(data_at + filesize)


def _pack_cpio(entries):
    out = bytearray()
    for header, name, data in entries:
        fresh = bytearray(header[:110])
        fresh[54:62] = ('%08X' % len(data)).encode('ascii')
        out += fresh
        out += name
        while len(out) % 4:
            out.append(0)
        out += data
        while len(out) % 4:
            out.append(0)
    while len(out) % 256:
        out.append(0)
    return bytes(out)


def mark_cpio(raw):
    if raw[:6] not in (b'070701', b'070702'):
        raise PolicyError('램디스크가 newc cpio 가 아닙니다.')
    entries = list(_iter_cpio(raw))
    if not entries or entries[-1][1].split(b'\x00', 1)[0] != b'TRAILER!!!':
        raise PolicyError('cpio TRAILER 가 없습니다.')
    replaced = 0
    rebuilt = []
    for header, name, data in entries:
        base = name.split(b'\x00', 1)[0]
        if base in (b'sepolicy', b'/sepolicy'):
            mode = int(header[14:22], 16)
            if (mode & 0o170000) != 0o100000:
                raise PolicyError('sepolicy 가 일반 파일이 아닙니다.')
            data = mark_policy(data)
            replaced += 1
        rebuilt.append((header, name, data))
    if replaced != 1:
        names = [item[1].split(b'\x00', 1)[0] for item in entries[:12]]
        raise PolicyError('sepolicy 항목이 %d 개입니다. 앞 이름 %s' % (replaced, names))
    return _pack_cpio(rebuilt)


def _self_test():
    def u32(value):
        return struct.pack('<I', value)

    def eb(nodes):
        if not nodes:
            return struct.pack('<III', 64, 0, 0)
        out = [struct.pack('<III', 64, nodes[-1][0] + 64, len(nodes))]
        for start, mapping in nodes:
            out.append(struct.pack('<IQ', start, mapping))
        return b''.join(out)

    def symbol(name, value, properties):
        raw_name = name.encode('ascii')
        return u32(len(raw_name)) + u32(value) + u32(properties) + u32(0) + raw_name

    common = u32(4) + u32(1) + u32(1) + u32(1) + b'file' + u32(4) + u32(1) + b'read'
    one_expr = u32(1) + u32(1) + u32(CEXPR_NAMES) + u32(4) + u32(1) + eb([]) + eb([]) + eb([]) + u32(0)
    klass = (
        u32(4) + u32(4) + u32(1) + u32(1) + u32(1) + u32(1) +
        b'file' + b'file' + u32(4) + u32(1) + b'open' + one_expr +
        u32(0) + u32(0) + u32(0) + u32(0) + u32(0)
    )
    role = u32(1) + u32(1) + u32(0) + b'r' + eb([]) + eb([])
    types = (
        symbol('init', 1, 1) + symbol('domain', 3, TYPEDATUM_PROPERTY_ATTRIBUTE) +
        symbol('recovery', 2, 1) + symbol('adbd', 70, 1)
    )
    body = (
        u32(SELINUX_MAGIC) + u32(len(POLICY_STRING)) + POLICY_STRING +
        u32(30) + u32(1) + u32(8) + u32(0) +
        eb([]) + eb([]) +
        u32(1) + u32(1) + common +
        u32(1) + u32(1) + klass +
        u32(2) + u32(1) + role +
        u32(70) + u32(4) + types +
        b'SUFFIX-MARKER'
    )
    updated = mark_policy(body)
    if not updated.endswith(b'SUFFIX-MARKER'):
        raise SystemExit('suffix lost')
    if len(updated) == len(body):
        raise SystemExit('bitmap did not grow for bit 70')
    region_start = updated.find(struct.pack('<III', 64, 128, 2))
    if region_start < 0:
        raise SystemExit('grown bitmap header missing')
    _start, mapping = struct.unpack_from('<IQ', updated, region_start + 12)
    _high, highmap = struct.unpack_from('<IQ', updated, region_start + 24)
    if mapping & (1 << 1) == 0 or mapping & (1 << 2) == 0 or highmap & (1 << 6) == 0:
        raise SystemExit('bits missing %x %x' % (mapping, highmap))

    present = eb([(0, (1 << 1) | (1 << 2) | (1 << 3))])
    small_types = symbol('init', 1, 1) + symbol('recovery', 2, 1) + symbol('adbd', 3, 1)
    same = (
        u32(SELINUX_MAGIC) + u32(len(POLICY_STRING)) + POLICY_STRING +
        u32(30) + u32(1) + u32(8) + u32(0) +
        eb([]) + present +
        u32(0) + u32(0) + u32(0) + u32(0) + u32(0) + u32(0) +
        u32(1) + u32(3) + small_types + b'END'
    )
    same_out = mark_policy(same)
    if len(same_out) != len(same) or not same_out.endswith(b'END'):
        raise SystemExit('same-size rewrite changed the length')

    def entry(ino, name, data):
        raw_name = name + b'\x00'
        fields = [ino, 0o100644, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw_name), 0]
        header = b'070701' + b''.join(('%08X' % field).encode('ascii') for field in fields)
        blob = header + raw_name
        blob += b'\x00' * ((4 - (len(blob) % 4)) % 4)
        blob += data
        blob += b'\x00' * ((4 - (len(data) % 4)) % 4)
        return blob

    archive = entry(1, b'init', b'elf') + entry(2, b'sepolicy', body) + entry(3, b'TRAILER!!!', b'')
    packed = mark_cpio(archive)
    found_policy = None
    found_init = None
    for _header, name, data in _iter_cpio(packed):
        base = name.split(b'\x00', 1)[0]
        if base == b'sepolicy':
            found_policy = data
        if base == b'init':
            found_init = data
    if found_init != b'elf' or found_policy is None or not found_policy.endswith(b'SUFFIX-MARKER'):
        raise SystemExit('cpio rewrite dropped a member')
    _parse(found_policy)
    try:
        mark_policy(body[:40])
    except PolicyError:
        pass
    else:
        raise SystemExit('truncated policy was accepted')
    print('self-test ok')


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--self-test':
        _self_test()
    else:
        sys.exit('사용법: mark_permissive.py --self-test')
