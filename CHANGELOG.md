# Changelog

本文档记录 **chat2word**（macOS 实时语音转文字助手）的所有重要变更。

---

## [0.4.0] — 2026-02-22

### ✨ 新增
- **双热键模式**：主热键（默认 `Option`）保持原有“按住说话、松开粘贴原文”；新增 LLM 热键（默认 `右 Option`）在松开后进入润色流程
- **LLM 润色链路**：新增 `QwenPolishAdapter`，通过 OpenAI 兼容接口调用 `qwen3.5-plus`，要求意图不变、结构清晰、优先编号输出
- **每日 Markdown 历史日志**：新增 `MarkdownHistoryLogger`，按天写入 `chat2word_YYYY-MM-DD.md`，记录时间、模式、粘贴结果、原文和润色文本
- **状态机扩展**：`SessionState` 新增 `LLM_PROCESSING`，UI 托盘和悬浮窗支持“润色中”提示

### 🔧 修复/改进
- **LLM 失败回退策略**：润色失败时自动回退粘贴原始识别文本，并发出错误提示，不阻断输入
- **双键配对规则**：热键监听增加“按下/松开同源配对”，避免 `Option` 双侧按键交叉触发导致会话错配
- **配置扩展**：新增 `secondary_hotkey` 配置项（默认 `Key.alt_r`），保留 `hotkey` 作为主热键兼容
- **测试基线对齐**：识别器测试迁移到 `fun-asr-realtime` 回调模型；补充 LLM 模式、日志与热键路由测试

## [0.3.0] — 2026-02-22

### 🎉 里程碑：首个可用版本
按住 Option 说话 → 松开自动识别并粘贴到输入框，全流程打通。

### 🔧 修复
- **识别 API 替换**：从错误的 `qwen3-asr-flash`（multimodal-generation HTTP）切换到官方 `fun-asr-realtime`（WebSocket 实时流式），实现真正的边说边识别
- **热键重写 (NSEvent)**：`pynput` 在打包后 "not trusted"，改用 macOS 原生 `NSEvent.addGlobalMonitorForEventsMatchingMask` + `addLocalMonitorForEventsMatchingMask`，解决按键不响应和松开检测失败
- **NSEvent 掩码错误**：修复 `NSFlagsChanged` 枚举值(12)被当作掩码使用的 bug，改为正确的 `1 << 12`
- **NSEvent 非键盘事件崩溃**：添加 `event.type() == 12` 过滤，避免鼠标事件触发 `keyCode()` 导致 objc 异常
- **CGEventTap 后台线程冲突**：CGEventTap 的 CFRunLoop 与 Qt 主事件循环冲突，改用 NSEvent 全局监听（运行在主线程）
- **sounddevice 打包缺失**：`_sounddevice_data`（含 `libportaudio.dylib`）未被 py2app 打包，添加到 `setup.py` packages
- **粘贴崩溃 (pynput Controller)**：`pynput.keyboard.Controller` 在非可信 app 中模拟按键导致原生崩溃，替换为 Quartz `CGEventCreateKeyboardEvent` 模拟 Cmd+V
- **焦点丢失**：悬浮窗弹出时激活了 ASR 应用窗口，导致目标输入框失焦。录音前通过 `NSWorkspace.frontmostApplication()` 保存目标 app，粘贴前 `activateWithOptions_` 恢复焦点
- **识别器重复 FINAL 事件**：`on_event(sentence_end=True)` 和 `on_complete` 各发一次，用 `_final_emitted` 标志去重

### ✨ 新增
- **`.env` 文件支持**：API Key 读取优先级：`config.json` → 环境变量 `DASHSCOPE_API_KEY` → `.env` 文件
- **文件日志**：日志同时输出到 stderr 和 `~/Library/Logs/ASR-Assistant.log`，Finder 双击启动也能查看
- **`codesign` 签名**：打包后执行 `codesign --force --deep --sign -` 确保辅助功能权限识别
- **悬浮窗改进**：添加 `WindowDoesNotAcceptFocus`、`WA_ShowWithoutActivating`、`raise_()` 确保悬浮窗不抢焦点

