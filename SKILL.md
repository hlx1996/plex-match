---
name: plex-match
description: "Plex 媒体库全库扫描、批量匹配与验证：先强制 scan 全部媒体库，再找到未匹配（unmatched）的资源并自动匹配元数据，然后验证全部匹配是否正确并自动修正错误，最后输出本次新 match 和新 update 的媒体列表。当用户提到 Plex 匹配、unmatched、元数据匹配、Plex 刮削、海报缺失时使用。"
argument-hint: "<plex-url>"
---

# Plex Match

先强制扫描整个 Plex 媒体库，再批量匹配所有未匹配资源，并验证全部匹配的正确性。

## Workflow

### Phase 1: 获取信息

1. **Plex URL**：如果用户提供了 URL（如 `https://plex.example.com`），直接使用。否则向用户询问。
2. **Token**：告知用户获取方法，等待用户直接粘贴 Token（不要用 AskUserQuestion，直接让用户在对话中输入）：

> 获取 Plex Token 的方法：
> 1. 浏览器打开你的 Plex Web 界面
> 2. 按 `F12` 或 `Cmd+Option+I` 打开开发者工具
> 3. 切换到 **Network** 标签，刷新页面
> 4. 点击任意请求，在 URL 中找 `X-Plex-Token=xxxxx`
>
> 找到后直接把 Token 发给我即可。

等待用户回复 Token 后继续。

⚠️ 如果 token 看起来是正确的但 API 返回 401，要特别检查是否混入了**形似英文字母的非 ASCII 字符**（例如西里尔字母 `у` 看起来像英文字母 `y`）。Plex token 应该是纯 ASCII。

### Phase 2: 验证连接

```bash
curl -s "<BASE>/library/sections?X-Plex-Token=<TOKEN>" | head -200
```

确认能获取到媒体库列表。401 则 Token 无效，引导重新获取。

### Phase 3: 先强制 scan 全部媒体库

先定位 **Python 3**。不要回退到系统自带的 `python`，因为某些 NAS 环境里的 `python` 实际上还是 Python 2，会导致脚本无法运行。实机可用的兜底路径示例：

```bash
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif [ -x /share/CACHEDEV1_DATA/.qpkg/Apache84/bin/python3.13 ]; then
  export LD_LIBRARY_PATH="/share/CACHEDEV1_DATA/.qpkg/Apache84/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  PYTHON_BIN="/share/CACHEDEV1_DATA/.qpkg/Apache84/bin/python3.13"
else
  echo "Python 3 is required for plex-match scripts" >&2
  exit 1
fi
```

然后先触发全库扫描，并等待所有库的 `refreshing` 状态回到 `0`：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_scan.py \
  --base "<BASE>" --token "<TOKEN>"
```

扫描结果会保存到 `/tmp/plex_scan_result.json`。只有在扫描完成后，才能进入后续匹配步骤。

### Phase 4: 批量匹配 Unmatched

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_match.py \
  --base "<BASE>" --token "<TOKEN>" --delay 1.0
```

参数：`--delay`（请求间隔）、`--library <key>`（指定库）、`--dry-run`（只列出不匹配）、`--result-file`（指定 JSON 结果路径）。

匹配量大时用 background 运行，定期检查日志进度。脚本内置 503 重试（3 次，间隔递增）。

脚本会把结果保存到 `/tmp/plex_match_result.json`，其中：
- `matched_items` = 本次新 match 的媒体列表
- `failed_items` = 本次仍未成功匹配的媒体列表

匹配完成后，对脚本报告的 failed 项，逐个手动搜索修正：
1. 用 `/library/metadata/<key>/matches` 搜索正确结果
2. 用 `PUT /library/metadata/<key>/match` 应用匹配
3. 如果 Plex 数据库中确实没有该项，记录并告知用户

### Phase 5: 验证所有匹配

匹配完成后 **必须** 运行验证脚本，检查全部媒体（包括原本已匹配的）：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_verify.py \
  --base "<BASE>" --token "<TOKEN>" --fix --delay 0.3
