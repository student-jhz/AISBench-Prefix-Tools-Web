# AISBench-Prefix-Tools-Web

AISBench-Prefix-Tools 的网页版（Web Edition）—— 将原 Windows 桌面向导工具完整迁移为浏览器访问的容器化 Web 服务。

> 上游桌面版项目：[ucm-system/AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools)

## 定位

- **不再打包 Windows EXE**，改为在执行机上以 **Docker 容器服务** 方式部署，浏览器直接访问。
- 完整保留桌面版 **6 步向导** 全部功能：SSH 连接 → 文件与模型 → Docker 部署 → config.py 配置 → 测试用例设计 → 执行测试与结果提取。
- 额外提供独立的 **结果对比分析** 与 **远程日志工具（KV cache 筛选）**。

## 部署方式（规划）

```bash
docker compose up -d --build
# 浏览器访问 http://<执行机IP>:8320
```

## 功能清单（迁移中）

- [x] 仓库初始化
- [ ] Step 1 SSH 连接执行机（密码 / 密钥）
- [ ] Step 2 文件与模型（镜像 tar 上传、内置代码 / zip、远程目录浏览）
- [ ] Step 3 Docker 部署（实时日志、镜像去重、容器创建）
- [ ] Step 4 config.py 生成 / 写入 / 回读校验
- [ ] Step 5 测试用例设计（KV cache 计算、用例增删改、调整系数）
- [ ] Step 6 执行测试（实时日志、日志下载、CSV 提取、结果摘要）
- [ ] 远程日志工具（宿主机 / 容器 / docker logs，KV cache 一键填入）
- [ ] 结果对比分析（CSV 校验、用例匹配、差异表、柱状图、导出）
- [ ] 环境清理（容器删除 + 工作目录清理）
- [ ] Dockerfile / docker-compose 一键部署