### 📦 打包
- `setup.py`：添加 `_sounddevice_data`、`cffi` 到 packages，`.env` 打包到 Resources
- `build.sh`：自动化 py2app 构建脚本

---

## [0.2.0] — 2026-02-21

### 🔧 修复
- **Recognizer 音频格式**：修复 `DashscopeRecognizerAdapter` 向 API 发送裸 PCM bytes 的错误，改为 PCM→WAV→base64 编码后再提交（`_pcm_to_wav_base64`）
- **API Key 前置校验**：识别流程启动前即检查 API Key 是否为空，提前返回 `AUTH_FAILED` 而非等到网络超时
- **Qt 主线程阻塞**：`stop_session()` 内有阻塞等待 `_final_event.wait()`，从热键释放直接调用会冻结 UI，现改为后台线程执行

### ✨ 新增
- **悬浮窗定位**：`OverlayWindow` 自动定位到屏幕顶部居中（距菜单栏 40px）
- **延迟隐藏**：`hide_with_delay()` 支持会话结束后 400ms 渐隐
- **错误样式**：错误提示使用红色文字 + ⚠️ 前缀，2 秒后自动隐藏
- **托盘图标**：代码动态生成圆形图标，按状态切换颜色（灰=空闲 / 红=录音 / 橙=错误）
- **状态信号**：新增 `state_signal` 将状态机变化路由到 UI 线程，托盘 tooltip 实时更新
- **API Key 热替换**：在设置面板修改 API Key 后立即生效，无需重启
- **测试覆盖**：
  - `tests/test_recognizer.py` — 8 个用例（流式解析、错误映射、空音频、缺失 SDK、中断取消）
  - `tests/test_recorder.py` — 8 个用例（队列推送、丢帧计数、Sentinel、幂等 start/stop）
  - `tests/test_session_controller.py` — 新增 4 个用例（cancel、partial 回调、空 FinalResult、IDLE cancel）
- `.gitignore` — Python / macOS / IDE 标准忽略规则
- `requirements.txt` 添加 `pytest-qt>=4.3`

### 📝 文档
- 设计文档迁移至 `docs/` 目录（技术路线、功能详细设计、测试方案、评审报告、验收清单）

---

## [0.1.0] — 2026-02-21

### 🎉 初始版本
- **项目骨架**：建立 7 个核心模块的完整代码结构
  - `SessionController` — 五状态状态机（IDLE → RECORDING → FINALIZING → PASTING → IDLE / ERROR）
  - `SoundDeviceRecorder` — 麦克风录音，100ms 分块推入有界队列
  - `DashscopeRecognizerAdapter` — 阿里千问 qwen3-asr-flash 语音识别适配
  - `ClipboardPasteService` — 剪贴板粘贴 + 旧内容恢复
  - `GlobalHotkeyAdapter` — pynput 全局热键（默认 Option 长按），防抖
  - `OverlayWindow` — PySide6 半透明悬浮窗
  - `JsonConfigStore` — JSON 文件配置持久化
- **Protocol 接口** (`interfaces.py`)：`Recorder`、`RecognizerAdapter`、`PasteService`、`ConfigStore`
- **数据模型** (`models.py`)：`AudioFrame`、`RecognitionEvent`、`PasteResult`、`SessionState`
- **错误码** (`errors.py`)：5 个标准错误码 + 用户可读提示
- **应用入口** (`main.py`)：PySide6 状态栏应用 + 菜单
- **初始测试**：`test_session_controller.py`（4 用例）、`test_auto_paste.py`（2 用例）、`test_config_store.py`（2 用例）
- **CI 配置**：`.github/workflows/e2e.yml`
- **设计文档**：技术路线 v2、功能详细设计 v2、测试方案 v2、评审报告、验收清单
