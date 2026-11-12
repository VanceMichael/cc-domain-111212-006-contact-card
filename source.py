def inspect_bytes(data):
    text = data.decode('utf8')
    unfolded = text.replace('\r\n ', '').replace('\r\n\t', '')
    result = []
    current = None
    for line in unfolded.splitlines():
        if line == 'BEGIN:VCARD':
            if current is not None:
                raise ValueError('名片不允许嵌套')
            current = {'name': '', 'emails': []}
        elif line == 'END:VCARD':
            if current is None:
                raise ValueError('缺少名片起始')
            result.append(current)
            current = None
        elif current is not None and ':' in line:
            name, value = line.split(':', 1)
            if name == 'FN':
                current['name'] = value
            elif name.split(';')[0] == 'EMAIL':
                current['emails'].append(value)
    if current is not None or not result:
        raise ValueError('名片不完整或为空')
    return result