```

脚本会：
1. 遍历所有媒体库，获取每项的文件路径
2. 对电影，读取 `Media/Part` 文件路径；对电视剧/动漫/纪录片，额外读取 show 级别的 `Location` 目录路径
3. 对比清洗后的来源路径与 Plex 的 `title` / `originalTitle` / `slug` 相似度，尽量减少“英文文件名 + 中文标题”的误报
4. 将“高置信度错配”和“翻译/别名待人工复核”区分开，不要把所有低相似度项目都直接强修
5. `--fix` 模式只自动修复高置信度错误；其余项目进入人工复核清单
6. 修完后必须按**原始来源路径**回查归属，确认该目录/文件已经挂到正确条目下

结果保存到 `/tmp/plex_verify_result.json`。其中：
- `updated` / `fixed` = 本次新 update（修正）列表
- `unmatched_remaining` = 仍未匹配的项目
- `unfixable` = 需要人工审查的项目

验证完成后，读取 JSON 结果并向用户汇报：
- 自动修复了多少项
- 仍然 unmatched 的项（提醒用户关注，**不要强行匹配**）
- 需要手动审查的项

### Phase 5.5: 用户反馈后的定向 rematch

如果用户明确指出了“当前错误标题 → 正确目标标题”，把用户反馈视为**高优先级事实**，直接做定向 rematch，不要继续依赖相似度猜测。

步骤：
1. 先用 `/library/metadata/<ratingKey>/matches` 按**目标标题**拉候选
2. 选择与用户目标一致的 `guid`
3. 调用 `PUT /library/metadata/<ratingKey>/match`
4. **不要只看旧 `ratingKey` 是否还在**；对于电视剧/动漫，match 到已存在 show 后，旧条目可能会并入目标 show，旧 `ratingKey` 会消失
5. 最后按**原始目录路径**回查，确认该路径现在归属于正确作品

这一条是本次实操的重要结论：对 show 来说，“路径归属正确”比“旧 `ratingKey` 还存在”更能证明修正成功。

### Phase 7: Chinese Localization for Plex（必做）

匹配、验证和必要的定向 rematch 完成后，**必须** 运行 CLP 对媒体库进行拼音排序和标签汉化。

1. 克隆仓库并安装依赖：

```bash
cd /tmp && git clone https://github.com/x1ao4/chinese-localization-for-plex.git
cd chinese-localization-for-plex
pip3 install -r requirements.txt
```

如果当前环境的 `/tmp` 很小、`pip3`/`python3` 不可用，**不要** 在 `/tmp` 里建 venv 硬装。优先复用 Phase 3 已定位出的 `PYTHON_BIN`，并把依赖安装到宿主机持久目录（例如 `/share/CACHEDEV1_DATA/.qoder/tmp/plex-phase78/pydeps`）：

```bash
PHASE78_DIR="/share/CACHEDEV1_DATA/.qoder/tmp/plex-phase78/pydeps"
mkdir -p "$PHASE78_DIR"
"$PYTHON_BIN" -m pip install --target "$PHASE78_DIR" flask pypinyin requests
PYTHONPATH="$PHASE78_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" chinese-localization-for-plex.py --all
```

2. 配置 `config/config.ini`：

```ini
[server]
address = <BASE>
token = <TOKEN>
skip_libraries =
```

3. 运行处理所有项目：

```bash
python3 chinese-localization-for-plex.py --all
```

脚本会将所有中文标题的排序字段改为拼音首字母缩写，并将英文标签汉化。已处理的项目会被跳过。

### Phase 8: Plex-Trakt 同步 (PlexTraktSync)（必做）

中文本地化完成后，**必须** 运行 PlexTraktSync，将 Plex 与 Trakt 的观看记录、评分、收藏/想看等状态同步。

1. 安装：

```bash
pipx install PlexTraktSync
```

如果 `pipx` 不可用，或者 `/tmp` / 默认缓存目录空间太小导致安装失败，改用宿主机持久目录安装并显式指定 `PTS_CONFIG_DIR` / `PYTHONPATH`：

```bash
PHASE78_DIR="/share/CACHEDEV1_DATA/.qoder/tmp/plex-phase78/pydeps"
mkdir -p "$PHASE78_DIR"
"$PYTHON_BIN" -m pip install --target "$PHASE78_DIR" PlexTraktSync
export PTS_CONFIG_DIR="$PHASE78_DIR"
export PYTHONPATH="$PHASE78_DIR${PYTHONPATH:+:$PYTHONPATH}"
```

2. 先把 Plex 服务器写进 `servers.yml`，至少包含：

```yaml
servers:
  default:
    token: <TOKEN>
    urls:
      - <BASE>
