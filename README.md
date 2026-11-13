# contact-card

多来源 vCard 联系人属性的忠实接收：vCard 3.0/4.0 规范化解析、属性级来源留存与合并决策、原件与隔离件留存。

## 当前能力

- `vcard.py`：字节级折行展开 → 名片边界 → 行语法/参数 → 版本化解码 → 转义与结构化值，
  全程不先 split 分号再补救；不可支持的编码整卡隔离并保留原字节。
- `directory.py`：按 UID/KIND 合并联系人；ALTID/LANGUAGE 语言变体并列；重复电话/地址合并
  类型与偏好并保留全部来源；MEMBER 前向引用补齐；REV 陈旧保护与来源作用域撤回；
  人工屏蔽按属性身份持久，重导旧卡不能复活；PID/CLIENTPIDMAP 仅限来源命名空间。
- `storage.py`：`Archive` 保留既有原件收件能力；`Ledger` 在同一 SQLite 中管理原件、
  隔离件、联系人快照、逐条合并决策与屏蔽记录。
- `source.py` 保留既有 FN/EMAIL 检查入口（内部复用新解析器）；`sampledata.py` 构造可重复的
  本地数据；`transport.py` 提供独立的回环 HTTP 演练源与请求记录。
- 解释范围和来源变体见 `docs/source-contract.md`，原有检查测试与新增业务能力测试分开保留。

## 环境

Python 3.11 或以上，仅标准库，无需凭据和外部数据。使用容器时可执行 `docker build -t contact-card .`，依赖及镜像须在正式运行前准备。
运行入口、测试与演示均不安装包，不访问公网。数据库使用临时路径，不向代码目录写入业务文件。

## 测试与构建

构建工具版本固定在 `requirements-build.txt`，在环境准备阶段执行 `python3 -m pip install -r requirements-build.txt`；容器构建已包含此步骤。应用运行仍仅使用标准库，测试及演示不安装依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py source.py sampledata.py storage.py transport.py vcard.py directory.py
python3 -m build --wheel --no-isolation
```

## 快速演示

```sh
python3 app.py demo          # 既有来源检查与原件收件演示
python3 app.py demo-merge    # 规范化接收、合并决策、屏蔽粘性与隔离件全链路演示
```

`demo-merge` 真实启动本地回环来源，演示折行中文、带分号地址、两种语言变体、组成员前向
引用、重复电话类型合并、旧 REV 重导保护、人工屏蔽后重导不恢复，以及无法支持编码的原件
留存，结果以 JSON 输出，退出后自动清理。正常在数秒内完成，不以预录日志代替执行。

## 单独检查与收件

```sh
python3 app.py fixtures /tmp/contact-card-fixtures
python3 app.py inspect /tmp/contact-card-fixtures/sample.vcf
python3 app.py normalize /tmp/contact-card-fixtures/bilingual.vcf
python3 app.py capture /tmp/contact-card-fixtures/sample.vcf --database /tmp/contact-card.sqlite3 --source local-partner
python3 app.py list --database /tmp/contact-card.sqlite3
python3 app.py ingest /tmp/contact-card-fixtures/bilingual.vcf --database /tmp/dir.sqlite3 --source partner-a
python3 app.py contacts --database /tmp/dir.sqlite3
python3 app.py decisions --database /tmp/dir.sqlite3
python3 app.py quarantine-list --database /tmp/dir.sqlite3
```

生成夹具不会覆盖已有文件。运行错误以 JSON 输出到标准错误并返回退出码 2；原始输入无效或
含隔离件时不生成成功收件（旧 `inspect/capture` 入口），新账本入口则在隔离表中留存原件并
返回隔离编号。内部原件与任何对外展示副本应分开管理，不写入真实个人数据。
