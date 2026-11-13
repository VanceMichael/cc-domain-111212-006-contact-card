"""来源检查的既有入口（只暴露 FN/EMAIL 概览）。

完整的规范化接收在 `vcard.py` 与 `directory.py`；本模块保留旧检查契约，
内部改为复用字节级解析，避免两套折行/转义逻辑漂移。
"""

from vcard import parse_cards, card_to_dict


def inspect_bytes(data):
    parsed = parse_cards(data)  # 结构性错误与隔离件都不冒充成功接收
    if parsed.quarantines:
        item = parsed.quarantines[0]
        raise ValueError('存在无法接收的名片: %s %s' % (item.reason, item.detail))
    rows = []
    for card in parsed.cards:
        names = [prop.text for prop in card.get('FN') if prop.text]
        emails = [prop.text for prop in card.get('EMAIL') if prop.text]
        rows.append({'name': names[0] if names else '', 'emails': emails})
    return rows


def inspect_detail(data):
    """检查用的完整规范化视图，供人工核对，不参与旧契约断言。"""
    parsed = parse_cards(data)
    return {
        'cards': [card_to_dict(card) for card in parsed.cards],
        'quarantines': [
            {'index': item.index, 'reason': item.reason, 'detail': item.detail,
             'line_no': item.line_no, 'digest': item.digest}
            for item in parsed.quarantines
        ],
    }
