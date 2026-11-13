"""vCard 3.0/4.0 规范化接收的底层解析。

处理顺序固定为：字节级折行展开 -> 名片边界切分（保留原件字节区间）->
行语法（组/属性名/参数/值）解析 -> 按版本与参数解码 -> 转义与结构化值处理。

任何一步都不会先按分号切值再补救：分号只有在值被确认为文本结构化类型后，
才在转义状态机里作为未转义分隔符处理。无法支持的编码不产生联系人，
只生成保留原始字节的隔离记录（Quarantine）。
"""

from dataclasses import dataclass, field
import hashlib
import re

CRLF = b'\r\n'
WSP = (0x20, 0x09)
FOLD_RE = re.compile(rb'\r\n([ \t])')

SUPPORTED_VERSIONS = ('3.0', '4.0')
UTF8_ALIASES = {'', 'UTF-8', 'UTF8', 'US-ASCII', 'ASCII'}

# 4.0 默认 URI 类型的属性（RFC 6350 6.3），3.0 中 TEL/GEO 仍是文本
URI_PROPERTIES_V4 = {
    'TEL', 'URL', 'MEMBER', 'GEO', 'KEY', 'SOURCE', 'IMPP',
    'FBURL', 'CALURI', 'CALADRURI', 'UID',
}
STRUCTURED_TEXT = {
    'N': 5,          # 姓;名;中间名;前缀;后缀
    'ADR': 7,        # 信箱;扩展;街道; locality; region;邮编;国家
    'GEO': 2,        # 仅 3.0：纬度;经度
}
TEXT_LIST_PROPERTIES = {'NICKNAME', 'CATEGORIES', 'ORG'}
FLAT_TEXT_PROPERTIES = {
    'FN', 'TITLE', 'ROLE', 'NOTE', 'EMAIL', 'LANG', 'TZ',
    'SORT-STRING', 'PRODID', 'CLientPIDMAP'.upper(),
}


@dataclass
class Property:
    name: str
    group: str | None
    params: dict[str, list[str]]
    value_bytes: bytes
    line_no: int
    text: str | None = None              # 反转义后的平面文本
    components: list[list[str]] | None = None  # 结构化文本，已逐项反转义
    uri: str | None = None               # URI 型属性的原文
    warnings: list[str] = field(default_factory=list)

    def param(self, key: str) -> str | None:
        values = self.params.get(key.lower())
        return values[0] if values else None

    @property
    def altid(self) -> str | None:
        return self.param('ALTID')

    @property
    def language(self) -> str | None:
        return self.param('LANGUAGE')

    @property
    def pids(self) -> list[str]:
        result = []
        for value in self.params.get('pid', []):
            result.extend(part.strip() for part in value.split(',') if part.strip())
        return result

    @property
    def types(self) -> list[str]:
        result = []
        for value in self.params.get('type', []):
            result.extend(part.strip().lower() for part in value.split(',') if part.strip())
        return result

    @property
    def pref(self) -> int | None:
        raw = self.param('PREF')
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            self.warnings.append('PREF 参数不是整数: %r' % raw)
            return None
        if not 1 <= value <= 100:
            self.warnings.append('PREF 参数超出 1..100: %r' % raw)
            return None
        return value


@dataclass
class NormalizedCard:
    version: str
    properties: list[Property]
    raw: bytes
    index: int
    digest: str
    warnings: list[str] = field(default_factory=list)
    clientpidmaps: list[tuple[str, str]] = field(default_factory=list)

    def get(self, name: str) -> list[Property]:
        return [prop for prop in self.properties if prop.name == name]

    @property
    def uid(self) -> str | None:
        values = self.get('UID')
        return values[0].uri or values[0].text if values else None

    @property
    def kind(self) -> str:
        values = self.get('KIND')
        if not values:
            return 'individual'
        return (values[0].text or 'individual').lower()

    @property
    def rev(self) -> str | None:
        values = self.get('REV')
        if not values:
            return None
        return values[0].text or values[0].uri


@dataclass
class Quarantine:
    """无法支持的名片：只留存原件与原因，绝不产出联系人字段。"""
    index: int
    raw: bytes
    reason: str
    detail: str
    line_no: int | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


@dataclass
class ParseResult:
    cards: list[NormalizedCard] = field(default_factory=list)
    quarantines: list[Quarantine] = field(default_factory=list)