```

3. 完成 Trakt 登录（首次需要交互式授权）：

```bash
plextraktsync trakt-login
```

如果环境里已经有可用的 `.pytrakt.json` / `.env` / `servers.yml`，直接复用；否则就必须现场补齐并完成授权。首次登录至少需要：
- Trakt API Client ID 和 Client Secret（从 https://trakt.tv/oauth/applications/new 创建）
- Plex 服务器 URL 和 Token（复用 Phase 1 获取的信息）

对于无头/NAS 环境，如果 `trakt-login` / `login` 因 `RemoteDisconnected`、`requests-cache`、TLS 或其他网络异常失败，**不要卡住重试同一条命令**；改用本 skill 自带的手动 device-auth 兜底脚本：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_trakt_device_login.py start \
  --client-id "<TRAKT_CLIENT_ID>" \
  --state-file /tmp/plex_trakt_device_auth.json
```

读取输出里的 `user_code` 和 `verification_url`，让用户完成授权后，再执行：

```bash
"$PYTHON_BIN" <skill-dir>/scripts/plex_trakt_device_login.py finish \
  --client-id "<TRAKT_CLIENT_ID>" \
  --client-secret "<TRAKT_CLIENT_SECRET>" \
  --state-file /tmp/plex_trakt_device_auth.json \
  --config-dir "$PTS_CONFIG_DIR" \
  --plex-server-name default
```

这一步会把 Trakt token 写入 `.pytrakt.json`，并把 `TRAKT_USERNAME` / `PLEX_SERVER` 写入 `.env`，供 PlexTraktSync 直接复用。

4. 运行同步：

```bash
plextraktsync sync
```

如果同步过程中卡在 `metadata.provider.plex.tv`、Plex Online watchlist 或 liked lists 超时，不要因此放弃整个 phase 8。先把以下开关临时关闭，至少完成 **watched / rating** 的核心同步：

```yaml
sync:
  plex_to_trakt:
    watchlist: false
  trakt_to_plex:
    liked_lists: false
    watchlist: false
```

同步完成后，把成功同步的核心结果并入最终报告；如果 watchlist / liked lists 因 Plex Online 超时被跳过，也要明确告知用户。

### Phase 6: 最终输出 & 修正

向用户输出最终报告：
- 各媒体库总数和 unmatched 数
- 本次新匹配数 / 失败数
- 验证发现并修正的错误数
- 用户反馈后的**定向 rematch 列表**（Phase 5.5；如果有手动改正，必须单独列出，不要被 `updated=[]` 掩盖）
- 中文本地化执行结果（phase 7）
- PlexTraktSync 执行结果（phase 8）
- **新 match 列表**（读取 `/tmp/plex_match_result.json` 的 `matched_items`）
- **新 update 列表**（读取 `/tmp/plex_verify_result.json` 的 `updated`）
- 仍无法匹配的项（建议用户检查文件名是否规范）
- 需要人工修正但暂不应强改的项
- 提醒刷新 Plex 界面加载新元数据

如果 `matched_items` 或 `updated` 为空，要明确输出“无”，不要省略这一节。

## 已知陷阱

### 不要盲目取第一个搜索结果
Plex 搜索 API 返回的第一个结果不一定是正确的。常见错误：
- 匹配到同名纪录片 / Making-of（如 "LOTR3" 匹配到 "LOTR Symphony"）
- 匹配到不同年份的同名片（如 "Dumbo 2019" 匹配到 "Mumbo Jumbo 2017"）
- 匹配到完全不同语言的同名片（如 "家有喜事" 匹配到 "Alles Lüge"）

