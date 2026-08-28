# 📱 花璃 · 安卓 (Termux) 专用安装

> 本文是 [README](../README.md) 中「安卓 (Termux) 专用」安装部分的独立文档，内容与 README 保持一致，方便单独收藏、转发或在手机上直接打开。

由于安卓环境与 PC 不同（需绕过 `pydantic` 编译且依赖预编译库），请严格按以下步骤执行：

## 步骤一：更换软件源（避免下载超时）

首次安装务必切换国内镜像，否则 `pkg` 可能无法连接：
```bash
termux-change-repo
```
（在界面中选择 `Tsinghua` 或 `USTC` 镜像）

## 步骤二：安装基础环境

```bash
pkg update && pkg upgrade -y
pkg install python python-pip git -y
# 如需读取手机存储（/sdcard），执行下方命令并授权：
termux-setup-storage
```

## 步骤三：安装项目依赖（关键步骤）

**请直接复制整条命令**，它强制从安卓专用源获取预编译包，**避免耗时 10 分钟以上的源码编译**：
```bash
pip install -r requirements.txt \
  -i https://termux-user-repository.github.io/pypi/ \
  --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --only-binary pydantic-core,pydantic
```

> **⚠️ 如果上述命令因网络问题下载失败**（提示 `github.com` 超时），请尝试：
> 1. 先安装系统自带的 yaml：`pkg install python-yaml -y`
> 2. 安装编译工具：`pkg install clang binutils rust -y`
> 3. 升级 pip 后直接编译安装（耗时约 10~20 分钟，请耐心等待）：

```bash
pip install --upgrade pip 
pip install -r requirements.txt
```

## 步骤四：运行项目

```bash
cd Flowerie_bot          # 进入项目目录（若克隆到其他路径请相应调整）
cp .env_example .env     # 复制示例配置生成 .env
# 然后编辑 .env，填入 DEEPSEEK_API_KEY（DeepSeek 密钥）与 BOT_QQ（机器人 QQ 号）
python main.py           # 启动机器人
```

---

[← 返回 README](../README.md)
