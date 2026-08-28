# 表情包（Sticker）

## 启用

```ini
STICKER_ENABLED=true
STICKER_DIR=/path/to/stickers   # 放图片即可（jpg/png/gif/webp/bmp）
```

## 工作机制

1. **索引**：启动时扫描目录，新图片交给视觉模型生成一句话描述，缓存到 SQLite（`data/stickers.db`）
2. **去重**：以文件 SHA-256 为内容身份——重启复用缓存，**不重复调用 Vision**；文件被替换（同名不同内容）才重新分析
3. **选择**：聊天时只把"表情包描述列表"（文字）给模型，模型按语境在回复中附加 `[STICKER:文件名]` 选择，发送时自动剥离标记
4. **防刷屏**：每群冷却（`STICKER_COOLDOWN`）+ 每次回复最多 `MAX_STICKERS_PER_MESSAGE` 张

## 容错

- Vision 失败：记录失败状态，不阻塞启动，不每次消息重试（24 小时后自动重试）
- 路径安全：只允许 `STICKER_DIR` 目录内的文件，路径穿越一律拒绝
