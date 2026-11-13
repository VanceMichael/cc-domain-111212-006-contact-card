"""多来源 vCard 的属性级合并引擎（纯内存、可序列化）。

核心纪律：
- 身份只由 UID + 属性语义键决定；PID/CLIENTPIDMAP 仅按来源语境留存，不参与身份。
- ALTID + LANGUAGE 是同一属性的语言变体，变体并列保留，绝不互相覆盖。
- 重复电话/地址合并类型与偏好，全部来源的逐次声明都留在 provenance 里可检查。
- 某来源的新 REV 只能撤回该来源独占的属性，跨来源属性永不被整卡覆盖删除。
- 陈旧 REV 不能覆盖新值；人工屏蔽按属性身份持久，重导旧卡只会得到 blocked 决策。
- 组的 MEMBER 可以前向引用尚未出现的 UID，人（individual）与组（group）严格分开。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import re

from vcard import NormalizedCard, Property, Quarantine

MODELED_PROPERTIES = {
    'FN', 'N', 'NICKNAME', 'PHOTO', 'BDAY', 'ANNIVERSARY', 'TITLE', 'ROLE',
    'ORG', 'EMAIL', 'TEL', 'ADR', 'LANG', 'TZ', 'GEO', 'NOTE', 'URL',
    'IMPP', 'CATEGORIES', 'SOURCE', 'KIND', 'XML',
}
ACTIONS = (
    'added', 'variant-added', 'updated', 'variant-updated', 'duplicate-merged',
    'variant-conflict', 'restored', 'suppressed-blocked', 'stale-rev-ignored',
    'withdrawn', 'source-claim-removed', 'member-added', 'member-resolved',
    'member-on-non-group', 'kind-conflict', 'quarantined', 'pid-recorded',
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_rev(value: str | None):
    """把 3.0/4.0 的 REV 变成可比较的 aware datetime；无法判断时返回 None。"""
    if not value:
        return None
    text = value.strip()
    basic = re.fullmatch(r'(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?', text)
    if basic:
        year, month, day, hour, minute, second, zed = basic.groups()
        text = '%s-%s-%s' % (year, month, day)
        if hour is not None:
            text += 'T%s:%s:%s%s' % (hour, minute, second, '+00:00' if zed else '')
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _digest(parts) -> str:
    return hashlib.sha256('\x1f'.join(str(part) for part in parts).encode('utf8')).hexdigest()[:16]


def normalize_member_ref(ref: str) -> str:
    text = ref.strip()
    lower = text.lower()
    if lower.startswith('urn:uuid:'):
        return 'urn:uuid:' + text[len('urn:uuid:'):].lower()
    return text


def value_key(prop: Property) -> str:
    """属性语义键：同一联系方式的不同写法应在此归一，分号转义已在解析层处理。"""
    if prop.name == 'TEL':
        raw = (prop.uri or prop.text or '').strip()
        if raw.lower().startswith('tel:'):
            raw = raw[4:]
        main = raw.split(';', 1)[0]  # 仅拆 RFC 3966 的电话参数，不拆 vCard 转义
        digits = re.sub(r'\D', '', main)
        return 'tel:' + digits if digits else 'tel:' + main.lower()
    if prop.name == 'EMAIL':
        return 'email:' + (prop.text or '').strip().lower()
    if prop.name == 'ADR':
        flat = ['|'.join(part.strip().lower() for part in component)
                for component in (prop.components or [])]
        return 'adr:' + ';'.join(flat)
    if prop.components is not None:
        flat = ['|'.join(part.strip() for part in component)
                for component in prop.components]
        return prop.name.lower() + ':' + ';'.join(flat)
    if prop.uri is not None:
        return prop.name.lower() + ':' + prop.uri.strip()
    return prop.name.lower() + ':' + (prop.text or '').strip()


def display_of(prop: Property) -> str:
    if prop.components is not None:
        return ';'.join(','.join(part for part in component) for component in prop.components)
    return prop.uri if prop.uri is not None else (prop.text or '')


@dataclass
class Provenance:
    source: str
    capture_id: int
    digest: str
    rev: str | None
    line_no: int
    received_at: str
    types: list[str]
    pref: int | None
    altid: str | None
    language: str | None
    pids: list[str]
    raw_value: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Attribute:
    attr_id: str
    property: str
    value_key: str
    display: str
    altid: str | None
    language: str | None
    family: str | None
    types: list[str] = field(default_factory=list)
    pref: int | None = None
    sources: dict[str, Provenance] = field(default_factory=dict)
    pids_by_source: dict[str, list[str]] = field(default_factory=dict)
    # 来源间对同一变体给出不同值时，被保留现值之外的原值在此登记，供人工裁决。
    conflicts: list[dict] = field(default_factory=list)
    status: str = 'active'            # active / withdrawn / suppressed
    suppress_reason: str | None = None
    suppressed_at: str | None = None

    def to_dict(self):
        data = asdict(self)
        data['sources'] = {name: claim.to_dict() for name, claim in self.sources.items()}
        return data


@dataclass
class MemberRef:
    ref: str
    target_uid: str
    sources: dict[str, Provenance] = field(default_factory=dict)
    status: str = 'pending'           # pending / resolved

    def to_dict(self):
        data = asdict(self)
        data['sources'] = {name: claim.to_dict() for name, claim in self.sources.items()}
        return data


@dataclass
class Contact:
    uid: str
    kind: str
    attributes: dict[str, Attribute] = field(default_factory=dict)
    members: dict[str, MemberRef] = field(default_factory=dict)
    latest_rev_by_source: dict[str, str] = field(default_factory=dict)
    clientpidmaps_by_source: dict[str, list] = field(default_factory=dict)

    def to_dict(self):
        return {
            'uid': self.uid,
            'kind': self.kind,
            'attributes': {key: value.to_dict() for key, value in self.attributes.items()},
            'members': {key: value.to_dict() for key, value in self.members.items()},
            'latest_rev_by_source': self.latest_rev_by_source,
            'clientpidmaps_by_source': self.clientpidmaps_by_source,
        }


@dataclass
class Decision:
    seq: int
    at: str
    source: str
    uid: str
    kind: str
    action: str
    reason: str
    attr_id: str | None = None
    property: str | None = None
    member_ref: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Directory:
    def __init__(self):
        self.contacts: dict[str, Contact] = {}
        self.decisions: list[Decision] = []
        self.quarantines: list[dict] = []
        self._seq = 0

    # ---- 内部工具 -------------------------------------------------------
    def _decision(self, source, uid, kind, action, reason, **detail):
        self._seq += 1
        decision = Decision(seq=self._seq, at=utc_now(), source=source, uid=uid,
                            kind=kind, action=action, reason=reason, **detail)
        self.decisions.append(decision)
        return decision

    def _contact_for(self, card: NormalizedCard, source: str) -> Contact | None:
        uid = card.uid
        if not uid:
            self._decision(source, '', card.kind, 'quarantined', '名片缺少 UID，无法归入联系人')
            return None
        contact = self.contacts.get(uid)
        if contact is not None and contact.kind != card.kind:
            self._decision(source, uid, card.kind, 'kind-conflict',
                           'UID 已登记为 %s，拒绝按 %s 混并' % (contact.kind, card.kind),
                           detail={'existing_kind': contact.kind, 'incoming_kind': card.kind})
            return None
        if contact is None:
            contact = Contact(uid=uid, kind=card.kind)
            self.contacts[uid] = contact
        return contact

    def _find_variant(self, contact: Contact, prop: Property):
        """同一 ALTID 家族内按语言匹配变体；没有 ALTID 时退回语义键匹配。"""
        if prop.altid:
            family = _digest((contact.uid, prop.name, prop.altid))
            for attr in contact.attributes.values():
                if attr.family == family and attr.language == (prop.language or ''):
                    return attr, family
            return None, family
        for attr in contact.attributes.values():
            if attr.property == prop.name and attr.value_key == value_key(prop):
                return attr, attr.family
        return None, None

    def _find_semantic(self, contact: Contact, prop: Property):
        key = value_key(prop)
        for attr in contact.attributes.values():
            if attr.property == prop.name and attr.value_key == key:
                return attr
        return None

    # ---- 接收入口 -------------------------------------------------------
    def ingest_card(self, card: NormalizedCard, source: str, capture_id: int):
        contact = self._contact_for(card, source)
        if contact is None:
            return
        kind = contact.kind
        new_rev_text = card.rev
        new_rev = parse_rev(new_rev_text)
        previous_rev_text = contact.latest_rev_by_source.get(source)
        previous_rev = parse_rev(previous_rev_text)
        # 新卡 REV 不新于该来源已接收的 REV（重导旧卡或同 REV 卡），整卡都不能覆盖。
        stale = bool(new_rev and previous_rev and new_rev <= previous_rev)
        newer = bool(new_rev and (previous_rev is None or new_rev > previous_rev))
        if stale:
            self._decision(source, contact.uid, contact.kind, 'stale-rev-ignored',
                           '整卡 REV=%s 不新于来源已接收 REV=%s，拒绝任何覆盖'
                           % (new_rev_text, previous_rev_text),
                           detail={'card_digest': card.digest})
            return
        if new_rev is None and previous_rev is not None:
            # 来源历史由带 REV 的卡建立后，无时间戳的重导不能当作新版本。
            self._decision(source, contact.uid, contact.kind, 'stale-rev-ignored',
                           '新卡缺少可解析 REV，来源已有 REV=%s，拒绝覆盖'
                           % previous_rev_text, detail={'card_digest': card.digest})
            return

        if card.clientpidmaps:
            contact.clientpidmaps_by_source[source] = card.clientpidmaps
            self._decision(source, contact.uid, kind, 'pid-recorded',
                           'CLIENTPIDMAP 仅在来源 %s 的 PID 命名空间内留存，不参与跨来源身份' % source,
                           detail={'clientpidmaps': card.clientpidmaps})

        touched: set[str] = set()
        for prop in card.properties:
            if prop.name in ('VERSION', 'CLIENTPIDMAP', 'UID', 'KIND', 'REV', 'MEMBER'):
                continue
            if prop.name not in MODELED_PROPERTIES:
                continue
            self._merge_property(contact, prop, source, capture_id, card, stale, touched)

        for prop in card.get('MEMBER'):
            self._merge_member(contact, prop, source, capture_id, card)

        if not stale:
            contact.latest_rev_by_source[source] = new_rev_text or previous_rev_text or ''

        # 只有“带 REV 且确实更新”的卡可以撤回本来源独占的旧属性。
        if newer and not stale:
            self._withdraw_missing(contact, source, kind, touched)

        self.resolve_all_members()

    def resolve_all_members(self):
        """遍历所有组，补齐此前前向挂起、现在 UID 已到达的成员。"""
        for group in self.contacts.values():
            if group.kind != 'group':
                continue
            for member in group.members.values():
                if member.status == 'pending' and member.target_uid in self.contacts:
                    member.status = 'resolved'
                    target = self.contacts[member.target_uid]
                    self._decision(','.join(sorted(member.sources)), group.uid, 'group',
                                   'member-resolved', '前向引用的 UID 已到达，组关系补齐',
                                   member_ref=member.ref,
                                   detail={'target_kind': target.kind})

    def _merge_property(self, contact: Contact, prop: Property, source: str,
                        capture_id: int, card: NormalizedCard, stale: bool,
                        touched: set[str]):
        kind = contact.kind
        claim = Provenance(
            source=source, capture_id=capture_id, digest=card.digest, rev=card.rev,
            line_no=prop.line_no, received_at=utc_now(), types=prop.types,
            pref=prop.pref, altid=prop.altid, language=prop.language,
            pids=prop.pids, raw_value=display_of(prop))
        attr, family = self._find_variant(contact, prop)
        key = value_key(prop)

        if attr is None:
            semantic = self._find_semantic(contact, prop) if prop.altid else None
            if semantic is not None:
                attr = semantic  # 同值但首次带 ALTID：归入同一属性并建立家族
                attr.altid = prop.altid
                attr.family = family
            else:
                attr = Attribute(
                    attr_id=_digest((contact.uid, prop.name, key, prop.altid or '',
                                     prop.language or '')),
                    property=prop.name, value_key=key, display=display_of(prop),
                    altid=prop.altid, language=prop.language or '', family=family,
                    types=list(prop.types), pref=prop.pref)
                contact.attributes[attr.attr_id] = attr
                action = 'variant-added' if prop.altid else 'added'
                attr.sources[source] = claim
                if prop.pids:
                    attr.pids_by_source[source] = prop.pids
                touched.add(attr.attr_id)
                self._decision(source, contact.uid, kind, action,
                               '新属性并入联系人', attr_id=attr.attr_id,
                               property=prop.name,
                               detail={'value': attr.display, 'language': attr.language,
                                       'altid': attr.altid, 'types': attr.types,
                                       'pref': attr.pref})
                return

        touched.add(attr.attr_id)
        if attr.status == 'suppressed':
            # 屏蔽按属性身份生效：任何来源重导都不能复活，只追加可检查的来源声明。
            attr.sources[source] = claim
            self._decision(source, contact.uid, kind, 'suppressed-blocked',
                           '属性已被人工屏蔽，重导不能恢复', attr_id=attr.attr_id,
                           property=prop.name,
                           detail={'suppressed_at': attr.suppressed_at,
                                   'reason': attr.suppress_reason,
                                   'incoming_value': claim.raw_value})
            return
        if stale:
            self._decision(source, contact.uid, kind, 'stale-rev-ignored',
                           '卡 REV=%s 早于该来源已接收的 REV=%s，拒绝覆盖'
                           % (card.rev, contact.latest_rev_by_source.get(source)),
                           attr_id=attr.attr_id, property=prop.name,
                           detail={'existing_value': attr.display,
                                   'incoming_value': claim.raw_value})
            return

        before = attr.display
        same_value = attr.value_key == key
        attr.sources[source] = claim
        if prop.pids:
            attr.pids_by_source[source] = prop.pids
        merged_types = sorted(set(attr.types) | set(prop.types))
        attr.types = merged_types
        attr.pref = max(x for x in (attr.pref, prop.pref) if x is not None) \
            if (attr.pref or prop.pref) else None
        if attr.status == 'withdrawn':
            attr.status = 'active'
            action, reason = 'restored', '来源重新声明了曾撤回的属性'
        elif same_value:
            action, reason = 'duplicate-merged', '同值重复声明：合并类型与偏好，来源全部留存'
        elif prop.altid and len(attr.sources) > 1:
            # 不同来源对同一 ALTID/语言变体给不同值：来源之间不得互相覆盖显示值，
            # 保留现值，冲突来源原值登记到 conflicts 并留在 provenance，交人工裁决。
            attr.conflicts.append({'source': source, 'raw_value': claim.raw_value,
                                   'digest': claim.digest, 'received_at': claim.received_at})
            action, reason = 'variant-conflict', (
                '不同来源对同一语言变体给出不同值，保留现值并留存冲突来源原值')
        else:
            attr.display = display_of(prop)
            attr.value_key = key
            action = 'variant-updated' if prop.altid else 'updated'
            reason = '同语言变体/属性被来源更新'
        self._decision(source, contact.uid, kind, action, reason,
                       attr_id=attr.attr_id, property=prop.name,
                       detail={'before': before, 'after': attr.display,
                               'incoming': claim.raw_value,
                               'types': attr.types, 'pref': attr.pref,
                               'language': attr.language, 'altid': attr.altid})

    def _withdraw_missing(self, contact: Contact, source: str, kind: str, touched: set[str]):
        for attr_id, attr in list(contact.attributes.items()):
            if source not in attr.sources or attr_id in touched:
                continue
            if attr.status == 'suppressed':
                continue  # 人工屏蔽不参与来源撤回
            if any(entry['source'] == source for entry in attr.conflicts):
                # 该来源在本属性的语言变体冲突中留有原值，撤回不清除冲突证据。
                self._decision(source, contact.uid, kind, 'source-claim-removed',
                               '该来源在语言变体冲突中留有原值，撤回不清除冲突证据',
                               attr_id=attr_id, property=attr.property,
                               detail={'kept_source': source})
                continue
            other_sources = [name for name in attr.sources if name != source]
            del attr.sources[source]
            attr.pids_by_source.pop(source, None)
            if other_sources:
                self._decision(source, contact.uid, kind, 'source-claim-removed',
                               '来源不再声明该属性，但其他来源仍持有，属性保留',
                               attr_id=attr_id, property=attr.property,
                               detail={'remaining_sources': other_sources})
            else:
                attr.status = 'withdrawn'
                self._decision(source, contact.uid, kind, 'withdrawn',
                               '新 REV 未携带该来源独占的属性，标记撤回（不物理删除）',
                               attr_id=attr_id, property=attr.property,
                               detail={'value': attr.display})

    def _merge_member(self, contact: Contact, prop: Property, source: str,
                      capture_id: int, card: NormalizedCard):
        if contact.kind != 'group':
            self._decision(source, contact.uid, contact.kind, 'member-on-non-group',
                           '非组名片上的 MEMBER 被忽略', member_ref=display_of(prop))
            return
        raw_ref = (prop.uri or prop.text or '').strip()
        target = normalize_member_ref(raw_ref)
        claim = Provenance(
            source=source, capture_id=capture_id, digest=card.digest, rev=card.rev,
            line_no=prop.line_no, received_at=utc_now(), types=prop.types,
            pref=prop.pref, altid=prop.altid, language=prop.language,
            pids=prop.pids, raw_value=raw_ref)
        member = contact.members.get(target)
        if member is None:
            member = MemberRef(ref=raw_ref, target_uid=target,
                               status='resolved' if target in self.contacts else 'pending')
            contact.members[target] = member
            self._decision(source, contact.uid, 'group', 'member-added',
                           '组成员已登记%s' % ('并解析到现有联系人' if member.status == 'resolved'
                                              else '，目标 UID 尚未出现，保持前向挂起'),
                           member_ref=raw_ref, detail={'status': member.status})
        member.sources[source] = claim

    def record_quarantine(self, item: Quarantine, source: str, capture_id: int):
        payload = {'source': source, 'capture_id': capture_id, 'index': item.index,
                   'reason': item.reason, 'detail': item.detail, 'line_no': item.line_no,
                   'digest': item.digest, 'at': utc_now()}
        self.quarantines.append(payload)
        self._decision(source, '', 'unknown', 'quarantined',
                       '%s: %s' % (item.reason, item.detail), detail=payload)

    # ---- 人工操作 -------------------------------------------------------
    def suppress(self, uid: str, attr_id: str, reason: str):
        contact = self.contacts.get(uid)
        if contact is None or attr_id not in contact.attributes:
            raise KeyError('属性不存在: %s/%s' % (uid, attr_id))
        attr = contact.attributes[attr_id]
        attr.status = 'suppressed'
        attr.suppress_reason = reason
        attr.suppressed_at = utc_now()
        self._decision('manual', uid, contact.kind, 'suppressed-blocked',
                       '人工屏蔽属性: %s' % reason, attr_id=attr_id, property=attr.property,
                       detail={'value': attr.display})

    def unsuppress(self, uid: str, attr_id: str):
        contact = self.contacts.get(uid)
        if contact is None or attr_id not in contact.attributes:
            raise KeyError('属性不存在: %s/%s' % (uid, attr_id))
        attr = contact.attributes[attr_id]
        if attr.status != 'suppressed':
            raise ValueError('属性未被屏蔽')
        attr.status = 'active' if attr.sources else 'withdrawn'
        attr.suppress_reason = None
        attr.suppressed_at = None
        self._decision('manual', uid, contact.kind, 'restored',
                       '人工解除屏蔽', attr_id=attr_id, property=attr.property)

    # ---- 视图 -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            'contacts': {uid: contact.to_dict() for uid, contact in self.contacts.items()},
            'decisions': [decision.to_dict() for decision in self.decisions],
            'quarantines': self.quarantines,
        }

    def active_contacts(self) -> list[dict]:
        rows = []
        for contact in self.contacts.values():
            attrs = [attr.to_dict() for attr in contact.attributes.values()
                     if attr.status != 'withdrawn']
            rows.append({'uid': contact.uid, 'kind': contact.kind, 'attributes': attrs,
                         'members': [member.to_dict() for member in contact.members.values()]})
        return rows
