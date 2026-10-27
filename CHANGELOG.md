# 更新日志

## 0.2.1

- 新增「归档隔离」回归测试：防止归档里的内容被当成活库记忆
- 自测总数 108 条；没装语义模型时显示 99/99 + 跳过 9 条（不是失败）
- 功能与 0.2.0 相同

## 0.2.0

- 新增「按意思找」（语义检索，可选、默认关）：`retrieval.retriever = semantic / hybrid`
- 新增 `tools/fetch_model.py`（下载 bge-small-zh-v1.5）、`tools/sem_build.py`（建向量）
- 向量自动补：生成醒来包时顺带补；检索时落后 ≤20 条现场补
- 检索失败一律退回关键词，并在结果里注明

## 0.1.1

- 文档合并为 `注意事项.md` + `注意事项.html`，README 只作路牌
- 新增 `tools/install_card.py --refresh`：改了睡眠开关后重装模板
- 新增 `demo.bat` / `demo-clean.bat`：3 条示例记忆一键演示与清理
- 仓库改名 `dsh-wakekit`（`wakekit` 在 npm 上已被占用）

## 0.1.0

- 首个可用版本：记忆库、原文归档、醒来包、睡眠设定
- MCP 服务手写实现，零依赖；七套自测
