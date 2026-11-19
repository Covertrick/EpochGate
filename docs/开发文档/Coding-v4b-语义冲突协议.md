# Coding v4b 语义冲突协议

写于 v4 确认集开始之前。v4 协议里的 `raise RuntimeError("epochgate-semantic-conflict")` 不改。若开发集可行性把它标成 `INVALID_FAULT`，v4 不把该故障上的失败算进模型或运行时的进展能力，也不算进不安全接纳。

本文件只定义另一批故障。任务仍是 v4 的三个开发任务。六个确认任务不使用。

## V1

沿用 v4 的末尾注释。凭证过期。内容是否失效由隐藏测试决定。版本保护拒绝一份仍能通过测试的补丁时，计入 Unnecessary Rejection，不计入避免错误发布。

## V2

在黄金补丁第一个改动块所在文件里，于该块起始行的下一行插入一行同缩进注释 `# epochgate-related-comment`。不改可执行语句。插入后工作区必须仍能导入。旧补丁能否应用由 `git apply` 记录。是否仍能通过隐藏测试，只由容器决定。

## V3

不在模块顶层替换会在导入时执行的语句。在第一个改动块所在函数的函数体第一行插入 `raise RuntimeError("epochgate-semantic-conflict")`。函数体内部的 raise 不在导入时执行。若该块不在任何函数内，或插入后不能解析、不能导入，这一格标 `INVALID_FAULT`，不进入 Unsafe Admission 的分母。

旧补丁在这棵树上不能通过隐藏测试，才把内容记为语义失效。存在一份由历史修复文件重新生成、并能通过隐藏测试的后继，才把这格算作有合法进展可恢复。

## 指标

Unsafe Admission Rate 的分母只含 Oracle 判定内容无效的效果。

Unnecessary Rejection Rate 的分母只含 Oracle 判定内容仍然有效、却被版本检查拒绝的效果。

Successor Recovery Rate 按生成、接纳、隐藏测试三阶段分开。

这三项是已有安全与可用性框架在本运行时中的联合测量，不是新的守卫定义。