def unfold(data: bytes) -> bytes:
    """按 RFC 6350/2425 字节规则展开折行。

    折行 = CRLF + 单个 WSP；该 WSP 是传输时插入的折行字符而非内容，
    因此与 CRLF 一并删除。若其后还有第二个 WSP，则属于原内容，保留。
    """
    return FOLD_RE.sub(b'', data)


def logical_lines(data: bytes):
    """把原件切成逻辑行，同时返回每行在原始字节中的区间（含被展开的折行）。"""
    lines = []
    current = None
    current_start = 0
    previous_was_crlf = False
    i = 0
    n = len(data)
    while i < n:
        j = i
        while j < n and data[j] != 0x0d and data[j] != 0x0a:
            j += 1
        if j == n:
            chunk = data[i:j]
            if current is None:
                if chunk:
                    lines.append((chunk, i, j))
            else:
                current.extend(chunk)
                lines.append((bytes(current), current_start, n))
            break
        if data[j] == 0x0d and j + 1 < n and data[j + 1] == 0x0a:
            skip, is_crlf = 2, True
        else:
            skip, is_crlf = 1, False
        physical = data[i:j]
        # 折行续行必须是 CRLF 后紧跟空格/制表符；孤立 LF 的 WSP 不算折行。
        # 该首个 WSP 是折行字符，按字节规则丢弃；同行其余 WSP 属于内容。
        if previous_was_crlf and current is not None and physical[:1] in (b' ', b'\t'):
            current.extend(physical[1:])
        else:
            if current is not None:
                lines.append((bytes(current), current_start, i))
            current = bytearray(physical)
            current_start = i
        previous_was_crlf = is_crlf
        i = j + skip
    if current is not None:
        lines.append((bytes(current), current_start, n))
    return lines


def _split_top_level(text: bytes, delimiters: frozenset[int]) -> list[bytes]:
    """在双引号保护之外按分隔符切分，用于参数段解析。"""
    parts = []
    start = 0
    quoted = False
    for i, byte in enumerate(text):
        if byte == 0x22:
            quoted = not quoted
        elif not quoted and byte in delimiters:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def parse_content_line(line: bytes, line_no: int) -> tuple[str | None, str | None, dict, bytes]:
    """解析 contentline = [group "."] name *(";" param) ":" value。

    全程在字节上按引号状态机推进，值部分原样保留，不在此处切分号。
    """
    head_end = len(line)
    quoted = False
    for i, byte in enumerate(line):
        if byte == 0x22:
            quoted = not quoted
        elif not quoted and byte in (0x3b, 0x3a):  # ';' or ':'
            head_end = i
            break
    head = line[:head_end]
    params: dict[str, list[str]] = {}
    group = None
    if b'.' in head and b':' not in head:
        group_bytes, name_bytes = head.split(b'.', 1)
        group = group_bytes.decode('ascii')
    else:
        name_bytes = head
    try:
        name = name_bytes.decode('ascii').upper()
    except UnicodeDecodeError:
        raise _LineError('invalid-parameter-encoding', '属性名不是 ASCII', line_no)
    rest = line[head_end:]
    while rest.startswith(b';'):
        rest = rest[1:]
        segment_end = len(rest)
        quoted = False
        for i, byte in enumerate(rest):
            if byte == 0x22:
                quoted = not quoted
            elif not quoted and byte in (0x3b, 0x3a):
                segment_end = i
                break
        segment = rest[:segment_end]
        rest = rest[segment_end:]
        if b'=' in segment:
            key_bytes, raw_value = segment.split(b'=', 1)
            try:
                key = key_bytes.decode('ascii').lower()
            except UnicodeDecodeError:
                raise _LineError('invalid-parameter-encoding', '参数名不是 ASCII', line_no)
            value = _decode_param_value(raw_value, line_no)
            params.setdefault(key, []).append(value)
        elif segment:
            params.setdefault(segment.decode('ascii', 'replace').lower(), []).append('')
    if not rest.startswith(b':'):
        raise _LineError('malformed-line', '缺少属性值冒号', line_no)
    return name, group, params, rest[1:]


def _decode_param_value(raw: bytes, line_no: int) -> str:
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError:
        raise _LineError('invalid-parameter-encoding', '参数值不是 ASCII', line_no)
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


class _LineError(Exception):
    def __init__(self, reason, detail, line_no):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.line_no = line_no


