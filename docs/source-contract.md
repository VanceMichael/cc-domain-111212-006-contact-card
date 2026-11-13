# 来源格式与业务语义

## 接收范围

来源支持 UTF-8 的 vCard 3.0（RFC 2425/2426）与 vCard 4.0（RFC 6350）非二进制属性。
声明 `ENCODING=quoted-printable`、非 UTF-8 `CHARSET`（如 GBK）、内联媒体或无法做 UTF-8
解码的名片不产生联系人，整卡进入隔离表并保留原始字节，通过 `quarantine-list` /
`quarantine-raw` 检查与导出，乱码不会被解码后冒充姓名。VERSION 不是 3.0/4.0 的名片同样隔离。

## 解析纪律（`vcard.py`）

处理顺序固定，任何一步都不先按分号切值再补救：

1. **字节级折行展开**：折行是 CRLF 后紧跟单个空格/制表符；该 WSP 是折行字符，与 CRLF
   一并按字节删除，其后第二个 WSP 才属于内容。孤立 LF 后的空白不算折行。
2. **名片边界**：`BEGIN/END:VCARD` 大小写不敏感，嵌套或缺结束符直接报错；每张卡保留其在
   原件中的字节区间与 SHA-256 摘要。
3. **行语法**：在字节上用双引号状态机解析 `[group.]name;param=...:value`，参数名按
   ASCII 处理，带引号的参数值允许包含分号，值字节原样保留。
4. **版本化解码**：4.0 的 TEL/MEMBER/UID 等按 URI 处理，3.0 的 TEL 是文本；文本值才做
   `\n \, \; \\` 反转义，复合值（N、ADR 等）只在未转义的 `;`/`,` 上切分，
   `Road\; Building` 这类转义分号不会被切开。
5. 未知属性不丢弃，按平面文本保留并在属性警告中登记。

## 身份与合并语义（`directory.py`）

- 联系人身份是 UID + KIND；同一 UID 的人与组（KIND 冲突）拒绝混并并记录 `kind-conflict`。
- 属性身份由 UID、属性名、归一化值键与 ALTID/LANGUAGE 决定。**PID 与 CLIENTPIDMAP
  只在对应来源的命名空间内留存**（`pids_by_source`、`clientpidmaps_by_source`），
  不参与跨来源身份，同 PID 不同值不会互相覆盖。
- `ALTID` 标识同一属性家族，`LANGUAGE` 区分语言变体；变体并列保留并共享 family，
  绝不出现同名字段互相覆盖。不同来源对同一 ALTID/语言给出不同值时，保留先到现值并登记
  `variant-conflict` 决策与属性 `conflicts` 原值（含来源与摘要），冲突证据不被后续撤回清除。
- 重复电话/地址按归一化值键识别：合并 TYPE 集合与最高 PREF，每次来源声明（行号、摘要、
  原始值、REV）都保存在属性的 `sources`  provenance 中，决策表记录 `duplicate-merged`。
- 组的 `MEMBER` 可前向引用尚未出现的 UID：先以 `pending` 挂起，后续名片到达后由
  `resolve_all_members` 补齐为 `resolved`；非组名片上的 MEMBER 记 `member-on-non-group`
  并忽略，人和组始终可区分。
- REV 比较：新卡 REV 不新于该来源已接收 REV 时整卡记 `stale-rev-ignored`，不覆盖任何值；
  来源已有 REV 后，无时间戳的重导同样拒绝。只有更新的 REV 可以撤回**该来源独占**的属性
  （`withdrawn`，保留记录不物理删除）；多来源共有的属性只移除该来源声明
  （`source-claim-removed`），任何来源都不能用整张卡删除另一来源的属性。
- 人工屏蔽按属性身份持久化（`suppression` 表）：重导任何卡都只产生 `suppressed-blocked`
  决策并追加来源声明，属性不会复活，除非人工 `unsuppress`。

## 持久化与检查（`storage.Ledger`）

原件先落 `captures` 表（同来源同摘要复用收件编号），隔离件落 `quarantines` 表并保留原件
字节；联系人快照在 `contacts`/`ledger_meta`，逐条合并决策在 `decisions`，人工屏蔽在
`suppression`。`contacts` 与 `decisions` CLI 输出完整的属性级来源与合并理由，可直接 JSON 检查。

本地演练夹具由 `sampledata.py` 生成，均为虚构业务数据。`sample`/`folded`/`invalid` 保持
原有字节；新增 `bilingual`（折行中文+双语）、`forward-group`/`forward-person`（前向引用）、
`source-b`/`source-b-new`（陈旧 REV 与撤回）、`vcard3`（3.0 语法）和
`unsupported-encoding`（隔离）。生成不会下载资源。

本地 HTTP 演练器采用方法与原始路径作为路由键，值为按调用次数返回的响应数组，耗尽后维持
最后一项。响应可定义 status、headers、text/base64、require_headers、chunk_bytes、
disconnect_after。每次请求保留方法、路径、头与正文摘要表示，便于核对实际访问；
演练器只监听回环地址。

原始输入上限 16 MiB 是现有查看入口的保护值，不代表未来持续采集需一次读完整来源。
格式含义和截断错误不能被 HTTP 200 覆盖。