脚本已优化：优先选择同年份结果，跳过纪录片类结果。

### 电视剧季别年份差异是正常的
Plex 按剧集（show）级别匹配，`year` 字段是整部剧的首播年份，不是具体季的年份。如 "咒术回战 S02 (2023)" 匹配到 "咒术回战 (2020)" 是正确的。

### 外语片 originalTitle 不同是正常的
韩语/日语/法语等外语片，Plex 匹配后的中文标题与文件中的外语原名不同是正常行为，不算错误。

### 繁简体差异是正常的
"长江七号" vs "長江七號"、"唐伯虎点秋香" vs "唐伯虎點秋香" 是繁简体差异，匹配正确。

### 合集文件夹会导致验证误报
如果多个电影文件放在同一个合集文件夹里（如 "漫威超级英雄电影22部合集"），文件夹名和单个电影标题自然不匹配。验证脚本会跳过合集文件夹。

### 搜索时要用多种关键词尝试
同一个资源可能有中文名、英文名、原名（日韩德法等）。搜索匹配时如果第一个关键词没有结果，要换其他关键词重试：
- 中文标题（`山东味道`）
- 英文标题（`A Bite of Shan Dong`）
- 拼音/罗马字（`Shan Dong Wei Dao`）
- 原名（`Welt am Draht`）

不要因为一次搜索没结果就认定不存在。验证脚本对 failed 项也应多试几个关键词。**对中文内容优先用中文名搜索**——英文名可能返回错误的同名资源（如搜 "Black Mirror" 返回 "来自未来的故事"，搜 "黑镜" 才返回正确结果）。

### Plex Discover 目录中的资源可能搜不到
部分资源存在于 Plex Discover 目录中，但不会出现在常规 `/matches` 搜索结果里。如果搜索多次都没有结果：
1. 让用户在 Plex Web UI 的 Discover/Browse 中搜索该资源
2. 打开详情页，从 URL 中提取 key（如 `/library/metadata/5d9c089bba6eb9001fba73de`）
3. 用 `plex://movie/<id>` 格式的 guid 直接调用 match API 匹配

### Plex 数据库中确实不存在的资源无法匹配
极少数冷门资源可能在 Plex 的元数据源（TMDB/TheTVDB）中不存在，无法自动匹配。这些应该报告给用户，由用户手动处理，**不要强行匹配到错误的结果**。

### Plex API 不支持 Unmatch
Plex 的 match API 只能将资源匹配到某个 guid，无法通过 API 取消匹配（Unmatch）。取消匹配只能在 Plex Web UI 中操作：点击 `...` → Match... → Unmatch。

### refresh 接口是异步的
`/library/sections/<key>/refresh` 返回 200 只代表任务已入队，不代表扫描已经完成。必须继续轮询 `/library/sections`，确认目标库的 `refreshing="0"` 后再开始批量匹配。

### 无头/NAS 环境下，`trakt-login` 可能直接失败
在某些 NAS / 精简 Python 环境里，`plextraktsync trakt-login` 可能因为 `RemoteDisconnected`、`requests-cache`、TLS 或上游短暂波动而直接失败。遇到这种情况时：
1. **不要** 一直重试同一条 `trakt-login`
2. 先确认 `https://api.trakt.tv/oauth/device/code` 直连可用
3. 改用本 skill 的 `scripts/plex_trakt_device_login.py` 走手动 device auth
4. 授权成功后，确认 `.pytrakt.json` 和 `.env` 已写好，再继续 `sync`

### Plex Online watchlist / liked lists 可能拖垮整个 sync
`plextraktsync sync` 不只访问本地 PMS；如果启用了 Plex watchlist / liked lists，同步过程中还会访问 Plex Online（如 `metadata.provider.plex.tv`）。这条链路在某些网络环境里会超时，导致整个 sync 提前退出。