def _unescape(text: str, version: str) -> tuple[str, list[str]]:
    out = []
    warnings: list[str] = []
    escaped = False
    for char in text:
        if not escaped:
            if char == '\\':
                escaped = True
            else:
                out.append(char)
            continue
        if char in ('n', 'N'):  # 4.0 允许两种大小写，3.0 只定义 \\n
            out.append('\n')
        elif char in ('\\', ';', ','):
            out.append(char)
        else:
            warnings.append('未知转义序列 \\%s 按字面量保留' % char)
            out.append('\\' + char)
        escaped = False
    if escaped:
        warnings.append('值以孤立反斜杠结尾，按字面量保留')
        out.append('\\')
    return ''.join(out), warnings


def split_structured(value: str) -> list[list[str]]:
    """只在未转义的 ; / , 上切分复合值；转义字符原样保留给反转义阶段。"""
    components: list[list[str]] = [[]]
    token = []
    escaped = False
    for char in value:
        if escaped:
            token.append(char)
            escaped = False
            continue
        if char == '\\':
            escaped = True
            token.append(char)
        elif char == ';':
            components[-1].append(''.join(token))
            components.append([])
            token = []
        elif char == ',':
            components[-1].append(''.join(token))
            token = []
        else:
            token.append(char)
    components[-1].append(''.join(token))
    return components


def _structured(value: str, version: str) -> list[list[str]]:
    result = []
    warnings: list[str] = []
    for component in split_structured(value):
        decoded = []
        for item in component:
            text, item_warnings = _unescape(item, version)
            decoded.append(text)
            warnings.extend(item_warnings)
        result.append(decoded)
    return result, warnings


def _detect_encoding_quarantine(name: str, params: dict, line_no: int) -> Quarantine | None:
    encoding_values = [value.upper() for value in params.get('encoding', [])]
    if encoding_values:
        return Quarantine(
            index=-1, raw=b'', reason='unsupported-encoding',
            detail='属性 %s 声明了未支持的 ENCODING=%s（quoted-printable/内联媒体须隔离）'
                   % (name, ','.join(encoding_values)), line_no=line_no)
    charset = (params.get('charset', [''])[0] or '').upper()
    if charset not in UTF8_ALIASES:
        return Quarantine(
            index=-1, raw=b'', reason='unsupported-charset',
            detail='属性 %s 声明了未支持的 CHARSET=%s，仅接收 UTF-8' % (name, charset),
            line_no=line_no)
    return None


def _decode_value(value: bytes, charset: str, line_no: int) -> str:
    try:
        return value.decode('utf-8')
    except UnicodeDecodeError as error:
        raise _LineError('invalid-utf8', 'UTF-8 解码失败: %s' % error.reason, line_no)


def _build_property(name: str, group: str | None, params: dict, value_bytes: bytes,
                    version: str, line_no: int) -> Property:
    charset = (params.get('charset', [''])[0] or '').upper()
    text_value = _decode_value(value_bytes, charset, line_no)
    prop = Property(name=name, group=group, params=params,
                    value_bytes=value_bytes, line_no=line_no)
    if version == '4.0' and name in URI_PROPERTIES_V4:
        prop.uri = text_value
        return prop
    if name in STRUCTURED_TEXT:
        prop.components, component_warnings = _structured(text_value, version)
        prop.warnings.extend(component_warnings)
        expected = STRUCTURED_TEXT[name]
        if name == 'GEO' and version == '4.0':
            prop.uri = text_value
            prop.components = None
        elif len(prop.components) != expected:
            prop.warnings.append('%s 应有 %d 个分号分量，实际 %d 个'
                                 % (name, expected, len(prop.components)))
        return prop
    if name in TEXT_LIST_PROPERTIES:
        prop.components, component_warnings = _structured(text_value, version)
        prop.warnings.extend(component_warnings)
        return prop
    if name in FLAT_TEXT_PROPERTIES or name in {'REV', 'BDAY', 'ANNIVERSARY', 'KIND'}:
        prop.text, prop.warnings = _unescape(text_value, version)
        return prop
    # 3.0 的 TEL/GEO 等是文本型；未知属性保留平面文本并告警，不丢弃。
    prop.text, text_warnings = _unescape(text_value, version)
    prop.warnings.extend(text_warnings)
    prop.warnings.append('未登记属性类型，按平面文本保留: %s' % name)
    return prop


