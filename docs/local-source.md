# 本地协议演练

`python3 scenario.py` 启动仅监听回环的业务来源，输出临时端口和可用路由。进程在前台运行，以 Ctrl-C 关闭。

`python3 scenario.py --export` 输出演练响应定义，便于按 `docs/source-contract.md` 在独立副本增加乱序、截断、失败或来源更新情形。响应数组顺次消耗并保持最后状态；不会调用真实第三方系统。

请求者可以在测试中用 `from scenario import scenario` 和 `ReplaySource(scenario())` 获得同样来源并检查 requests，避免固定端口冲突。现有 scenario 测试核对来源实际返回值；新增的规范化接收能力由 `tests/test_vcard.py`、`tests/test_directory.py` 与 `tests/test_app_demo.py` 覆盖。

完整接收链路演示：

```sh
python3 app.py demo-merge
```

该演示通过真实回环 HTTP 依次取回组（含前向 MEMBER）、双语联系人、后到成员、旧 REV 卡、
vCard 3.0 卡与含不可编码件的字节流，写入临时 SQLite 账本，再执行人工屏蔽、旧卡重导和
新 REV 撤回，输出属性级合并决策、组解析结果与隔离件留存校验，退出后自动清理。

## 账本 CLI

```sh
python3 app.py normalize <file.vcf>                 # 完整规范化视图（不入库）
python3 app.py ingest <file.vcf> --database db.sqlite3 --source partner-a
python3 app.py contacts --database db.sqlite3       # 属性级来源、类型、偏好与状态
python3 app.py groups --database db.sqlite3         # 组及成员 pending/resolved
python3 app.py decisions --database db.sqlite3      # 逐条合并决策与理由
python3 app.py quarantine-list --database db.sqlite3
python3 app.py quarantine-raw --database db.sqlite3 <id> exported.vcf
python3 app.py suppress --database db.sqlite3 --uid <uid> --attr-id <id> --reason "..."
python3 app.py unsuppress --database db.sqlite3 --uid <uid> --attr-id <id>
```

标准演练顺序是先准备依赖，再运行 `python3 -m unittest discover -s tests -v`、构建命令和
`python3 app.py demo` / `python3 app.py demo-merge`。新增能力演示须调用实际新增入口，
不以现有结构检查输出代替；演练应选择有限输入，并在九十秒内完成。