如果核心目标只是完成 **watched / rating** 同步，而 Plex Online 访问不稳定，应临时关闭：
- `sync.plex_to_trakt.watchlist`
- `sync.trakt_to_plex.watchlist`
- `sync.trakt_to_plex.liked_lists`

然后先完成核心同步，并在最终报告里明确说明 watchlist / liked lists 本次被跳过。

### 某些 Trakt watched-show 响应会返回 `seasons: null`
实操中遇到过 `plextraktsync sync` 在 `plextraktsync/pytrakt_extensions.py` 崩溃，报错：

```text
TypeError: 'NoneType' object is not iterable
```

根因是 Trakt 的部分 watched-show / season 数据字段可能返回 `null`，而 PlexTraktSync 当前版本直接迭代了 `shows` / `seasons` / `episodes`。

如果命中这个问题：
1. 先确认报错栈是否落在 `pytrakt_extensions.py`
2. 对本地安装的 PlexTraktSync 做最小兼容修复：把 `shows`、`seasons`、`episodes` 的 `None` 当空列表处理
3. 修完后再重跑 `plextraktsync sync`

### 少量外语片罗马字标题仍可能进入人工复核
即使已经结合 `originalTitle` 和 `slug` 做相似度校验，仍可能有少量外语片因为文件名使用**罗马字/英译名**、而 Plex 元数据使用**中文名或原文字**而被标记为可疑，例如：
- `Jagten` ↔ `The Hunt`
- `Kimi to Nami ni Noretara` ↔ `若能与你共乘海浪之上`
- `Gamlet` ↔ `哈姆雷特`

这类项目要优先人工确认，**不要因为 verify 报警就强行改成别的匹配**。

### 实操复盘：这次为什么会“反凑”

这次真实出现的错配，根因主要有 4 类：

1. **之前的 verify 对 show 检查不完整**
   - 只看电影 `Part` 文件路径是不够的；动漫/电视剧很多条目只有 show 级 `Location` 路径
   - 这会漏掉明显错配，例如：
     - `大清风云` 实际目录是 `大宋提刑官2...`
     - `斗罗大陆Ⅱ绝世唐门` 实际目录是 `Jujutsu.Kaisen.S02...`
     - `特工科恩` 实际目录是 `SPY×FAMILY`
     - `舞法天女之绚彩归来` 实际目录是 `Rick and Morty S04`
     - `镖人` 实际目录是 `进击的巨人第一季第二季第三季`

2. **不能把“只重叠一个词”当成正确匹配**
   - `SPY×FAMILY` 和 `The Spy / 特工科恩` 只共享一个 `spy`
   - `Made in Abyss S02` 和 `玛露露库的日常` 共享了同 franchise，但不是同一作品
   - 结论：**单词级弱重叠**、**同 franchise 子集重叠**，都只能进人工复核，不能直接自动修

3. **短英文标题同名碰撞风险很高**
   - `Myth of Love` 这类短英文名，Plex 很可能命中另一个同名作品
   - 即使标题和年份都对，也不代表作品就对
   - 结论：如果文件名是**短英文别名**，而官方中文标题与它几乎没有 token 重叠，就必须人工确认，必要时让用户给出目标标题

4. **合集/季包目录很容易把 show 带偏**
   - 如果目录本身是“第一季第二季第三季合集”“S01-S08”等打包名，Plex 有时会把它归到错误条目
   - 结论：这类目录要在 verify 结果里优先人工审查；修完后一定按目录归属回查

### 下次执行时的硬规则

1. **先 scan，再 match，再 verify**
2. **verify 必须覆盖 show 的 `Location` 路径**，不能只看 movie `Part`
3. **高置信度错配才自动修**
   - 完全不同作品
   - 用户明确给出“错误标题 → 正确标题”
   - 修完后可按路径归属证明成功
4. **以下情况只进人工复核**
   - 只共享 1 个英文词
   - 同 franchise 但像 parent show / spinoff
   - 短英文别名 / unofficial English alias
   - 合集目录 / 多季打包目录
5. **修完后一定按原始路径回查**
   - 对 show，允许旧 `ratingKey` 消失
   - 只要原始目录已经并入正确作品，就算修正成功
