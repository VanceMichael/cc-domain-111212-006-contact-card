"""可重复的本地虚构数据。

原有的 sample/folded/invalid 三个夹具字节保持不变；新增夹具覆盖：
折行中文、带分号的地址、ALTID/LANGUAGE 双语变体、组成员前向引用、
vCard 3.0 接收、重复电话合并与不可编码件隔离。
"""


def samples():
    card = 'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:1\r\nFN:Lin\r\nFN;ALTID=1;LANGUAGE=zh:林经理\r\nFN;ALTID=1;LANGUAGE=en:Lin Manager\r\nEMAIL;PREF=1:lin@example.invalid\r\nADR;TYPE=work:;;Road\\; Building;City;;;\r\nCLIENTPIDMAP:1;urn:source:a\r\nTEL;PID=1.1;TYPE=work:tel:+86-0000\r\nEND:VCARD\r\n'
    group = 'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:group:1\r\nKIND:group\r\nFN:Team\r\nMEMBER:urn:person:1\r\nEND:VCARD\r\n'

    # 双语联系人：FN 与 ADR 都带 ALTID/LANGUAGE 变体；中文 FN 被折成三行。
    bilingual = (
        'BEGIN:VCARD\r\n'
        'VERSION:4.0\r\n'
        'UID:urn:person:2\r\n'
        'FN;ALTID=fn1;LANGUAGE=zh:深圳会展\r\n'
        ' 中心王经理\r\n'        # 折行 1：CRLF + 空格，WSP 属于内容
        '\t联络处\r\n'            # 折行 2：CRLF + 制表符
        'FN;ALTID=fn1;LANGUAGE=en:Manager Wang\r\n'
        'N;ALTID=n1;LANGUAGE=zh:王;经理;;;\r\n'
        'N;ALTID=n1;LANGUAGE=en:Wang;Manager;;;\r\n'
        'ADR;ALTID=adr1;LANGUAGE=zh;TYPE=work;PREF=1:;;福田路 88 号\\; A 座;深圳;广东;518000;中国\r\n'
        'ADR;ALTID=adr1;LANGUAGE=en;TYPE=work:;;88 Futian Rd\\; Tower A;Shenzhen;Guangdong;518000;China\r\n'
        'TEL;TYPE=work,voice;PREF=1:tel:+86-755-8888-0000\r\n'
        'TEL;TYPE=cell:tel:+86-138-0000-0000\r\n'
        'EMAIL;TYPE=work:wang@example.invalid\r\n'
        'LANG;PREF=1:zh-CN\r\n'
        'LANG:en\r\n'
        'REV:20260101T000000Z\r\n'
        'END:VCARD\r\n'
    )

    # 组先到、人后到：MEMBER 前向引用，补齐时仍保持 group/individual 区分。
    forward_group = (
        'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:group:forward\r\n'
        'KIND:group\r\nFN:双语接待组\r\n'
        'MEMBER:urn:person:2\r\nMEMBER:urn:person:3\r\nEND:VCARD\r\n'
    )
    forward_person = (
        'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:3\r\n'
        'FN;LANGUAGE=zh:陈联络\r\nEMAIL:chen@example.invalid\r\n'
        'END:VCARD\r\n'
    )

    # 来源 B 的同 UID 卡：同一电话号码的另一种写法应合并而不是新增，
    # 且其 REV 更旧，任何更新都必须被陈旧保护拒绝。
    source_b = (
        'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:2\r\n'
        'FN;ALTID=fn1;LANGUAGE=zh:旧名字不应覆盖\r\n'
        'TEL;TYPE=fax:tel:+8675588880000\r\n'
        'NOTE:旧卡备注\r\n'
        'REV:20250101T000000Z\r\n'
        'END:VCARD\r\n'
    )

    # 较新的同来源卡：撤回本来源独占 NOTE，但不能动其他来源的属性。
    source_b_new = (
        'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:2\r\n'
        'TEL;TYPE=fax:tel:+8675588880000\r\n'
        'TEL;TYPE=cell:tel:+86-138-0000-0000\r\n'
        'REV:20260202T000000Z\r\n'
        'END:VCARD\r\n'
    )

    # vCard 3.0：分号转义地址、TYPE 参数、3.0 风格文本 TEL 与时间戳 REV。
    v3_card = (
        'BEGIN:VCARD\r\nVERSION:3.0\r\n'
        'UID:urn:person:4\r\n'
        'FN:展商三号\r\nN:展;三号;;;\r\n'
        'ADR;TYPE=WORK,POSTAL:;;园区路 9 号\\; 二层;杭州;浙江;310000;中国\r\n'
        'TEL;TYPE=WORK,VOICE:+86-571-5555-6666\r\n'
        'EMAIL;TYPE=INTERNET,pref:three@example.invalid\r\n'
        'REV:2026-03-03T08:30:00Z\r\n'
        'END:VCARD\r\n'
    )

    # 无法支持的编码：quoted-printable 与非 UTF-8 字符集必须隔离原件，
    # 同流中的合法名片仍可正常接收。
    qp_card = (
        'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:urn:person:bad\r\n'
        'FN;ENCODING=quoted-printable:=B2=BB=B2=E2\r\n'
        'END:VCARD\r\n'
    ).encode('ascii')
    # 直接放置 GBK 字节，证明乱码不会被解码后冒充姓名。
    gbk_name = '展馆'.encode('gbk')
    charset_card = (
        b'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:urn:person:bad2\r\n'
        b'FN;CHARSET=GBK:' + gbk_name + b'\r\n'
        b'END:VCARD\r\n'
    )
    valid_after_quarantine = (
        'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:5\r\n'
        'FN:隔离流中的合法名片\r\nEND:VCARD\r\n'
    ).encode('utf-8')
    unsupported = qp_card + charset_card + valid_after_quarantine

    return {
        'sample.vcf': (group + card).encode(),
        'folded.vcf': card.replace('Lin', 'Li\r\n n', 1).encode(),
        'invalid.vcf': b'BEGIN:VCARD\r\nFN:unfinished',
        'bilingual.vcf': bilingual.encode(),
        'forward-group.vcf': forward_group.encode(),
        'forward-person.vcf': forward_person.encode(),
        'source-b.vcf': source_b.encode(),
        'source-b-new.vcf': source_b_new.encode(),
        'vcard3.vcf': v3_card.encode(),
        'unsupported-encoding.vcf': unsupported,
    }
