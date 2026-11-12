# 来源格式与业务语义

来源支持 UTF-8 vCard 3.0 与 4.0 的非二进制属性；未声明支持的 quoted-printable/内联媒体必须隔离并保留原文。展开 CRLF 后空格/制表符续行以字节为单位。复合值中的转义分隔符不分项。source_id 与 CLIENTPIDMAP 构成 PID 命名空间。UID 可供 MEMBER 引用，但不同来源未批准的同 UID 只作为关联候选；人工屏蔽按属性身份保存。

本地演练夹具由 `sampledata.py` 生成，均为虚构业务数据。`sample` 是当前可查看输入，`invalid` 是边界失败输入，其余用于对照来源变体。生成不会下载资源。

本地 HTTP 演练器采用方法与原始路径作为路由键，值为按调用次数返回的响应数组，耗尽后维持最后一项。响应可定义 status、headers、text/base64、require_headers、chunk_bytes、disconnect_after。每次请求保留方法、路径、头与正文摘要表示，便于核对实际访问；演练器只监听回环地址。

原始输入上限 16 MiB 是现有查看入口的保护值，不代表未来持续采集需一次读完整来源。格式含义和截断错误不能被 HTTP 200 覆盖。
