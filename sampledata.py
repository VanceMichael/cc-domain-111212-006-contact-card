def samples():
    card = 'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:person:1\r\nFN:Lin\r\nFN;ALTID=1;LANGUAGE=zh:林经理\r\nFN;ALTID=1;LANGUAGE=en:Lin Manager\r\nEMAIL;PREF=1:lin@example.invalid\r\nADR;TYPE=work:;;Road\\; Building;City;;;\r\nCLIENTPIDMAP:1;urn:source:a\r\nTEL;PID=1.1;TYPE=work:tel:+86-0000\r\nEND:VCARD\r\n'
    group = 'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:group:1\r\nKIND:group\r\nFN:Team\r\nMEMBER:urn:person:1\r\nEND:VCARD\r\n'
    return {'sample.vcf': (group + card).encode(),
            'folded.vcf': card.replace('Lin', 'Li\r\n n', 1).encode(),
            'invalid.vcf': b'BEGIN:VCARD\r\nFN:unfinished'}
