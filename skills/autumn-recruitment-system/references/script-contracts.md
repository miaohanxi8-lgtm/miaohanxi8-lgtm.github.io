# 确定性脚本与数据契约

## 使用边界

- `scripts/recruitment_rules.py` 只做标准化、严格证据匹配、结构校验和历史耗时计算，不访问邮箱或 Notion。
- 脚本输出是候选、唯一精确命中或错误报告，不替代语义判断。`ambiguous` 必须由模型复核，仍不能唯一确定时询问用户。
- 所有输出 JSON 使用临时文件后原子替换。输入和输出放在运行数据目录或临时工作区，不放入 Skill。
- 日常运行固定复用 `normalized_mail.json`、`matches.json`、`validation.json` 和 `estimates.json`，每轮覆盖，不按日期创建历史副本；只有用户明确要求留档时才另存。

## 邮件标准化

命令：

`python scripts/recruitment_rules.py normalize-mails --input <pending_mail.json> --output <normalized_mail.json>`

脚本按每封邮件产生：标准化主题、邮件 ID、发件地址与域名、岗位/申请编号候选、申请链接、会议链接、其他链接、日期候选、附件元数据和严格证据键。它不复制邮件正文到新文件，避免重复占用磁盘；正文仍只存在原始 `pending_mail.json`。

## 精确匹配

命令：

`python scripts/recruitment_rules.py match --candidates <normalized_mail.json> --records <records.json> --output <matches.json>`

`records.json` 使用以下最小结构；未知字段可省略：

```json
{
  "records": [
    {
      "id": "Notion 页面 URL 或稳定 ID",
      "message_id": "邮件 Message-ID",
      "uid": 123,
      "sender_address": "sender@example.com",
      "sender_domain": "example.com",
      "job_ids": ["REQ-1234"],
      "application_urls": ["https://example.com/apply/1234"],
      "company": "公司",
      "job_title": "岗位",
      "subject": "标准化前或后的主题"
    }
  ]
}
```

证据强度从高到低：

1. 邮件 ID + 发件人地址。
2. UID + 发件人地址。
3. 岗位编号或申请入口 + 发件域名。
4. 公司 + 岗位 + 发件人地址或域名。
5. 标准化主题 + 发件人地址。

脚本只在最强证据层恰好命中一个对象时返回 `unique_match`；同层多对象返回 `ambiguous`，没有证据返回 `no_match`。发件人单独命中永远不产生结果。

## 运行计划校验

命令：

`python scripts/recruitment_rules.py validate-plan --input <plan.json> --output <validation.json>`

校验输入可包含：

```json
{
  "date": "2026-09-02",
  "daily_mock_tasks": [
    {"id": "task-url", "kind": "targeted", "scheduled_date": "2026-09-02"}
  ],
  "interviews": [
    {
      "id": "interview-key",
      "status": "active",
      "real_pages": ["url"],
      "simulation_pages": ["url"],
      "schedules": ["url"],
      "simulation_tasks": ["url"],
      "review_tasks": ["url"],
      "real_date": "2026-09-02",
      "simulation_date": "2026-09-02",
      "review_date": "2026-09-02"
    }
  ],
  "report": {
    "sections": [
      {"name": "今日行动", "fact_ids": ["fact-1"]},
      {"name": "最新变化", "fact_ids": ["fact-2"]},
      {"name": "未来 7 天", "fact_ids": ["fact-3"]},
      {"name": "待补全与需确认", "fact_ids": ["fact-4"]}
    ]
  },
  "status_transitions": [
    {"entity": "page-url", "from": "笔试", "to": "拒信", "evidence": "邮件 ID"}
  ],
  "commit": {
    "requested": true,
    "jobs_ok": true,
    "interviews_ok": true,
    "tasks_ok": true,
    "report_ok": true
  }
}
```

脚本检查每日模拟数量、针对性/通用模拟冲突、真人面试五对象数量和日期、早报四段顺序、事实跨区块重复、终态证据与 UID 提交条件。

## 历史耗时估算

命令：

`python scripts/recruitment_rules.py estimate-focus --input <focus.json> --output <estimates.json>`

输入：

```json
{
  "records": [
    {
      "task_id": "task-url",
      "task_type": "模拟面试",
      "start": "2026-09-02T09:00:00+08:00",
      "end": "2026-09-02T10:10:00+08:00",
      "completed": true
    }
  ]
}
```

脚本只使用已完成任务，剔除无效、跨日、零时长和单段超过四小时的记录；同一任务的重叠区间先合并。估算通常使用中位数，样本分散时使用上四分位，并向上取整到 15 分钟。三条及以上样本为高置信度，一至两条为中置信度；没有样本时由模型按正式时长或保守默认值给出低置信度估算。

## 邮件存储管理

查看占用：

`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/qq_job_mail/run_qq_mail.ps1 storage-report`

邮件附件内容不写入磁盘，只在 `pending_mail.json` 中保留文件名、MIME 类型和大小。`state.json`、`pending_mail.json`、`normalized_mail.json`、匹配、校验和估算结果均使用固定文件名覆盖；运行目录不存在随邮件数量持续增长的附件或历史归档。

