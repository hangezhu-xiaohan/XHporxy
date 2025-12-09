# XHproxy - 高性能代理服务器

XHproxy是一个功能强大的HTTP/HTTPS代理服务器，提供了直观的图形用户界面(GUI)，方便用户进行流量监控和分析。

## 主要功能

### 核心功能
- ✅ 支持HTTP和HTTPS请求转发
- ✅ 高性能并发处理
- ✅ 详细的请求/响应日志记录
- ✅ 完整的错误处理机制

### 监控与分析
- 📊 实时流量监控图表
- 📈 网络请求统计分析
- 📋 详细的请求记录表格
- 📉 带宽使用情况分析

### 用户界面
- 🎨 现代化的图形界面
- 📱 响应式设计
- ⚙️ 易于配置的参数设置
- 📦 直观的仪表盘展示

## 技术栈

- **后端**: Python 3.x
- **GUI框架**: PyQt5
- **图表库**: PyQtChart, Matplotlib
- **数据处理**: NumPy

## 安装与运行

### 环境要求
- Python 3.7 或更高版本
- Windows/macOS/Linux

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动程序

```bash
python proxy_gui.py
```

### 直接运行代理服务器

如果不需要GUI界面，可以直接运行代理服务器：

```bash
python proxy_server.py
```

## 项目结构

```
XHporxy/
├── proxy_gui.py      # GUI界面主文件
├── proxy_server.py   # 代理服务器核心实现
├── requirements.txt  # 项目依赖
├── README.md         # 项目说明文档
└── LICENSE           # 许可证文件
```

## 配置说明

### 基本配置
- 监听端口: 默认8080
- 最大连接数: 1000
- 超时时间: 30秒

### 高级配置
- 日志级别: DEBUG/INFO/WARNING/ERROR
- 缓冲区大小: 8192字节
- 线程池大小: 动态调整

## 使用方法

1. 启动程序后，点击"启动代理"按钮
2. 在浏览器或其他应用中设置代理服务器地址为 `http://localhost:8080`
3. 开始使用代理浏览网络
4. 在监控面板查看实时流量和请求记录

## 性能特点

- 采用多线程架构处理并发请求
- 内存友好的连接管理
- 高效的日志记录机制
- 实时性能监控

## 许可证

本项目采用MIT许可证，详见LICENSE文件。

## 贡献

欢迎提交Issue和Pull Request！

## 联系方式

如有问题或建议，欢迎通过GitHub Issues反馈。
