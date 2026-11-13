import hashlib
import json
import sqlite3

from directory import Directory
from vcard import parse_cards

class Archive:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript(
            'CREATE TABLE IF NOT EXISTS captures('
            'id INTEGER PRIMARY KEY, source TEXT NOT NULL, digest TEXT NOT NULL,'
            'raw BLOB NOT NULL, rows_json TEXT NOT NULL, UNIQUE(source,digest));')

    def save(self, source, raw, rows):
        if not source.strip():
            raise ValueError('来源标识不能为空')
        digest = hashlib.sha256(raw).hexdigest()
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        with self.connection:
            self.connection.execute(
                'INSERT OR IGNORE INTO captures(source,digest,raw,rows_json) VALUES(?,?,?,?)',
                (source, digest, raw, payload))
            record = self.connection.execute(
                'SELECT id FROM captures WHERE source=? AND digest=?', (source, digest)).fetchone()
        return record[0]

    def list(self):
        return [dict(id=row[0], source=row[1], digest=row[2], rows=json.loads(row[3]))
                for row in self.connection.execute(
                    'SELECT id,source,digest,rows_json FROM captures ORDER BY id')]

    def raw(self, capture_id):
        row = self.connection.execute('SELECT raw FROM captures WHERE id=?', (capture_id,)).fetchone()
        if row is None:
            raise KeyError(capture_id)
        return bytes(row[0])

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Ledger:
    """规范化接收的持久账本：原件留存、隔离件、目录快照与属性级决策同库。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS captures(
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, digest TEXT NOT NULL,
        raw BLOB NOT NULL, rows_json TEXT NOT NULL, UNIQUE(source,digest)
    );
    CREATE TABLE IF NOT EXISTS ledger_meta(
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS quarantines(
        id INTEGER PRIMARY KEY,
        capture_id INTEGER NOT NULL,
        card_index INTEGER NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT NOT NULL,
        line_no INTEGER,
        digest TEXT NOT NULL,
        raw BLOB NOT NULL
    );
    CREATE TABLE IF NOT EXISTS contacts(
        uid TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS decisions(
        seq INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        source TEXT NOT NULL,
        uid TEXT NOT NULL,
        kind TEXT NOT NULL,
        action TEXT NOT NULL,
        reason TEXT NOT NULL,
        attr_id TEXT,
        property TEXT,
        member_ref TEXT,
        detail_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS suppression(
        uid TEXT NOT NULL,
        attr_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY(uid, attr_id)
    );
    """

    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript(self.SCHEMA)

    # ---- 接收 -----------------------------------------------------------
    def ingest(self, source, raw):
        """原件先落库，再解析；隔离件原样留存并返回可检查结果。"""
        parsed = parse_cards(raw)
        with self.connection:
            digest = hashlib.sha256(raw).hexdigest()
            self.connection.execute(
                'INSERT OR IGNORE INTO captures(source,digest,raw,rows_json) VALUES(?,?,?,?)',
                (source, digest, raw, json.dumps([], ensure_ascii=False)))
            capture_id = self.connection.execute(
                'SELECT id FROM captures WHERE source=? AND digest=?', (source, digest)
            ).fetchone()[0]
            quarantine_ids = []
            for item in parsed.quarantines:
                cursor = self.connection.execute(
                    'INSERT INTO quarantines(capture_id,card_index,reason,detail,line_no,digest,raw)'
                    ' VALUES(?,?,?,?,?,?,?)',
                    (capture_id, item.index, item.reason, item.detail, item.line_no,
                     item.digest, raw))
                quarantine_ids.append(cursor.lastrowid)
        directory = self.load_directory()
        for card in parsed.cards:
            directory.ingest_card(card, source=source, capture_id=capture_id)
        for item in parsed.quarantines:
            directory.record_quarantine(item, source=source, capture_id=capture_id)
        self._save_directory(directory)
        return {'capture_id': capture_id,
                'cards': len(parsed.cards),
                'quarantines': quarantine_ids}

    # ---- 人工屏蔽 -------------------------------------------------------
    def suppress(self, uid, attr_id, reason):
        directory = self.load_directory()
        directory.suppress(uid, attr_id, reason)
        with self.connection:
            self.connection.execute(
                'INSERT OR REPLACE INTO suppression(uid,attr_id,reason,at) VALUES(?,?,?,?)',
                (uid, attr_id, reason, directory.decisions[-1].at))
        self._save_directory(directory)
        return directory.decisions[-1].to_dict()

    def unsuppress(self, uid, attr_id):
        directory = self.load_directory()
        directory.unsuppress(uid, attr_id)
        with self.connection:
            self.connection.execute(
                'DELETE FROM suppression WHERE uid=? AND attr_id=?', (uid, attr_id))
        self._save_directory(directory)
        return directory.decisions[-1].to_dict()

    # ---- 读取 -----------------------------------------------------------
    def load_directory(self):
        from directory import Attribute, Contact, Decision, MemberRef, Provenance
        row = self.connection.execute(
            "SELECT value_json FROM ledger_meta WHERE key='snapshot'").fetchone()
        directory = Directory()
        if row is None:
            return directory
        snapshot = json.loads(row[0])
        for uid, data in snapshot['contacts'].items():
            contact = Contact(uid=uid, kind=data['kind'],
                              latest_rev_by_source=data['latest_rev_by_source'],
                              clientpidmaps_by_source=data['clientpidmaps_by_source'])
            for attr_id, attr_data in data['attributes'].items():
                sources = {name: Provenance(**claim)
                           for name, claim in attr_data.pop('sources').items()}
                pids = attr_data.pop('pids_by_source')
                attr = Attribute(**attr_data)
                attr.sources = sources
                attr.pids_by_source = pids
                contact.attributes[attr_id] = attr
            for target, member_data in data['members'].items():
                sources = {name: Provenance(**claim)
                           for name, claim in member_data.pop('sources').items()}
                member = MemberRef(**member_data)
                member.sources = sources
                contact.members[target] = member
            directory.contacts[uid] = contact
        for item in snapshot['decisions']:
            directory.decisions.append(Decision(**item))
        directory.quarantines = list(snapshot['quarantines'])
        directory._seq = len(directory.decisions)
        return directory

    def _save_directory(self, directory):
        snapshot = json.dumps(directory.to_dict(), ensure_ascii=False, sort_keys=True)
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO ledger_meta(key,value_json) VALUES('snapshot',?)",
                (snapshot,))
            self.connection.execute('DELETE FROM decisions')
            self.connection.executemany(
                'INSERT INTO decisions(seq,at,source,uid,kind,action,reason,'
                'attr_id,property,member_ref,detail_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                [(d.seq, d.at, d.source, d.uid, d.kind, d.action, d.reason,
                  d.attr_id, d.property, d.member_ref,
                  json.dumps(d.detail, ensure_ascii=False, sort_keys=True))
                 for d in directory.decisions])
            self.connection.execute('DELETE FROM contacts')
            from directory import utc_now
            self.connection.executemany(
                'INSERT INTO contacts(uid,kind,snapshot_json,updated_at) VALUES(?,?,?,?)',
                [(uid, contact.kind,
                  json.dumps(contact.to_dict(), ensure_ascii=False, sort_keys=True),
                  utc_now()) for uid, contact in directory.contacts.items()])

    def list_quarantines(self):
        return [dict(id=row[0], capture_id=row[1], card_index=row[2], reason=row[3],
                     detail=row[4], line_no=row[5], digest=row[6])
                for row in self.connection.execute(
                    'SELECT id,capture_id,card_index,reason,detail,line_no,digest'
                    ' FROM quarantines ORDER BY id')]

    def quarantine_raw(self, quarantine_id):
        row = self.connection.execute(
            'SELECT raw FROM quarantines WHERE id=?', (quarantine_id,)).fetchone()
        if row is None:
            raise KeyError(quarantine_id)
        return bytes(row[0])

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