def parse_cards(data: bytes) -> ParseResult:
    """解析整个 vCard 字节流，返回名片与隔离记录；结构性错误直接抛 ValueError。"""
    result = ParseResult()
    lines = logical_lines(data)
    card_index = -1
    card_lines: list[tuple] | None = None
    card_start = card_end = 0
    pending_quarantine: Quarantine | None = None

    def finish_card(end_offset: int):
        nonlocal card_lines, pending_quarantine, card_start, card_end
        if card_lines is None:
            return
        raw = data[card_start:end_offset]
        if pending_quarantine is not None:
            pending_quarantine.raw = raw
            pending_quarantine.index = card_index
            result.quarantines.append(pending_quarantine)
        else:
            try:
                result.cards.append(_build_card(raw, card_index, card_lines))
            except _LineError as error:
                result.quarantines.append(Quarantine(
                    index=card_index, raw=raw, reason=error.reason,
                    detail=error.detail, line_no=error.line_no))
        card_lines = None
        pending_quarantine = None

    for ordinal, (line_bytes, raw_start, raw_end) in enumerate(lines, start=1):
        if not line_bytes:
            continue
        upper = line_bytes.upper()
        if upper == b'BEGIN:VCARD':
            if card_lines is not None:
                raise ValueError('名片不允许嵌套（第 %d 行）' % ordinal)
            card_index += 1
            card_lines = []
            card_start, card_end = raw_start, raw_end
            continue
        if upper == b'END:VCARD':
            if card_lines is None:
                raise ValueError('缺少名片起始（第 %d 行）' % ordinal)
            card_end = raw_end
            finish_card(raw_end)
            continue
        if card_lines is None:
            continue  # 名片外的杂行不参与语义
        card_lines.append((line_bytes, ordinal))
        if pending_quarantine is not None:
            continue
        try:
            name, group, params, value_bytes = parse_content_line(line_bytes, ordinal)
        except _LineError as error:
            pending_quarantine = Quarantine(
                index=card_index, raw=b'', reason=error.reason,
                detail=error.detail, line_no=error.line_no)
            continue
        blocked = _detect_encoding_quarantine(name, params, ordinal)
        if blocked is not None:
            pending_quarantine = blocked
            continue
        if name == 'VERSION':
            version = value_bytes.decode('ascii', 'replace').strip()
            if version not in SUPPORTED_VERSIONS:
                pending_quarantine = Quarantine(
                    index=card_index, raw=b'', reason='unsupported-version',
                    detail='仅支持 vCard 3.0/4.0，收到 VERSION=%s' % version,
                    line_no=ordinal)
            continue
    if card_lines is not None:
        raise ValueError('名片不完整或为空：缺少 END:VCARD')
    if not result.cards and not result.quarantines:
        raise ValueError('名片不完整或为空')
    return result


def _build_card(raw: bytes, index: int, card_lines: list[tuple]) -> NormalizedCard:
    version = '4.0'
    declared_version = False
    properties: list[Property] = []
    warnings: list[str] = []
    clientpidmaps: list[tuple[str, str]] = []
    for line_bytes, line_no in card_lines:
        name, group, params, value_bytes = parse_content_line(line_bytes, line_no)
        if name == 'VERSION':
            version = value_bytes.decode('ascii', 'replace').strip()
            declared_version = True
            continue
        prop = _build_property(name, group, params, value_bytes, version, line_no)
        if name == 'CLIENTPIDMAP':
            mapping = split_structured(prop.text or '')
            flat = [item for component in mapping for item in component]
            if len(flat) >= 2:
                clientpidmaps.append((flat[0].strip(), flat[1].strip()))
            else:
                prop.warnings.append('CLIENTPIDMAP 分量不足: %r' % prop.text)
        warnings.extend(prop.warnings)
        properties.append(prop)
    if not declared_version:
        warnings.append('缺少 VERSION，按 vCard %s 兼容处理' % version)
    return NormalizedCard(version=version, properties=properties, raw=raw, index=index,
                          digest=hashlib.sha256(raw).hexdigest(),
                          warnings=warnings, clientpidmaps=clientpidmaps)


def card_to_dict(card: NormalizedCard) -> dict:
    """供 CLI/检查输出的可序列化视图。"""
    props = []
    for prop in card.properties:
        props.append({
            'name': prop.name,
            'group': prop.group,
            'line_no': prop.line_no,
            'params': prop.params,
            'altid': prop.altid,
            'language': prop.language,
            'types': prop.types,
            'pref': prop.pref,
            'pids': prop.pids,
            'text': prop.text,
            'uri': prop.uri,
            'components': prop.components,
            'warnings': prop.warnings,
        })
    return {
        'index': card.index,
        'version': card.version,
        'uid': card.uid,
        'kind': card.kind,
        'rev': card.rev,
        'digest': card.digest,
        'clientpidmaps': card.clientpidmaps,
        'warnings': card.warnings,
        'properties': props,
    }
