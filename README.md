# pi-retry

中断之后不用再手打「继续」。`/retry` 把上一轮请求原样再发一次，**不会往请求里加任何内容**。

```
/retry          # 重发被中断的请求
/retry force    # 上一轮已正常答完时，显式重新生成
ctrl+shift+r    # 同 /retry
```

上一轮**已经正常答完**时 `/retry` 不会发请求（只会提示），因为那等于把同一个问题再问一遍、
在对话里多出第二份回答 —— 需要重答请显式 `/retry force`。

## 会不会多出内容

不会。用真实 provider 抓了每次请求的最终 payload（`before_provider_request`）逐字段对比：

| 会话状态 | 你手打「continue」 | `/retry` | `/retry force` |
|---|---|---|---|
| 被中断（`aborted`） | 旧的部分回答：不带；**多一条 user 消息** | 旧的部分回答：不带；**无额外消息** | 同左 |
| 正常答完（`stop`） | 旧回答：**带**；多一条 user 消息 | 不发请求 | 旧回答：不带（重新生成）；**无额外消息** |

复现：`python3 test/fixture-driver.py`（见下方测试）。

三处内容卫生：

1. **被中断的那一轮**：pi 自己就会剔掉。`packages/ai/src/api/transform-messages.ts` 里对所有
   `stopReason === "error" || stopReason === "aborted"` 的 assistant 消息直接 `continue`，
   任何 provider 请求都不会重放它。所以中断残留根本到不了模型 —— 这也是为什么这个插件
   **没有**「接着写半截回答」的选项：保留也保不住。
2. **触发 run 的隐藏消息**：`customType = "pi-retry/trigger"`、`display: false`。它在
   `context` / `context_with_system` 钩子里从**每一次** provider 请求中删掉；第一次
   `turn_end` 后再写一条 `context_edit(replacement: null)` 把它从 session projection 里永久
   omission —— 因为 compaction / branch summary 是直接序列化 projection、不走 `context`
   钩子的，不这么做摘要 prompt 里就会多一行 "continue"。
3. **正常答完后再 `/retry force`**：请求里去掉尾部那一条 assistant（等价「重答」）。只在这一次
   run 内生效（按 timestamp 记住），不会改会话记录；下一轮对话仍能看到两条回答。

验证过的 payload 对比（`test/rpc-command-driver.py` 的 abort 场景）：

```
before: ['system', 'user']     # 被中断的那次请求
after : ['system', 'user']     # /retry 发出的请求
retried request == expected context: True
```

## 安装

```bash
# 永久安装（写进 ~/.pi/agent/settings.json）
pi install git:github.com/meatRoll/pi-retry@v1.0.2

# 或者只在这次启动加载
pi -e git:github.com/meatRoll/pi-retry@v1.0.2
```

装好后 `/hotkeys` 的 Extensions 表里能看到 `ctrl+shift+r`。

快捷键需要终端能把 `ctrl+shift+r` 和 `ctrl+r` 区分开（Kitty 键盘协议
`ESC[114;6u`，或 xterm modifyOtherKeys `ESC[27;6;114~`）。Ghostty / kitty /
WezTerm / iTerm2 可以；Terminal.app 发不出这个组合，那里用 `/retry`。

## 行为细节

| 情况 | 行为 |
|---|---|
| 正在流式输出 | 提示先按 Esc，不插队 |
| 上一轮被中断 | 请求与中断前那次完全一致（pi 本来就不重放 aborted 轮） |
| 上一轮正常答完 | 拒绝并提示（避免凭空多出第二份回答）；`/retry force` 才重答 |
| 同一轮里有多次请求（tool 循环） | 被去掉的回答对整轮 run 都保持隐藏，不会中途「复活」成重复回答 |
| 尾消息带 tool call | 不裁剪（否则会留下孤儿 tool result），直接续跑 |
| 空会话 | 提示没有可重试的内容 |

## 文件

```
index.ts                     插件本体（只 import type，运行时零依赖）
test/log-context.ts          记录 context 钩子里的消息 + 每次 settle 后的 projection
test/log-payload.ts          记录 provider 的最终 payload（真正发给模型的东西）
test/make-fixture.py         生成 transcript 末尾为 aborted / stop 的会话 fixture
test/fixture-driver.py       确定性测试：payload 里该有的有、不该有的没有；已答完时不发请求
test/rpc-command-driver.py   真实命令路径：prompt → abort → "/retry"
test/e2e.ts + e2e-driver.py  直接调用 retry()，覆盖 tool 循环
```

## 测试

```bash
# 确定性矩阵：aborted/stop × /retry / /retry force / 手打「continue」
python3 test/fixture-driver.py                                # aborted + /retry
PI_RETRY_FIXTURE=stop python3 test/fixture-driver.py           # 已答完 + /retry → 不发请求
PI_RETRY_FIXTURE=stop PI_RETRY_ARGS=force python3 test/fixture-driver.py   # 显式重答
PI_RETRY_BASELINE=1 python3 test/fixture-driver.py             # 对照组：手打 continue
PI_RETRY_FIXTURE=stop PI_RETRY_BASELINE=1 python3 test/fixture-driver.py

# 真实中断 → /retry（payload 必须与中断前一致）
python3 test/rpc-command-driver.py
PI_RETRY_SCENARIO=regen python3 test/rpc-command-driver.py  # 正常答完再 /retry force
PI_RETRY_SCENARIO=busy python3 test/rpc-command-driver.py   # 流式中执行被拒绝

# 直接调 retry()
python3 test/e2e-driver.py
```

全部打印 `RESULT: PASS / FAIL`；请求摘要写到 `/tmp/pi-retry-context.jsonl`、
`/tmp/pi-retry-payload.jsonl`、`/tmp/pi-retry-projection.jsonl`。

类型检查（devDependencies 从 npm 装宿主包的类型）：

```bash
npm install
npx tsc -p tsconfig.json
```

## 限制

- 触发 run 依赖 `pi.sendMessage({ triggerTurn: true })`：pi 没有暴露「从当前 transcript
  继续跑一轮」的 API（`agent.continue()` 要求最后一条不是 assistant）。每次 `/retry` 会在
  session JSONL 里写一条 `display: false` 的 `custom_message`，随即被 `context_edit` 从
  projection 里 omission。
- 想让模型真正「接着写半截回答」，只能把半截内容塞进一条 user 消息 —— 那就会多出内容，
  和这个插件的目标相反，所以没做。
