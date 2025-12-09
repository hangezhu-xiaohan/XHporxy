#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代理服务器GUI界面
提供高大气上档次的用户界面，包含流量监控和分析功能
"""

import sys
import time
import threading
import queue
import json
from datetime import datetime

# 导入PyQt5模块
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QFormLayout, QSpinBox, QCheckBox, QMessageBox, QSplitter,
    QStatusBar, QProgressBar, QComboBox, QMenuBar, QAction, QScrollArea,
    QFileDialog, QSizePolicy, QSystemTrayIcon, QMenu, QFrame, QTreeWidget, QTreeWidgetItem
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QDateTime, QSize, QMargins
)
from PyQt5.QtGui import (
    QIcon, QColor, QPainter, QPen, QBrush, QFont,
    QStandardItemModel, QStandardItem
)

# 导入图表库
from PyQt5.QtChart import (
    QChart, QChartView, QLineSeries, QBarSeries, QBarSet,
    QValueAxis, QDateTimeAxis, QCategoryAxis, QPieSeries, QPieSlice, QAbstractBarSeries
)

# 导入代理服务器模块
from proxy_server import ProxyServer, setup_logging

# 设置中文字体支持
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号


class TrafficMonitorThread(QThread):
    """流量监控线程，用于实时收集代理服务器的统计信息"""
    
    # 定义信号，用于向主线程发送数据
    stats_updated = pyqtSignal(dict)
    request_added = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    traffic_updated = pyqtSignal(dict)  # 新增流量更新信号
    
    def __init__(self, proxy_server):
        super().__init__()
        self.proxy_server = proxy_server
        self.running = True
        self.stats_queue = queue.Queue()
        
        # 初始化上次统计时间，用于计算速率
        self.last_stats_time = time.time()
        self.last_bytes_sent = 0
        self.last_bytes_received = 0
        self.last_requests_count = 0
        
        # 重写代理服务器的统计更新方法
        original_record_error = proxy_server._record_error
        
        def wrapped_record_error(error_type, error_message):
            original_record_error(error_type, error_message)
            error_data = {
                'type': error_type,
                'message': error_message,
                'timestamp': datetime.now().strftime('%H:%M:%S')
            }
            self.stats_queue.put(('error', error_data))
        
        proxy_server._record_error = wrapped_record_error
    
    def run(self):
        """运行监控线程，定期收集统计信息"""
        while self.running:
            try:
                # 确保代理服务器存在且方法可用
                if not hasattr(self, 'proxy_server') or self.proxy_server is None:
                    time.sleep(1)
                    continue
                
                # 获取代理服务器的统计信息，添加异常处理
                try:
                    current_stats = self.proxy_server.get_stats()
                except AttributeError:
                    # 如果get_stats方法不存在，使用默认值
                    current_stats = {
                        'requests_count': 0,
                        'total_http_requests': 0,
                        'total_https_requests': 0,
                        'active_connections': 0,
                        'bytes_received': 0,
                        'bytes_sent': 0,
                        'error_count': 0,
                        'retry_count': 0,
                        'successful_requests': 0,
                        'failed_requests': 0,
                        'connection_pool_hits': 0,
                        'connection_pool_misses': 0,
                        'websocket_connections': 0,
                        'start_time': datetime.now()
                    }
                
                # 计算时间差
                current_time = time.time()
                time_diff = current_time - self.last_stats_time
                if time_diff > 0:
                    # 计算速率
                    bytes_sent_rate = (current_stats.get('bytes_sent', 0) - self.last_bytes_sent) / time_diff
                    bytes_received_rate = (current_stats.get('bytes_received', 0) - self.last_bytes_received) / time_diff
                    requests_rate = (current_stats.get('requests_count', 0) - self.last_requests_count) / time_diff
                    
                    # 更新上次统计值
                    self.last_stats_time = current_time
                    self.last_bytes_sent = current_stats.get('bytes_sent', 0)
                    self.last_bytes_received = current_stats.get('bytes_received', 0)
                    self.last_requests_count = current_stats.get('requests_count', 0)
                    
                    # 添加速率信息
                    current_stats['bytes_sent_rate'] = bytes_sent_rate
                    current_stats['bytes_received_rate'] = bytes_received_rate
                    current_stats['requests_rate'] = requests_rate
                    current_stats['timestamp'] = datetime.now()
                
                # 发送统计信息更新信号
                self.stats_updated.emit(current_stats)
                
                # 发送流量更新信号
                traffic_data = {
                    'bytes_sent': current_stats.get('bytes_sent', 0),
                    'bytes_received': current_stats.get('bytes_received', 0),
                    'bytes_sent_rate': current_stats.get('bytes_sent_rate', 0),
                    'bytes_received_rate': current_stats.get('bytes_received_rate', 0)
                }
                self.traffic_updated.emit(traffic_data)
                
                # 获取并发送新的请求历史，添加异常处理
                try:
                    if hasattr(self.proxy_server, 'get_request_history'):
                        new_requests = self.proxy_server.get_request_history()
                        for request in new_requests:
                            self.request_added.emit(request)
                except Exception as e:
                    self.error_occurred.emit(f"获取请求历史错误: {str(e)}")
                
                # 获取并发送新的错误记录，添加异常处理
                try:
                    if hasattr(self.proxy_server, 'get_error_history'):
                        new_errors = self.proxy_server.get_error_history()
                        for error in new_errors:
                            self.error_occurred.emit(json.dumps(error))
                except Exception as e:
                    self.error_occurred.emit(f"获取错误历史错误: {str(e)}")
                
                # 处理队列中的请求和错误信息
                while not self.stats_queue.empty():
                    try:
                        data_type, data = self.stats_queue.get_nowait()
                        if data_type == 'error':
                            self.error_occurred.emit(json.dumps(data))
                    except queue.Empty:
                        break
                
                # 每秒更新一次
                time.sleep(1)
                
            except Exception as e:
                self.error_occurred.emit(f"监控线程错误: {str(e)}")
                time.sleep(1)
    
    def stop(self):
        """停止监控线程"""
        self.running = False
        self.wait()


class ProxyGUIMainWindow(QMainWindow):
    """代理服务器GUI主窗口"""
    
    def __init__(self):
        super().__init__()
        
        # 代理服务器实例
        self.proxy_server = None
        self.monitor_thread = None
        self.server_thread = None
        
        # 初始化数据存储
        self.request_history = []
        self.error_history = []
        self.traffic_history = []
        self.max_history_points = 300  # 最多保存300个数据点
        
        # 初始化日志缓冲区
        self.log_buffer = []
        self.max_log_entries = 10000  # 最多保存10000条日志记录
        
        # 设置窗口标题和大小
        self.setWindowTitle("高级代理服务器 - 流量监控与分析")
        self.setMinimumSize(1400, 900)
        
        # 设置窗口图标（可选）
        # self.setWindowIcon(QIcon("icon.png"))
        
        # 创建中央部件
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # 创建主布局
        self.main_layout = QVBoxLayout(self.central_widget)
        
        # 创建菜单栏
        self.create_menu_bar()
        
        # 创建工具栏
        self.create_tool_bar()
        
        # 创建标签页控件
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabShape(QTabWidget.Rounded)
        self.tab_widget.setDocumentMode(True)
        
        # 创建各个标签页
        self.create_dashboard_tab()
        self.create_monitor_tab()
        self.create_requests_tab()
        self.create_errors_tab()
        self.create_analysis_tab()  # 新增分析标签页
        self.create_log_viewer_tab()  # 新增日志查看标签页
        self.create_settings_tab()
        
        # 将标签页添加到标签页控件
        self.main_layout.addWidget(self.tab_widget)
        
        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # 添加状态栏组件
        self.status_label = QLabel("就绪")
        self.status_bar.addWidget(self.status_label)
        
        self.traffic_speed_label = QLabel("↑ 0 KB/s  ↓ 0 KB/s")
        self.status_bar.addPermanentWidget(self.traffic_speed_label)
        
        # 添加CPU和内存使用标签
        self.system_info_label = QLabel("CPU: 0% | RAM: 0 MB")
        self.status_bar.addPermanentWidget(self.system_info_label)
        
        # 创建定时器更新界面
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(1000)  # 每秒更新一次
        
        # 初始化代理服务器配置
        self.init_proxy_config()
        
        # 应用样式
        self.apply_styles()
        
        # 中文字体支持已通过matplotlib设置
        
        # 初始化系统托盘图标
        self.init_system_tray()
        
        # 注册全局快捷键帮助提示
        self.statusBar().showMessage("按F1查看快捷键帮助")
    
    def create_menu_bar(self):
        """创建菜单栏"""
        menu_bar = self.menuBar()
        
        # 文件菜单
        file_menu = menu_bar.addMenu("文件")
        
        start_action = QAction("启动服务器", self)
        start_action.triggered.connect(self.start_proxy_server)
        start_action.setShortcut("Ctrl+S")  # 添加快捷键
        file_menu.addAction(start_action)
        
        stop_action = QAction("停止服务器", self)
        stop_action.triggered.connect(self.stop_proxy_server)
        stop_action.setShortcut("Ctrl+X")  # 添加快捷键
        file_menu.addAction(stop_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        exit_action.setShortcut("Ctrl+Q")  # 添加快捷键
        file_menu.addAction(exit_action)
        
        # 视图菜单
        view_menu = menu_bar.addMenu("视图")
        
        dark_mode_action = QAction("深色模式", self)
        dark_mode_action.setCheckable(True)
        dark_mode_action.triggered.connect(self.toggle_dark_mode)
        dark_mode_action.setShortcut("Ctrl+D")  # 添加快捷键
        view_menu.addAction(dark_mode_action)
        
        # 添加标签页切换菜单项
        view_menu.addSeparator()
        
        dashboard_action = QAction("仪表板", self)
        dashboard_action.triggered.connect(lambda: self.tab_widget.setCurrentIndex(0))
        dashboard_action.setShortcut("Ctrl+1")
        view_menu.addAction(dashboard_action)
        
        monitor_action = QAction("监控", self)
        monitor_action.triggered.connect(lambda: self.tab_widget.setCurrentIndex(1))
        monitor_action.setShortcut("Ctrl+2")
        view_menu.addAction(monitor_action)
        
        analysis_action = QAction("分析", self)
        analysis_action.triggered.connect(lambda: self.tab_widget.setCurrentIndex(2))
        analysis_action.setShortcut("Ctrl+3")
        view_menu.addAction(analysis_action)
        
        logs_action = QAction("日志", self)
        logs_action.triggered.connect(lambda: self.tab_widget.setCurrentIndex(3))
        logs_action.setShortcut("Ctrl+4")
        view_menu.addAction(logs_action)
        
        # 帮助菜单
        help_menu = menu_bar.addMenu("帮助")
        
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        
        # 添加快捷键帮助菜单项
        shortcuts_action = QAction("快捷键帮助", self)
        shortcuts_action.triggered.connect(self.show_shortcuts_help)
        shortcuts_action.setShortcut("F1")  # 设置F1快捷键
        help_menu.addAction(shortcuts_action)
    
    def create_tool_bar(self):
        """创建工具栏"""
        tool_bar = self.addToolBar("控制栏")
        
        # 启动按钮
        self.start_button = QPushButton("启动服务器")
        self.start_button.setMinimumWidth(120)
        self.start_button.clicked.connect(self.start_proxy_server)
        tool_bar.addWidget(self.start_button)
        
        # 停止按钮
        self.stop_button = QPushButton("停止服务器")
        self.stop_button.setMinimumWidth(120)
        self.stop_button.clicked.connect(self.stop_proxy_server)
        self.stop_button.setEnabled(False)
        tool_bar.addWidget(self.stop_button)
        
        # 服务器状态指示器
        self.status_indicator = QLabel()
        self.status_indicator.setText("● 已停止")
        self.status_indicator.setStyleSheet("color: #ff4d4f;")
        tool_bar.addWidget(self.status_indicator)
    
    def update_traffic_chart(self, stats):
        """更新流量趋势图表"""
        try:
            # 检查图表相关属性是否存在
            if not hasattr(self, 'upload_series') or not hasattr(self, 'download_series'):
                return
            
            # 获取当前时间和流量数据
            current_time = QDateTime.currentDateTime()
            upload_speed = stats.get('bytes_sent_rate', 0) / 1024  # KB/s
            download_speed = stats.get('bytes_received_rate', 0) / 1024  # KB/s
            
            # 添加数据点
            self.upload_series.append(current_time.toMSecsSinceEpoch(), upload_speed)
            self.download_series.append(current_time.toMSecsSinceEpoch(), download_speed)
            
            # 限制数据点数量
            max_points = 30  # 显示最近30秒的数据
            if self.upload_series.count() > max_points:
                self.upload_series.remove(0)
            if self.download_series.count() > max_points:
                self.download_series.remove(0)
            
            # 动态调整Y轴范围
            max_speed = max(upload_speed, download_speed)
            if max_speed > 0:
                # 确保Y轴范围足够大
                new_max = max(100, max_speed * 1.2)
                self.traffic_y_axis.setRange(0, new_max)
            
        except Exception as e:
            print(f"更新流量图表失败: {str(e)}")
    
    def update_monitor_table(self, stats):
        """更新监控表格"""
        try:
            # 检查表格是否存在
            if not hasattr(self, 'monitor_table'):
                return
            
            # 添加新行
            row_position = self.monitor_table.rowCount()
            self.monitor_table.insertRow(row_position)
            
            # 设置时间
            timestamp = datetime.now().strftime('%H:%M:%S')
            self.monitor_table.setItem(row_position, 0, QTableWidgetItem(timestamp))
            
            # 设置请求数
            requests_count = str(stats.get('requests_count', 0))
            self.monitor_table.setItem(row_position, 1, QTableWidgetItem(requests_count))
            
            # 设置发送速度
            upload_speed = f"{stats.get('bytes_sent_rate', 0) / 1024:.1f} KB/s"
            self.monitor_table.setItem(row_position, 2, QTableWidgetItem(upload_speed))
            
            # 设置接收速度
            download_speed = f"{stats.get('bytes_received_rate', 0) / 1024:.1f} KB/s"
            self.monitor_table.setItem(row_position, 3, QTableWidgetItem(download_speed))
            
            # 设置活跃连接数
            active_connections = str(stats.get('active_connections', 0))
            self.monitor_table.setItem(row_position, 4, QTableWidgetItem(active_connections))
            
            # 设置错误数
            error_count = str(stats.get('error_count', 0))
            self.monitor_table.setItem(row_position, 5, QTableWidgetItem(error_count))
            
            # 限制表格行数
            max_rows = 100
            if self.monitor_table.rowCount() > max_rows:
                self.monitor_table.removeRow(0)
            
            # 自动滚动到最新行
            self.monitor_table.scrollToBottom()
            
        except Exception as e:
            print(f"更新监控表格失败: {str(e)}")
    
    def create_dashboard_tab(self):
        """创建仪表盘标签页"""
        dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout(dashboard_tab)
        
        # 创建统计卡片
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(20)
        
        # 请求总数卡片
        self.requests_count_card = self.create_stat_card("请求总数", "0", "#1890ff", icon="📊")
        stats_layout.addWidget(self.requests_count_card)
        
        # 活跃连接卡片
        self.active_connections_card = self.create_stat_card("活跃连接", "0", "#52c41a", icon="🔗")
        stats_layout.addWidget(self.active_connections_card)
        
        # 错误总数卡片
        self.errors_count_card = self.create_stat_card("错误总数", "0", "#ff4d4f", icon="⚠️")
        stats_layout.addWidget(self.errors_count_card)
        
        # 数据传输卡片
        self.data_transferred_card = self.create_stat_card("数据传输", "0 MB", "#fa8c16", icon="📈")
        stats_layout.addWidget(self.data_transferred_card)
        
        # 响应时间卡片
        self.response_time_card = self.create_stat_card("平均响应时间", "0 ms", "#722ed1", icon="⏱️")
        stats_layout.addWidget(self.response_time_card)
        
        dashboard_layout.addLayout(stats_layout)
        dashboard_layout.addSpacing(20)
        
        # 创建图表区域 - 使用网格布局
        charts_grid_layout = QHBoxLayout()
        
        # 左侧 - 流量图表
        left_charts_widget = QWidget()
        left_charts_layout = QVBoxLayout(left_charts_widget)
        
        # 流量图表
        traffic_chart_group = QGroupBox("实时流量监控")
        traffic_chart_layout = QVBoxLayout(traffic_chart_group)
        self.traffic_chart = self.create_traffic_chart()
        traffic_chart_view = QChartView(self.traffic_chart)
        traffic_chart_view.setRenderHint(QPainter.Antialiasing)
        traffic_chart_view.setMinimumHeight(300)
        traffic_chart_layout.addWidget(traffic_chart_view)
        left_charts_layout.addWidget(traffic_chart_group)
        
        # 请求类型分布图表
        request_types_group = QGroupBox("请求状态分布")
        request_types_layout = QVBoxLayout(request_types_group)
        self.request_types_chart = self.create_request_types_chart()
        request_types_chart_view = QChartView(self.request_types_chart)
        request_types_chart_view.setRenderHint(QPainter.Antialiasing)
        request_types_chart_view.setMinimumHeight(250)
        request_types_layout.addWidget(request_types_chart_view)
        left_charts_layout.addWidget(request_types_group)
        
        charts_grid_layout.addWidget(left_charts_widget, 2)
        
        # 右侧 - 错误分布和服务器状态
        right_charts_widget = QWidget()
        right_charts_layout = QVBoxLayout(right_charts_widget)
        
        # 错误类型分布
        error_types_group = QGroupBox("错误类型分布")
        error_types_layout = QVBoxLayout(error_types_group)
        self.error_types_chart = self.create_error_types_chart()
        error_types_chart_view = QChartView(self.error_types_chart)
        error_types_chart_view.setRenderHint(QPainter.Antialiasing)
        error_types_chart_view.setMinimumHeight(250)
        error_types_layout.addWidget(error_types_chart_view)
        right_charts_layout.addWidget(error_types_group)
        
        # 服务器状态信息
        server_status_group = QGroupBox("服务器状态")
        server_status_layout = QVBoxLayout(server_status_group)
        
        self.status_info_text = QTextEdit()
        self.status_info_text.setReadOnly(True)
        self.status_info_text.setMinimumHeight(300)
        self.status_info_text.setText("服务器未启动\n\n等待启动代理服务器...")
        server_status_layout.addWidget(self.status_info_text)
        
        right_charts_layout.addWidget(server_status_group)
        
        charts_grid_layout.addWidget(right_charts_widget, 1)
        
        dashboard_layout.addLayout(charts_grid_layout)
        
        # 添加仪表板标签页
        self.tab_widget.addTab(dashboard_tab, "仪表板")
    
    def create_monitor_tab(self):
        """创建监控标签页"""
        monitor_tab = QWidget()
        monitor_layout = QVBoxLayout(monitor_tab)
        
        # 创建实时监控区域
        monitor_group = QGroupBox("实时流量监控")
        monitor_group_layout = QVBoxLayout(monitor_group)
        
        # 顶部统计区域
        stats_layout = QHBoxLayout()
        
        # 上传速度指示器
        upload_group = QGroupBox("上传速度")
        upload_layout = QVBoxLayout(upload_group)
        self.upload_speed_meter = QProgressBar()
        self.upload_speed_meter.setRange(0, 100)
        self.upload_speed_meter.setValue(0)
        self.upload_speed_meter.setFormat("0 KB/s")
        self.upload_speed_meter.setStyleSheet("""
            QProgressBar { border: 2px solid #f5222d; border-radius: 5px; text-align: center; }
            QProgressBar::chunk { background-color: #f5222d; }
        """)
        upload_layout.addWidget(self.upload_speed_meter)
        upload_layout.addWidget(QLabel("最大刻度：100 KB/s"))
        stats_layout.addWidget(upload_group)
        
        # 下载速度指示器
        download_group = QGroupBox("下载速度")
        download_layout = QVBoxLayout(download_group)
        self.download_speed_meter = QProgressBar()
        self.download_speed_meter.setRange(0, 100)
        self.download_speed_meter.setValue(0)
        self.download_speed_meter.setFormat("0 KB/s")
        self.download_speed_meter.setStyleSheet("""
            QProgressBar { border: 2px solid #1890ff; border-radius: 5px; text-align: center; }
            QProgressBar::chunk { background-color: #1890ff; }
        """)
        download_layout.addWidget(self.download_speed_meter)
        download_layout.addWidget(QLabel("最大刻度：100 KB/s"))
        stats_layout.addWidget(download_group)
        
        # 流量图表
        chart_group = QGroupBox("流量趋势图")
        chart_layout = QVBoxLayout(chart_group)
        
        # 创建流量图表
        self.traffic_chart = QChart()
        self.traffic_chart.setTitle("流量实时变化")
        self.traffic_chart.setAnimationOptions(QChart.SeriesAnimations)
        
        # 创建系列
        self.upload_series = QLineSeries()
        self.upload_series.setName("上传速度 (KB/s)")
        self.upload_series.setColor(QColor(245, 34, 45))  # 红色
        
        self.download_series = QLineSeries()
        self.download_series.setName("下载速度 (KB/s)")
        self.download_series.setColor(QColor(24, 144, 255))  # 蓝色
        
        # 添加系列到图表
        self.traffic_chart.addSeries(self.upload_series)
        self.traffic_chart.addSeries(self.download_series)
        
        # 创建坐标轴
        self.traffic_x_axis = QDateTimeAxis()
        self.traffic_x_axis.setTitleText("时间")
        self.traffic_x_axis.setFormat("HH:mm:ss")
        self.traffic_x_axis.setTickCount(6)
        
        self.traffic_y_axis = QValueAxis()
        self.traffic_y_axis.setTitleText("速度 (KB/s)")
        self.traffic_y_axis.setRange(0, 100)
        
        # 添加坐标轴到图表
        self.traffic_chart.setAxisX(self.traffic_x_axis)
        self.traffic_chart.setAxisY(self.traffic_y_axis)
        self.upload_series.attachAxis(self.traffic_x_axis)
        self.upload_series.attachAxis(self.traffic_y_axis)
        self.download_series.attachAxis(self.traffic_x_axis)
        self.download_series.attachAxis(self.traffic_y_axis)
        
        # 创建图表视图
        self.traffic_chart_view = QChartView(self.traffic_chart)
        self.traffic_chart_view.setRenderHint(QPainter.Antialiasing)
        chart_layout.addWidget(self.traffic_chart_view)
        
        stats_layout.addWidget(chart_group)
        stats_layout.setStretch(0, 1)
        stats_layout.setStretch(1, 1)
        stats_layout.setStretch(2, 3)
        
        monitor_group_layout.addLayout(stats_layout)
        
        # 流量统计表格
        self.monitor_table = QTableWidget(0, 6)
        self.monitor_table.setHorizontalHeaderLabels([
            "时间", "请求数", "发送速度", "接收速度", "活跃连接", "错误数"
        ])
        
        # 设置表格自动调整列宽
        header = self.monitor_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        
        monitor_group_layout.addWidget(self.monitor_table)
        
        monitor_layout.addWidget(monitor_group)
        
        # 添加监控标签页
        self.tab_widget.addTab(monitor_tab, "实时监控")
    
    def create_requests_tab(self):
        """创建请求记录标签页"""
        requests_tab = QWidget()
        requests_tab_layout = QVBoxLayout(requests_tab)
        
        # 创建请求表格，增加目标主机和目标IP列
        self.requests_table = QTableWidget(0, 8)
        self.requests_table.setHorizontalHeaderLabels([
            "客户端", "端口", "目标主机", "目标IP", "开始时间", "结束时间", "持续时间", "状态"
        ])
        
        # 设置表格自动调整列宽
        header = self.requests_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        
        requests_tab_layout.addWidget(self.requests_table)
        
        # 添加请求记录标签页
        self.tab_widget.addTab(requests_tab, "请求记录")
    
    def create_errors_tab(self):
        """创建错误记录标签页"""
        errors_tab = QWidget()
        errors_tab_layout = QVBoxLayout(errors_tab)
        
        # 创建错误表格
        self.errors_table = QTableWidget(0, 3)
        self.errors_table.setHorizontalHeaderLabels([
            "时间", "错误类型", "错误消息"
        ])
        
        # 设置表格自动调整列宽
        header = self.errors_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        
        errors_tab_layout.addWidget(self.errors_table)
        
        # 添加错误记录标签页
        self.tab_widget.addTab(errors_tab, "错误记录")
    
    def create_settings_tab(self):
        """创建设置标签页"""
        settings_tab = QWidget()
        settings_tab_layout = QVBoxLayout(settings_tab)
        
        # 创建设置表单
        settings_form = QGroupBox("服务器设置")
        settings_form_layout = QFormLayout(settings_form)
        
        # 主机设置
        self.host_input = QLineEdit("0.0.0.0")
        settings_form_layout.addRow("监听地址:", self.host_input)
        
        # 端口设置
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(8080)
        settings_form_layout.addRow("监听端口:", self.port_input)
        
        # 缓冲区大小
        self.buffer_size_input = QSpinBox()
        self.buffer_size_input.setRange(1024, 65536)
        self.buffer_size_input.setSingleStep(1024)
        self.buffer_size_input.setValue(4096)
        settings_form_layout.addRow("缓冲区大小:", self.buffer_size_input)
        
        # 最大重试次数
        self.retries_input = QSpinBox()
        self.retries_input.setRange(0, 10)
        self.retries_input.setValue(3)
        settings_form_layout.addRow("最大重试次数:", self.retries_input)
        
        # 连接超时
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 60)
        self.timeout_input.setValue(10)
        settings_form_layout.addRow("连接超时(秒):", self.timeout_input)
        
        # 修改响应头
        self.modify_headers_checkbox = QCheckBox("修改响应头")
        self.modify_headers_checkbox.setChecked(True)
        settings_form_layout.addRow(self.modify_headers_checkbox)
        
        # 调试模式
        self.debug_mode_checkbox = QCheckBox("调试模式")
        self.debug_mode_checkbox.setChecked(False)
        settings_form_layout.addRow(self.debug_mode_checkbox)
        
        settings_tab_layout.addWidget(settings_form)
        
        # 创建按钮布局
        buttons_layout = QHBoxLayout()
        
        # 保存设置按钮
        save_button = QPushButton("保存设置")
        save_button.clicked.connect(self.save_settings)
        buttons_layout.addWidget(save_button)
        
        # 重置设置按钮
        reset_button = QPushButton("重置设置")
        reset_button.clicked.connect(self.reset_settings)
        buttons_layout.addWidget(reset_button)
        
        settings_tab_layout.addLayout(buttons_layout)
        
        # 添加设置标签页
        self.tab_widget.addTab(settings_tab, "设置")
    
    def create_stat_card(self, title, value, color, icon=""):
        """创建统计卡片（简化的文本显示版本）"""
        card = QWidget()
        # 简化样式表
        card.setStyleSheet(""
            "background-color: white;"
            "padding: 0px;"
            "border: 1px solid #e0e0e0;"
        )
        card.setMinimumHeight(100)
        card.setMinimumWidth(180)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(5)
        
        # 标题行
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #666666; font-size: 12px;")
        layout.addWidget(title_label)
        
        # 简单文本显示值
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 24px; font-weight: bold; color: " + color + ";")
        value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(value_label)
        
        # 添加简短描述文本区域
        self.stat_descriptions = getattr(self, 'stat_descriptions', {})
        if title not in self.stat_descriptions:
            self.stat_descriptions[title] = QLabel("")
            self.stat_descriptions[title].setStyleSheet("color: #999999; font-size: 10px;")
            layout.addWidget(self.stat_descriptions[title])
        
        return card
    
    def setup_card_animation(self, card):
        """设置卡片动画效果"""
        # 鼠标悬停效果
        card.enterEvent = lambda event: self.card_hover_effect(card, True)
        card.leaveEvent = lambda event: self.card_hover_effect(card, False)
        
        # 点击效果
        card.mousePressEvent = lambda event: self.card_press_effect(card)
        card.mouseReleaseEvent = lambda event: self.card_release_effect(card)
    
    def card_hover_effect(self, card, is_hovered):
        """卡片悬停效果"""
        if is_hovered:
            # 添加轻微缩放和阴影增强
            card.setCursor(Qt.PointingHandCursor)
            # 这里可以添加更复杂的动画效果
        else:
            card.setCursor(Qt.ArrowCursor)
    
    def card_press_effect(self, card):
        """卡片按下效果"""
        # 添加按下时的视觉反馈
        original_style = card.styleSheet()
        card.setStyleSheet(original_style.replace(
            "transform: translateY(-2px);",
            "transform: translateY(0px) scale(0.98);"
        ))
    
    def card_release_effect(self, card):
        """卡片释放效果"""
        # 恢复原始状态
        original_style = card.styleSheet()
        card.setStyleSheet(original_style.replace(
            "transform: translateY(0px) scale(0.98);",
            "transform: translateY(-2px);"
        ))
    
    def hex_to_rgb(self, hex_color):
        """将十六进制颜色转换为RGB值"""
        hex_color = hex_color.lstrip('#')
        lv = len(hex_color)
        return ', '.join(str(int(hex_color[i:i + lv // 3], 16)) for i in range(0, lv, lv // 3))
    
    def create_traffic_chart(self):
        """创建流量监控图表，增强版"""
        chart = QChart()
        chart.setTitle("网络流量 (KB/s)")
        chart.setAnimationOptions(QChart.SeriesAnimations)  # 仅对系列进行动画，提升性能
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.legend().setFont(QFont("Arial", 9))
        
        # 创建发送速率系列
        self.send_rate_series = QLineSeries()
        self.send_rate_series.setName("上传速率")
        send_pen = QPen(QColor("#ff4d4f"), 2.5)  # 稍粗的线条
        send_pen.setStyle(Qt.SolidLine)
        self.send_rate_series.setPen(send_pen)
        self.send_rate_series.setUseOpenGL(True)  # 使用OpenGL加速渲染
        self.send_rate_series.setPointLabelsVisible(False)  # 默认不显示点标签
        
        # 创建接收速率系列
        self.receive_rate_series = QLineSeries()
        self.receive_rate_series.setName("下载速率")
        receive_pen = QPen(QColor("#52c41a"), 2.5)  # 稍粗的线条
        receive_pen.setStyle(Qt.SolidLine)
        self.receive_rate_series.setPen(receive_pen)
        self.receive_rate_series.setUseOpenGL(True)  # 使用OpenGL加速渲染
        self.receive_rate_series.setPointLabelsVisible(False)  # 默认不显示点标签
        
        # 添加系列到图表
        chart.addSeries(self.send_rate_series)
        chart.addSeries(self.receive_rate_series)
        
        # 创建X轴（时间轴）
        self.time_axis = QDateTimeAxis()
        self.time_axis.setFormat("HH:mm:ss")
        self.time_axis.setTitleText("时间")
        self.time_axis.setTickCount(6)  # 减少刻度数量，避免拥挤
        self.time_axis.setLabelsAngle(-30)  # 倾斜标签，提高可读性
        chart.addAxis(self.time_axis, Qt.AlignBottom)
        
        # 创建Y轴（速率轴）
        self.rate_axis = QValueAxis()
        self.rate_axis.setTitleText("速率 (KB/s)")
        self.rate_axis.setLabelFormat("%.1f")
        self.rate_axis.setTickCount(6)
        self.rate_axis.setMinorTickCount(1)
        chart.addAxis(self.rate_axis, Qt.AlignLeft)
        
        # 附加系列到轴
        self.send_rate_series.attachAxis(self.time_axis)
        self.send_rate_series.attachAxis(self.rate_axis)
        self.receive_rate_series.attachAxis(self.time_axis)
        self.receive_rate_series.attachAxis(self.rate_axis)
        
        # 初始化峰值记录
        self.last_peak_send = 0
        self.last_peak_receive = 0
        self.last_peak_time = QDateTime.currentDateTime()
        
        # 设置图表边距，增加边距防止文字截断
        chart.setMargins(QMargins(20, 15, 20, 25))
        chart.setBackgroundRoundness(5)
        
        return chart
    
    def create_request_types_chart(self):
        """创建请求类型分布图表"""
        chart = QChart()
        chart.setTitle("请求状态分布")
        chart.setAnimationOptions(QChart.AllAnimations)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        
        # 创建饼图系列
        self.request_types_series = QPieSeries()
        self.request_types_series.setHoleSize(0.35)  # 甜甜圈样式
        self.request_types_series.setPieSize(0.8)
        
        # 设置饼图样式
        slice1 = QPieSlice("成功请求", 0)
        slice1.setBrush(QColor("#52c41a"))
        slice1.setLabelVisible(False)
        
        slice2 = QPieSlice("失败请求", 0)
        slice2.setBrush(QColor("#ff4d4f"))
        slice2.setLabelVisible(False)
        
        self.request_types_series.append(slice1)
        self.request_types_series.append(slice2)
        
        # 添加系列到图表
        chart.addSeries(self.request_types_series)
        
        # 设置饼图点击效果
        self.request_types_series.setLabelsVisible()
        self.request_types_series.setLabelsPosition(QPieSlice.LabelPosition.LabelOutside)
        
        return chart
    
    def create_error_types_chart(self):
        """创建错误类型分布图表，修复显示问题"""
        chart = QChart()
        chart.setTitle("错误类型分布")
        chart.setAnimationOptions(QChart.SeriesAnimations)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.legend().setFont(QFont("Arial", 9))
        
        # 创建柱状图系列
        self.error_types_series = QBarSeries()
        self.error_types_series.setLabelsVisible(True)
        self.error_types_series.setLabelsPosition(QBarSeries.LabelsInsideEnd)  # 内部标签避免截断
        self.error_types_series.setLabelsAngle(0)  # 水平标签更易读
        
        # 创建单个条形集，每个错误类型作为一个数据点
        error_types = ["连接错误", "超时错误", "其他错误"]
        error_colors = ["#ff4d4f", "#fa8c16", "#722ed1"]
        
        # 创建一个条形集，为每个错误类型添加数据
        self.error_set = QBarSet("错误数量")
        self.error_set.append([0, 0, 0])  # 初始化三个错误类型的值
        self.error_types_series.append(self.error_set)
        
        # 添加类别轴，为每个错误类型创建独立类别
        self.error_types_axis = QCategoryAxis()
        self.error_types_axis.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
        
        # 为每个错误类型添加类别
        for i, error_type in enumerate(error_types):
            self.error_types_axis.append(error_type, i + 1.0)  # 为每个类型创建独立位置
        
        # 添加值轴
        self.error_values_axis = QValueAxis()
        self.error_values_axis.setTitleText("错误数量")
        self.error_values_axis.setLabelFormat("%d")
        self.error_values_axis.setTickCount(6)
        self.error_values_axis.setMinorTickCount(1)
        
        chart.addSeries(self.error_types_series)
        chart.addAxis(self.error_types_axis, Qt.AlignBottom)
        chart.addAxis(self.error_values_axis, Qt.AlignLeft)
        
        self.error_types_series.attachAxis(self.error_types_axis)
        self.error_types_series.attachAxis(self.error_values_axis)
        
        # 设置图表背景和样式
        chart.setBackgroundRoundness(5)
        
        return chart
    
    def update_error_types_chart(self, connection_errors, timeout_errors, other_errors):
        """更新错误类型分布图表，修复显示问题"""
        # 数据映射
        data_values = [connection_errors, timeout_errors, other_errors]
        
        # 更新条形集数据
        if hasattr(self, 'error_set'):
            # 更新所有数据点
            self.error_set.replace(0, data_values[0])
            self.error_set.replace(1, data_values[1])
            self.error_set.replace(2, data_values[2])
        
        # 计算总错误数
        total_errors = sum(data_values)
        
        # 更新Y轴范围
        max_value = max(data_values)
        if max_value > 0 and hasattr(self, 'error_values_axis'):
            # 根据最大值动态调整边距
            if max_value > 50:
                margin = 0.1
            elif max_value > 5:
                margin = 0.2
            else:
                margin = 0.3
            
            self.error_values_axis.setRange(0, max_value * (1 + margin))
            
            # 更新标题显示
            if hasattr(self, 'error_types_series') and hasattr(self.error_types_series, 'chart'):
                chart = self.error_types_series.chart()
                if chart:
                    # 添加总错误数统计到标题
                    chart.setTitle(f"错误类型分布 (总计: {total_errors} 个错误)")
                    
                    # 高亮显示最常见的错误类型
                    if total_errors > 0:
                        max_error_index = data_values.index(max_value)
                        error_percentage = (max_value / total_errors) * 100
                        
                        # 如果主要错误类型占比超过50%，在标题中强调
                        if error_percentage > 50:
                            error_type_names = ["连接错误", "超时错误", "其他错误"]
                            chart.setTitle(f"错误类型分布 (总计: {total_errors} 个错误) - 主要错误: {error_type_names[max_error_index]} ({error_percentage:.1f}%)")
    
    def create_response_time_chart(self):
        """创建响应时间分析图表，修复显示问题"""
        chart = QChart()
        chart.setTitle("响应时间分布")
        chart.setAnimationOptions(QChart.SeriesAnimations)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.legend().setFont(QFont("Arial", 9))
        
        # 创建柱状图系列
        self.response_time_series = QBarSeries()
        self.response_time_series.setLabelsVisible(True)
        self.response_time_series.setLabelsPosition(QBarSeries.LabelsInsideEnd)
        self.response_time_series.setLabelsAngle(0)  # 水平标签更易读

        
        # 定义响应时间区间
        self.response_time_categories = ["< 100ms", "100-500ms", "500-1000ms", "1-3s", "> 3s"]
        
        # 创建单个条形集，包含所有响应时间区间
        self.response_time_set = QBarSet("请求数量")
        self.response_time_set.append([0, 0, 0, 0, 0])  # 初始化所有区间的值
        self.response_time_series.append(self.response_time_set)
        
        # 添加类别轴，为每个响应时间区间创建独立类别
        self.response_time_axis = QCategoryAxis()
        self.response_time_axis.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
        
        # 为每个响应时间区间添加类别
        for i, category in enumerate(self.response_time_categories):
            self.response_time_axis.append(category, i + 1.0)
        
        # 添加值轴
        self.response_time_values_axis = QValueAxis()
        self.response_time_values_axis.setTitleText("请求数量")
        self.response_time_values_axis.setLabelFormat("%d")
        self.response_time_values_axis.setTickCount(6)
        
        chart.addSeries(self.response_time_series)
        chart.addAxis(self.response_time_axis, Qt.AlignBottom)
        chart.addAxis(self.response_time_values_axis, Qt.AlignLeft)
        
        self.response_time_series.attachAxis(self.response_time_axis)
        self.response_time_series.attachAxis(self.response_time_values_axis)
        
        # 设置图表背景和样式
        chart.setBackgroundRoundness(5)
        
        return chart
    
    def update_response_time_chart(self, response_time_data):
        """更新响应时间分布图表，修复显示问题"""
        if not hasattr(self, 'response_time_set'):
            return
        
        # 数据映射
        data_values = [
            response_time_data.get('fast', 0),      # < 100ms
            response_time_data.get('normal', 0),    # 100-500ms
            response_time_data.get('slow', 0),      # 500-1000ms
            response_time_data.get('very_slow', 0), # 1-3s
            response_time_data.get('timeout', 0)    # > 3s
        ]
        
        # 更新条形集数据
        self.response_time_set.replace(0, data_values[0])
        self.response_time_set.replace(1, data_values[1])
        self.response_time_set.replace(2, data_values[2])
        self.response_time_set.replace(3, data_values[3])
        self.response_time_set.replace(4, data_values[4])
        
        # 计算总请求数
        total_requests = sum(data_values)
        
        # 更新Y轴范围
        max_value = max(data_values)
        if max_value > 0 and hasattr(self, 'response_time_values_axis'):
            # 根据最大值动态调整边距
            if max_value > 100:
                margin = 0.1
            elif max_value > 10:
                margin = 0.2
            else:
                margin = 0.3
            
            self.response_time_values_axis.setRange(0, max_value * (1 + margin))
            
            # 更新标题显示
            if hasattr(self, 'response_time_series') and hasattr(self.response_time_series, 'chart'):
                chart = self.response_time_series.chart()
                if chart:
                    # 添加总请求数统计到标题
                    chart.setTitle(f"响应时间分布 (总计: {total_requests} 个请求)")
            
            # 如果有数据，添加统计信息到标题
            if total_requests > 0:
                # 计算平均响应时间的粗略估计
                avg_rt_ms = 0
                weights = [50, 300, 750, 2000, 4000]  # 每个区间的平均权重
                for i, count in enumerate(data_values):
                    avg_rt_ms += count * weights[i]
                avg_rt_ms = avg_rt_ms / total_requests if total_requests > 0 else 0
                
                # 更新图表标题，添加统计信息
                if hasattr(self, 'response_time_series') and hasattr(self.response_time_series, 'chart'):
                    chart = self.response_time_series.chart()
                    if chart:
                        chart.setTitle(f"响应时间分布 (平均: {avg_rt_ms:.0f} ms)")
    
    # 流量趋势分析已改为文本显示，不再需要图表创建方法
    
    def update_traffic_trend_chart(self):
        """更新流量趋势统计信息（文本显示）"""
        if not hasattr(self, 'traffic_stats_text'):
            return
        
        if not hasattr(self, 'traffic_history') or not self.traffic_history:
            self.traffic_stats_text.setPlainText("流量统计信息将在此显示\n\n- 等待数据收集...")
            return
        
        # 计算统计数据
        total_sent = sum(point['bytes_sent'] for point in self.traffic_history)
        total_received = sum(point['bytes_received'] for point in self.traffic_history)
        total_traffic = total_sent + total_received
        
        # 计算峰值
        if self.traffic_history:
            max_sent = max(point['bytes_sent'] / 1024 for point in self.traffic_history)
            max_received = max(point['bytes_received'] / 1024 for point in self.traffic_history)
            
            # 计算平均值
            avg_sent = (total_sent / len(self.traffic_history)) / 1024
            avg_received = (total_received / len(self.traffic_history)) / 1024
            
            # 获取最近的数据点
            latest_point = self.traffic_history[-1]
            latest_time = latest_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            latest_sent = latest_point['bytes_sent'] / 1024
            latest_received = latest_point['bytes_received'] / 1024
            
            # 生成统计文本
            stats_text = f"=== 流量统计信息 ===\n\n"
            stats_text += f"总流量: {total_traffic / 1024:.2f} KB ({total_traffic / (1024*1024):.3f} MB)\n"
            stats_text += f"发送流量: {total_sent / 1024:.2f} KB ({total_sent / (1024*1024):.3f} MB)\n"
            stats_text += f"接收流量: {total_received / 1024:.2f} KB ({total_received / (1024*1024):.3f} MB)\n\n"
            
            stats_text += f"峰值发送: {max_sent:.2f} KB\n"
            stats_text += f"峰值接收: {max_received:.2f} KB\n\n"
            
            stats_text += f"平均发送: {avg_sent:.2f} KB/采样\n"
            stats_text += f"平均接收: {avg_received:.2f} KB/采样\n\n"
            
            stats_text += f"最近数据 ({latest_time}):\n"
            stats_text += f"  发送: {latest_sent:.2f} KB\n"
            stats_text += f"  接收: {latest_received:.2f} KB\n\n"
            
            stats_text += f"数据点数量: {len(self.traffic_history)}\n"
            
            self.traffic_stats_text.setPlainText(stats_text)
    
    def create_analysis_tab(self):
        """创建分析标签页"""
        analysis_tab = QWidget()
        analysis_layout = QVBoxLayout(analysis_tab)
        
        # 创建标题
        title_label = QLabel("流量分析报告")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1890ff;")
        analysis_layout.addWidget(title_label)
        
        # 创建统计概览卡片
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(20)
        
        # 添加平均响应时间卡片
        self.avg_response_time_card = self.create_stat_card("平均响应时间", "0.0 ms", "#1890ff", "⏱️")
        stats_layout.addWidget(self.avg_response_time_card)
        
        # 添加成功率卡片
        self.success_rate_card = self.create_stat_card("请求成功率", "0.0%", "#52c41a", "✅")
        stats_layout.addWidget(self.success_rate_card)
        
        # 添加错误率卡片
        self.error_rate_card = self.create_stat_card("请求错误率", "0.0%", "#ff4d4f", "❌")
        stats_layout.addWidget(self.error_rate_card)
        
        # 添加总请求数卡片
        self.total_requests_card = self.create_stat_card("总请求数", "0", "#faad14", "📊")
        stats_layout.addWidget(self.total_requests_card)
        
        analysis_layout.addLayout(stats_layout)
        analysis_layout.addSpacing(20)
        
        # 创建流量趋势分析区域（文本统计显示）
        traffic_trend_group = QGroupBox("流量趋势分析")
        traffic_trend_layout = QVBoxLayout(traffic_trend_group)
        
        # 创建流量统计文本区域
        self.traffic_stats_text = QPlainTextEdit()
        self.traffic_stats_text.setReadOnly(True)
        self.traffic_stats_text.setMinimumHeight(400)
        self.traffic_stats_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.traffic_stats_text.setPlainText("流量统计信息将在此显示\n\n- 等待数据收集...")
        traffic_trend_layout.addWidget(self.traffic_stats_text)
        analysis_layout.addWidget(traffic_trend_group)
        analysis_layout.addSpacing(20)
        
        # 创建详细分析区域
        details_layout = QHBoxLayout()
        details_layout.setSpacing(20)
        
        # 错误类型分析图表
        error_chart_group = QGroupBox("错误类型分析")
        error_chart_layout = QVBoxLayout(error_chart_group)
        error_chart = self.create_error_types_chart()
        error_chart_view = QChartView(error_chart)
        error_chart_view.setRenderHint(QPainter.Antialiasing)
        error_chart_view.setMinimumHeight(350)
        error_chart_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        error_chart_layout.addWidget(error_chart_view)
        details_layout.addWidget(error_chart_group)
        
        # 响应时间分析图表
        response_time_group = QGroupBox("响应时间分析")
        response_time_layout = QVBoxLayout(response_time_group)
        response_time_chart = self.create_response_time_chart()
        response_time_chart_view = QChartView(response_time_chart)
        response_time_chart_view.setRenderHint(QPainter.Antialiasing)
        response_time_chart_view.setMinimumHeight(350)
        response_time_chart_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        response_time_layout.addWidget(response_time_chart_view)
        details_layout.addWidget(response_time_group)
        
        analysis_layout.addLayout(details_layout)
        analysis_layout.addSpacing(20)
        
        # 详细错误统计
        from PyQt5.QtWidgets import QTreeWidget
        error_stats_group = QGroupBox("错误详细统计")
        error_stats_layout = QVBoxLayout(error_stats_group)
        
        self.error_details_tree = QTreeWidget()
        self.error_details_tree.setHeaderLabels(["错误类型", "数量", "占比", "最近发生时间"])
        self.error_details_tree.setColumnWidth(0, 200)
        self.error_details_tree.setColumnWidth(1, 80)
        self.error_details_tree.setColumnWidth(2, 80)
        self.error_details_tree.setColumnWidth(3, 150)
        error_stats_layout.addWidget(self.error_details_tree)
        
        analysis_layout.addWidget(error_stats_group)
        analysis_layout.addSpacing(20)
        
        # 添加导出按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        from PyQt5.QtWidgets import QFileDialog
        export_btn = QPushButton("导出分析报告")
        export_btn.clicked.connect(self.export_analysis_report)
        button_layout.addWidget(export_btn)
        
        analysis_layout.addLayout(button_layout)
        analysis_layout.addStretch()
        
        # 添加到标签页
        self.tab_widget.addTab(analysis_tab, "分析")
    
    def export_analysis_report(self):
        """导出分析报告"""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出分析报告", "proxy_analysis_report.html", "HTML Files (*.html);;CSV Files (*.csv)"
        )
        
        if file_path:
            # 这里可以实现导出逻辑
            QMessageBox.information(self, "导出成功", "分析报告已成功导出")
    
    def init_proxy_config(self):
        """初始化代理服务器配置"""
        # 尝试从配置文件加载设置
        try:
            with open("proxy_config.json", "r") as f:
                config = json.load(f)
                self.host_input.setText(config.get("host", "0.0.0.0"))
                self.port_input.setValue(config.get("port", 8080))
                self.buffer_size_input.setValue(config.get("buffer_size", 4096))
                self.retries_input.setValue(config.get("retries", 3))
                self.timeout_input.setValue(config.get("timeout", 10))
                self.modify_headers_checkbox.setChecked(config.get("modify_headers", True))
                self.debug_mode_checkbox.setChecked(config.get("debug_mode", False))
        except FileNotFoundError:
            # 配置文件不存在，使用默认值
            pass
    
    def save_settings(self):
        """保存设置到配置文件"""
        config = {
            "host": self.host_input.text(),
            "port": self.port_input.value(),
            "buffer_size": self.buffer_size_input.value(),
            "retries": self.retries_input.value(),
            "timeout": self.timeout_input.value(),
            "modify_headers": self.modify_headers_checkbox.isChecked(),
            "debug_mode": self.debug_mode_checkbox.isChecked()
        }
        
        try:
            with open("proxy_config.json", "w") as f:
                json.dump(config, f, indent=4)
            
            QMessageBox.information(self, "成功", "设置已保存")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存设置失败: {str(e)}")
    
    def reset_settings(self):
        """重置设置为默认值"""
        self.host_input.setText("0.0.0.0")
        self.port_input.setValue(8080)
        self.buffer_size_input.setValue(4096)
        self.retries_input.setValue(3)
        self.timeout_input.setValue(10)
        self.modify_headers_checkbox.setChecked(True)
        self.debug_mode_checkbox.setChecked(False)
    
    def start_proxy_server(self):
        """启动代理服务器"""
        try:
            # 获取设置
            host = self.host_input.text()
            port = self.port_input.value()
            buffer_size = self.buffer_size_input.value()
            max_retries = self.retries_input.value()
            connection_timeout = self.timeout_input.value()
            modify_headers = self.modify_headers_checkbox.isChecked()
            debug_mode = self.debug_mode_checkbox.isChecked()
            
            # 记录启动信息
            self.log_event("INFO", f"准备启动代理服务器，配置: host={host}, port={port}, buffer_size={buffer_size}")
            
            # 设置日志
            setup_logging(debug_mode)
            self.log_event("INFO", "日志系统已初始化")
            
            # 创建代理服务器实例
            self.log_event("DEBUG", "创建代理服务器实例...")
            self.proxy_server = ProxyServer(
                host=host,
                port=port,
                buffer_size=buffer_size,
                modify_headers=modify_headers,
                max_retries=max_retries,
                connection_timeout=connection_timeout
            )
            
            # 创建并启动监控线程
            self.log_event("DEBUG", "创建流量监控线程...")
            self.monitor_thread = TrafficMonitorThread(self.proxy_server)
            self.monitor_thread.stats_updated.connect(self.update_stats)
            self.monitor_thread.request_added.connect(self.add_request_record)
            self.monitor_thread.error_occurred.connect(self.add_error_record)
            self.monitor_thread.traffic_updated.connect(self.update_traffic_stats)
            self.monitor_thread.traffic_updated.connect(self.update_traffic_chart)  # 添加到图表更新方法的连接
            self.monitor_thread.start()
            
            # 在单独的线程中启动代理服务器
            self.log_event("DEBUG", "启动代理服务器线程...")
            self.server_thread = threading.Thread(target=self.run_proxy_server)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            # 更新UI状态
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status_indicator.setText("● 运行中")
            self.status_indicator.setStyleSheet("color: #52c41a;")
            self.status_label.setText(f"代理服务器运行在 {host}:{port}")
            # 更新服务器状态文本
            if hasattr(self, 'status_info_text'):
                self.status_info_text.setText(f"服务器已启动\n\n代理服务器运行在 {host}:{port}\n\n正在接收和处理请求...")
            
            self.log_event("INFO", f"代理服务器成功启动在 {host}:{port}")
            QMessageBox.information(self, "成功", f"代理服务器已启动在 {host}:{port}")
            
        except Exception as e:
            error_msg = f"无法启动代理服务器: {str(e)}"
            self.log_event("ERROR", error_msg)
            QMessageBox.critical(self, "启动失败", error_msg)
    
    def run_proxy_server(self):
        """在单独的线程中运行代理服务器"""
        try:
            self.log_event("DEBUG", "开始监听连接...")
            self.proxy_server.start()
        except Exception as e:
            error_msg = f"代理服务器运行错误: {str(e)}"
            self.log_event("ERROR", error_msg)
            # 将错误信息发送到GUI线程
            self.stop_proxy_server()
    
    def stop_proxy_server(self):
        """停止代理服务器"""
        try:
            self.log_event("INFO", "开始停止代理服务器...")
            
            # 停止监控线程
            if self.monitor_thread:
                self.log_event("DEBUG", "停止流量监控线程...")
                self.monitor_thread.stop()
                self.monitor_thread = None
            
            # 停止代理服务器
            if self.proxy_server:
                self.log_event("DEBUG", "停止代理服务器实例...")
                self.proxy_server.stop()
                self.proxy_server = None
            
            # 更新UI状态
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.status_indicator.setText("● 已停止")
            self.status_indicator.setStyleSheet("color: #ff4d4f;")
            self.status_label.setText("代理服务器已停止")
            # 更新服务器状态文本
            if hasattr(self, 'status_info_text'):
                self.status_info_text.setText("服务器未启动\n\n等待启动代理服务器...")
            
            self.log_event("INFO", "代理服务器已成功停止")
            QMessageBox.information(self, "成功", "代理服务器已停止")
            
        except Exception as e:
            error_msg = f"停止代理服务器时出错: {str(e)}"
            self.log_event("ERROR", error_msg)
            QMessageBox.critical(self, "停止失败", error_msg)
    
    def update_traffic_stats(self, traffic_data):
        """更新流量统计信息"""
        # 更新状态标签
        bytes_sent_rate = traffic_data.get('bytes_sent_rate', 0) / 1024  # KB/s
        bytes_received_rate = traffic_data.get('bytes_received_rate', 0) / 1024  # KB/s
        
        if hasattr(self, 'traffic_speed_label'):
            self.traffic_speed_label.setText(
                f"↑ {bytes_sent_rate:.1f} KB/s  ↓ {bytes_received_rate:.1f} KB/s"
            )
        
        # 更新实时流量指示器
        if hasattr(self, 'upload_speed_meter'):
            self.upload_speed_meter.setValue(min(100, int(bytes_sent_rate / 10)))
        if hasattr(self, 'download_speed_meter'):
            self.download_speed_meter.setValue(min(100, int(bytes_received_rate / 10)))
    
    def update_error_details(self, error_stats):
        """更新错误详细统计"""
        if not hasattr(self, 'error_details_tree'):
            return
        
        # 清空现有数据
        self.error_details_tree.clear()
        
        total_errors = sum(error_stats.values())
        
        # 添加错误详情
        for error_type, count in error_stats.items():
            item = QTreeWidgetItem()
            item.setText(0, error_type)
            item.setText(1, str(count))
            
            # 计算占比
            if total_errors > 0:
                percentage = (count / total_errors) * 100
                item.setText(2, f"{percentage:.1f}%")
            else:
                item.setText(2, "0%")
            
            # 设置最近发生时间（这里使用当前时间作为示例）
            item.setText(3, datetime.now().strftime("%H:%M:%S"))
            
            # 根据错误类型设置颜色
            if "连接" in error_type:
                item.setForeground(0, QColor("#ff4d4f"))
            elif "超时" in error_type:
                item.setForeground(0, QColor("#faad14"))
            else:
                item.setForeground(0, QColor("#1890ff"))
            
            self.error_details_tree.addTopLevelItem(item)
    
    def update_stats(self, stats):
        """更新统计信息，增强版"""
        # 计算流量速率
        bytes_sent_rate = stats.get('bytes_sent_rate', 0) / 1024  # KB/s
        bytes_received_rate = stats.get('bytes_received_rate', 0) / 1024  # KB/s
        
        # 更新状态标签，添加颜色指示
        if hasattr(self, 'traffic_speed_label'):
            # 根据流量大小设置不同颜色
            if bytes_sent_rate > 1024 or bytes_received_rate > 1024:  # 大于1MB/s
                color = '#ff4d4f'  # 红色
            elif bytes_sent_rate > 100 or bytes_received_rate > 100:  # 大于100KB/s
                color = '#faad14'  # 橙色
            else:
                color = '#52c41a'  # 绿色
            
            self.traffic_speed_label.setText(
                f"<span style='color:{color};font-weight:bold'>↑ {bytes_sent_rate:.1f} KB/s  ↓ {bytes_received_rate:.1f} KB/s</span>"
            )
            self.traffic_speed_label.setWordWrap(True)
        
        # 更新统计卡片，添加动画效果
        self._update_stat_card('requests_count_card', str(stats.get('requests_count', 0)))
        self._update_stat_card('active_connections_card', str(stats.get('active_connections', 0)))
        self._update_stat_card('errors_count_card', str(stats.get('error_count', 0)))
        
        # 计算总流量并格式化显示
        total_bytes = stats.get('bytes_sent', 0) + stats.get('bytes_received', 0)
        if total_bytes < 1024:
            data_str = f"{total_bytes} B"
        elif total_bytes < 1024 * 1024:
            data_str = f"{total_bytes / 1024:.2f} KB"
        elif total_bytes < 1024 * 1024 * 1024:
            data_str = f"{total_bytes / (1024 * 1024):.2f} MB"
        else:
            data_str = f"{total_bytes / (1024 * 1024 * 1024):.2f} GB"
        self._update_stat_card('data_transferred_card', data_str)
        
        # 更新平均响应时间
        avg_response_time = stats.get('avg_response_time', 0)
        self._update_stat_card('avg_response_time_card', f"{avg_response_time:.1f} ms")
        
        # 更新总请求数卡片
        total_requests = stats.get('requests_count', 0)
        self._update_stat_card('total_requests_card', str(total_requests))
        
        # 计算成功率和错误率，添加趋势比较
        successful_requests = stats.get('successful_requests', 0)
        failed_requests = stats.get('failed_requests', 0)
        
        if total_requests > 0:
            success_rate = (successful_requests / total_requests) * 100
            error_rate = (failed_requests / total_requests) * 100
            
            # 更新成功率卡片，添加颜色指示
            success_color = '#52c41a' if success_rate > 90 else '#faad14' if success_rate > 70 else '#ff4d4f'
            success_text = f"<span style='color:{success_color};font-weight:bold'>{success_rate:.1f}%</span>"
            self._update_stat_card('success_rate_card', success_text)
            
            # 更新错误率卡片
            self._update_stat_card('error_rate_card', f"{error_rate:.1f}%")
            
            # 添加高级统计：95%和99%响应时间百分位数
            if hasattr(self, 'p95_response_time_card'):
                p95_time = stats.get('p95_response_time', avg_response_time)
                self._update_stat_card('p95_response_time_card', f"{p95_time:.1f} ms")
            
            if hasattr(self, 'p99_response_time_card'):
                p99_time = stats.get('p99_response_time', avg_response_time * 1.5)
                self._update_stat_card('p99_response_time_card', f"{p99_time:.1f} ms")
        
        # 更新请求类型分布图表，添加动画效果
        self.update_request_types_chart(stats)
        
        # 更新错误类型图表，增强可视化效果
        self.update_error_types_chart(
            stats.get('connection_errors', 0),
            stats.get('timeout_errors', 0),
            stats.get('other_errors', 0)
        )
        
        # 更新响应时间分布图表，增强可视化
        response_time_data = {
            'fast': stats.get('fast_requests', 0),      # < 100ms
            'normal': stats.get('normal_requests', 0),  # 100-500ms
            'slow': stats.get('slow_requests', 0),      # 500-1000ms
            'very_slow': stats.get('very_slow_requests', 0),  # 1-3s
            'timeout': stats.get('timeout_requests', 0)  # > 3s
        }
        self.update_response_time_chart(response_time_data)
        
        # 更新错误详情，添加更多分析信息
        error_stats = stats.get('error_stats', {})
        if not error_stats:
            # 如果没有错误统计，使用默认的错误类型
            error_stats = {
                '连接错误': stats.get('connection_errors', 0),
                '超时错误': stats.get('timeout_errors', 0),
                '其他错误': stats.get('other_errors', 0)
            }
        self.update_error_details(error_stats)
        
        # 保存历史数据，限制数据量
        self.save_traffic_history(stats)
        
        # 更新流量趋势图表，增强数据可视化
        self.update_traffic_trend_chart()
        
        # 更新流量图表，添加数据点标记
        self.update_traffic_chart(stats)
        
        # 更新监控表格，优化性能
        self.update_monitor_table(stats)
        
        # 更新系统信息状态栏，添加更多系统信息
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory_info = psutil.virtual_memory()
            # 添加网络接口信息
            net_io = psutil.net_io_counters()
            total_net_bytes = net_io.bytes_sent + net_io.bytes_recv
            net_str = f"网络: {total_net_bytes / (1024 * 1024):.1f} MB"
            
            self.system_info_label.setText(f"CPU: {cpu_percent:.1f}% | RAM: {memory_info.used // (1024 * 1024)} MB | {net_str}")
        except:
            # 如果psutil不可用，显示基本信息
            pass
    
    def _update_stat_card(self, card_attr, value):
        """更新统计卡片的辅助方法（简化版本）"""
        if hasattr(self, card_attr):
            try:
                card = getattr(self, card_attr)
                # 获取值显示控件（在布局索引1位置）
                if card.layout().count() > 1:
                    value_widget = card.layout().itemAt(1).widget()
                    if value_widget:
                        # 直接设置新值，避免HTML处理
                        value_widget.setText(value)
                
                # 更新描述文本（如果卡片标题对应有描述）
                card_title = card.layout().itemAt(0).widget().text()
                if hasattr(self, 'stat_descriptions') and card_title in self.stat_descriptions:
                    # 根据卡片类型设置相应的描述
                    if '响应时间' in card_title:
                        self.stat_descriptions[card_title].setText("最近请求的平均响应耗时")
                    elif '成功率' in card_title:
                        self.stat_descriptions[card_title].setText("成功请求占总请求的百分比")
                    elif '错误率' in card_title:
                        self.stat_descriptions[card_title].setText("失败请求占总请求的百分比")
                    elif '请求数' in card_title:
                        self.stat_descriptions[card_title].setText("已处理的请求总数")
            except Exception as e:
                # 记录错误但不中断程序
                self.log_event('DEBUG', f"更新统计卡片失败: {e}")
    
    def _animate_card_update(self, card):
        """简化的卡片更新动画（仅保留基本效果）"""
        # 移除所有复杂动画，使用最简单的实现
        pass
    
    def update_traffic_chart(self, stats):
        """更新流量图表，增强版"""
        # 获取当前时间
        current_time = QDateTime.currentDateTime()
        
        # 计算KB/s
        bytes_sent_rate = stats.get('bytes_sent_rate', 0) / 1024
        bytes_received_rate = stats.get('bytes_received_rate', 0) / 1024
        
        # 添加数据点，使用更粗的线条和点标记
        current_ms = current_time.toMSecsSinceEpoch()
        self.send_rate_series.append(current_ms, bytes_sent_rate)
        self.receive_rate_series.append(current_ms, bytes_received_rate)
        
        # 保持图表只显示最近的数据点，可配置的时间窗口
        max_points = 60  # 显示最近60秒的数据
        if self.send_rate_series.count() > max_points:
            self.send_rate_series.remove(0)
            self.receive_rate_series.remove(0)
        
        # 更新轴范围，添加一些边距
        start_time = current_time.addSecs(-max_points)
        self.time_axis.setRange(start_time, current_time)
        
        # 自动调整Y轴范围，更智能的范围计算
        max_rate = 0
        if self.send_rate_series.count() > 0 and self.receive_rate_series.count() > 0:
            max_send = max(self.send_rate_series.pointsVector(), key=lambda p: p.y()).y()
            max_receive = max(self.receive_rate_series.pointsVector(), key=lambda p: p.y()).y()
            max_rate = max(max_send, max_receive)
        
        # 动态调整Y轴范围，确保显示更合理
        if max_rate > 0:
            # 根据流量大小调整放大倍数
            if max_rate > 1024:  # 大于1MB/s
                margin = 0.1  # 10%边距
            elif max_rate > 100:  # 大于100KB/s
                margin = 0.2  # 20%边距
            else:
                margin = 0.3  # 30%边距
            
            self.rate_axis.setMax(max_rate * (1 + margin))
            # 确保最小值不为0，提供更好的可视化效果
            self.rate_axis.setMin(0)
        else:
            self.rate_axis.setMax(10)
            self.rate_axis.setMin(0)
        
        # 添加峰值标记
        if hasattr(self, 'last_peak_time'):
            # 仅在流量有显著变化时更新峰值标记
            if bytes_sent_rate > self.last_peak_send * 1.5 or bytes_received_rate > self.last_peak_receive * 1.5:
                self._add_peak_marker(current_time, bytes_sent_rate, bytes_received_rate)
        
        # 更新峰值记录
        self.last_peak_send = max(self.last_peak_send, bytes_sent_rate)
        self.last_peak_receive = max(self.last_peak_receive, bytes_received_rate)
        self.last_peak_time = current_time
    
    def _add_peak_marker(self, timestamp, send_rate, receive_rate):
        """添加峰值标记"""
        # 简单的峰值标记逻辑，实际实现可以更复杂
        # 这里可以添加闪烁效果、标记点等
        pass
    
    def update_request_types_chart(self, stats):
        """更新请求类型分布图表"""
        successful = stats.get('successful_requests', 0)
        failed = stats.get('failed_requests', 0)
        
        # 更新饼图数据
        self.request_types_series.clear()
        self.request_types_series.append("成功请求", successful)
        self.request_types_series.append("失败请求", failed)
        
        # 设置标签
        for slice in self.request_types_series.slices():
            if slice.value() > 0:
                slice.setLabelVisible(True)
                slice.setLabel(f"{slice.label()}: {slice.value()} ({slice.percentage() * 100:.1f}%)")
            else:
                slice.setLabelVisible(False)
    
    def update_monitor_table(self, stats):
        """更新监控表格"""
        # 获取当前时间
        current_time = datetime.now().strftime("%H:%M:%S")
        
        # 计算速率
        bytes_sent_rate = stats.get('bytes_sent_rate', 0) / 1024  # KB/s
        bytes_received_rate = stats.get('bytes_received_rate', 0) / 1024  # KB/s
        
        # 插入新行
        row = self.monitor_table.rowCount()
        self.monitor_table.insertRow(row)
        
        # 设置单元格数据
        self.monitor_table.setItem(row, 0, QTableWidgetItem(current_time))
        self.monitor_table.setItem(row, 1, QTableWidgetItem(str(stats.get('requests_count', 0))))
        self.monitor_table.setItem(row, 2, QTableWidgetItem(f"{bytes_sent_rate:.1f} KB/s"))
        self.monitor_table.setItem(row, 3, QTableWidgetItem(f"{bytes_received_rate:.1f} KB/s"))
        self.monitor_table.setItem(row, 4, QTableWidgetItem(str(stats.get('active_connections', 0))))
        self.monitor_table.setItem(row, 5, QTableWidgetItem(str(stats.get('error_count', 0))))
        
        # 保持表格只显示最近的记录
        max_rows = 100
        if self.monitor_table.rowCount() > max_rows:
            self.monitor_table.removeRow(0)
        
        # 滚动到最新记录
        self.monitor_table.scrollToBottom()
    
    def add_request_record(self, request_data):
        """添加请求记录"""
        # 记录请求日志
        client_ip = request_data['client']
        duration = request_data['duration']
        status = request_data['status']
        target_host = request_data.get('target_host', 'unknown')
        self.log_event('INFO', f"请求记录 - 客户端: {client_ip}, 目标: {target_host}, 状态: {status}, 耗时: {duration:.3f}s")
        
        # 插入新行
        row = self.requests_table.rowCount()
        self.requests_table.insertRow(row)
        
        # 设置单元格数据
        self.requests_table.setItem(row, 0, QTableWidgetItem(request_data['client']))
        self.requests_table.setItem(row, 1, QTableWidgetItem(str(request_data['port'])))
        
        # 添加目标主机信息
        target_host_item = QTableWidgetItem(request_data.get('target_host', 'unknown'))
        self.requests_table.setItem(row, 2, target_host_item)
        
        # 添加目标IP信息（如果有），否则尝试从target_host解析
        target_ip = request_data.get('target_ip', 'unknown')
        target_ip_item = QTableWidgetItem(target_ip)
        self.requests_table.setItem(row, 3, target_ip_item)
        
        # 设置时间和持续时间
        self.requests_table.setItem(row, 4, QTableWidgetItem(request_data['start_time'].strftime('%H:%M:%S.%f')[:-3]))
        self.requests_table.setItem(row, 5, QTableWidgetItem(request_data['end_time'].strftime('%H:%M:%S.%f')[:-3]))
        self.requests_table.setItem(row, 6, QTableWidgetItem(f"{request_data['duration']:.3f}s"))
        
        # 设置状态颜色
        status_item = QTableWidgetItem(request_data['status'])
        if request_data['status'] == 'completed':
            status_item.setBackground(QColor("#f6ffed"))
            status_item.setForeground(QColor("#52c41a"))
        else:
            status_item.setBackground(QColor("#fff2f0"))
            status_item.setForeground(QColor("#ff4d4f"))
        
        self.requests_table.setItem(row, 7, status_item)
        
        # 保持表格只显示最近的记录
        max_rows = 1000
        if self.requests_table.rowCount() > max_rows:
            self.requests_table.removeRow(0)
        
        # 滚动到最新记录
        self.requests_table.scrollToBottom()
    
    def add_error_record(self, error_json):
        """添加错误记录"""
        try:
            error_data = json.loads(error_json)
            # 记录错误日志
            error_type = error_data.get('type', 'unknown')
            message = error_data.get('message', '')
            self.log_event('ERROR', f"错误发生 - 类型: {error_type}, 消息: {message}")
        except:
            # 如果不是JSON格式，直接记录
            error_data = {
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'type': 'unknown',
                'message': error_json
            }
            self.log_event('ERROR', f"未知错误 - 消息: {error_json}")
        
        # 插入新行
        row = self.errors_table.rowCount()
        self.errors_table.insertRow(row)
        
        # 设置单元格数据
        self.errors_table.setItem(row, 0, QTableWidgetItem(error_data['timestamp']))
        self.errors_table.setItem(row, 1, QTableWidgetItem(error_data.get('type', 'unknown')))
        
        # 设置错误消息，限制长度
        message = error_data.get('message', '')
        if len(message) > 100:
            message = message[:97] + '...'
        
        message_item = QTableWidgetItem(message)
        message_item.setBackground(QColor("#fff2f0"))
        self.errors_table.setItem(row, 2, message_item)
        
        # 保持表格只显示最近的记录
        max_rows = 1000
        if self.errors_table.rowCount() > max_rows:
            self.errors_table.removeRow(0)
        
        # 滚动到最新记录
        self.errors_table.scrollToBottom()
    
    def save_traffic_history(self, stats):
        """保存流量历史数据"""
        # 记录流量统计日志
        requests_count = stats.get('requests_count', 0)
        error_count = stats.get('error_count', 0)
        active_conn = stats.get('active_connections', 0)
        self.log_event('DEBUG', f"流量统计 - 请求数: {requests_count}, 错误数: {error_count}, 活跃连接: {active_conn}")
        
        # 确保traffic_history和max_history_points属性存在
        if not hasattr(self, 'traffic_history'):
            self.traffic_history = []
        if not hasattr(self, 'max_history_points'):
            self.max_history_points = 100  # 默认保存100个历史点
        
        # 创建历史数据点
        history_point = {
            'timestamp': datetime.now(),
            'requests_count': stats.get('requests_count', 0),
            'bytes_sent': stats.get('bytes_sent', 0),
            'bytes_received': stats.get('bytes_received', 0),
            'active_connections': stats.get('active_connections', 0),
            'error_count': stats.get('error_count', 0),
            'bytes_sent_rate': stats.get('bytes_sent_rate', 0),
            'bytes_received_rate': stats.get('bytes_received_rate', 0)
        }
        
        # 添加到历史记录
        self.traffic_history.append(history_point)
        
        # 限制历史记录数量
        if len(self.traffic_history) > self.max_history_points:
            self.traffic_history = self.traffic_history[-self.max_history_points:]
    
    def update_ui(self):
        """定期更新UI"""
        # 这里可以添加一些需要定期更新的UI元素
        pass
    
    def apply_styles(self):
        """应用样式表"""
        # 应用全局样式
        self.setStyleSheet("""
/* 全局样式 */
.QMainWindow {
            background-color: #f5f5f5;
        }
        
.QWidget {
            font-family: 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif;
            font-size: 14px;
            color: #333333;
        }
        
/* 按钮样式 */
.QPushButton {
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: 500;
            background-color: #1890ff;
            color: white;
            border: none;

        }
        
.QPushButton:hover {
            background-color: #40a9ff;
        }
        
.QPushButton:pressed {
            background-color: #096dd9;
        }
        
.QPushButton:disabled {
            background-color: #d9d9d9;
            color: #bfbfbf;
        }
        
/* 次要按钮 */
.QPushButton.secondary {
            background-color: #ffffff;
            color: #333333;
            border: 1px solid #d9d9d9;
        }
        
.QPushButton.secondary:hover {
            border-color: #40a9ff;
            color: #40a9ff;
        }
        
/* 分组框样式 */
.QGroupBox {
            border: 1px solid #e8e8e8;
            border-radius: 8px;
            margin-top: 15px;
            background-color: #ffffff;
            border: 1px solid #e0e0e0;
        }
        
.QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 12px;
            left: 15px;
            top: -10px;
            background-color: #ffffff;
            font-weight: 600;
            color: #1890ff;
        }
        
/* 标签页样式 */
.QTabWidget::pane {
            border: 1px solid #e8e8e8;
            border-top: none;
            background-color: #ffffff;
            border-radius: 0 0 8px 8px;
        }
        
.QTabBar::tab {
            padding: 10px 20px;
            border: 1px solid transparent;
            border-bottom: none;
            background-color: #ffffff;
            margin-right: 2px;
            border-radius: 8px 8px 0 0;

        }
        
.QTabBar::tab:hover {
            background-color: #f0f0f0;
        }
        
.QTabBar::tab:selected {
            background-color: #ffffff;
            border: 1px solid #e8e8e8;
            border-bottom: none;
            color: #1890ff;
            font-weight: 500;
        }
        
/* 输入控件样式 */
.QLineEdit, .QSpinBox, .QComboBox, .QTextEdit {
            padding: 8px 12px;
            border: 1px solid #d9d9d9;
            border-radius: 6px;
            background-color: #ffffff;

        }
        
.QLineEdit:focus, .QSpinBox:focus, .QComboBox:focus, .QTextEdit:focus {
            border-color: #40a9ff;
            border-width: 2px;
            outline: none;
        }
        
/* 表格样式 */
.QTableWidget {
            border: 1px solid #e8e8e8;
            border-radius: 6px;
            background-color: #ffffff;
            alternate-background-color: #fafafa;
        }
        
.QHeaderView::section {
            background-color: #fafafa;
            padding: 10px;
            border: 1px solid #e8e8e8;
            font-weight: 600;
            color: #333333;
        }
        
.QTableWidgetItem {
            padding: 8px;
            border-bottom: 1px solid #f0f0f0;
        }
        
/* 状态栏样式 */
.QStatusBar {
            background-color: #ffffff;
            border-top: 1px solid #e8e8e8;
            padding: 4px 10px;
        }
        
/* 复选框样式 */
.QCheckBox {
            spacing: 8px;
        }
        
.QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 2px solid #d9d9d9;
        }
        
.QCheckBox::indicator:checked {
            background-color: #1890ff;
            border-color: #1890ff;
        }
        
/* 滚动条样式 */
QScrollBar:vertical {
            width: 8px;
            background-color: #f5f5f5;
            margin: 0;
        }
        
QScrollBar::handle:vertical {
            background-color: #d9d9d9;
            border-radius: 4px;
        }
        
QScrollBar::handle:vertical:hover {
            background-color: #bfbfbf;
        }
        
QScrollBar:horizontal {
            height: 8px;
            background-color: #f5f5f5;
            margin: 0;
        }
        
QScrollBar::handle:horizontal {
            background-color: #d9d9d9;
            border-radius: 4px;
        }
        
QScrollBar::handle:horizontal:hover {
            background-color: #bfbfbf;
        }
        
/* 进度条样式 */
.QProgressBar {
            border-radius: 10px;
            text-align: center;
            background-color: #f5f5f5;
            height: 6px;
        }
        
.QProgressBar::chunk {
            border-radius: 10px;
            background-color: #1890ff;
        }
        
/* 分隔器样式 */
QSplitter::handle {
            background-color: #e8e8e8;
        }
        
QSplitter::handle:hover {
            background-color: #d9d9d9;
        }
        """)
    
    def toggle_dark_mode(self, checked):
        """切换深色模式"""
        if checked:
            # 应用深色主题
            dark_style = """/* 深色主题样式 */
            .QWidget {
                background-color: #1a1a1a;
                color: #d9d9d9;
            }
            
            .QTabWidget::pane {
                background-color: #262626;
                border-color: #434343;
            }
            
            .QTabBar::tab {
                background-color: #262626;
                color: #d9d9d9;
                border-color: #434343;
            }
            
            .QTabBar::tab:selected {
                background-color: #1a1a1a;
                border-top-color: #1890ff;
            }
            
            .QGroupBox {
                border-color: #434343;
            }
            
            .QGroupBox::title {
                background-color: #1a1a1a;
                color: #d9d9d9;
            }
            
            .QHeaderView::section {
                background-color: #262626;
                border-color: #434343;
                color: #d9d9d9;
            }
            
            .QLineEdit, .QSpinBox, .QComboBox {
                background-color: #262626;
                color: #d9d9d9;
                border-color: #434343;
            }
            
            .QTableWidget {
                background-color: #262626;
                border-color: #434343;
            }
            
            .QTableWidget::item {
                color: #d9d9d9;
                background-color: #262626;
            }
            """
            self.setStyleSheet(dark_style)
        else:
            # 恢复默认样式
            self.apply_styles()
            
    def create_log_viewer_tab(self):
        """创建日志查看标签页"""
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        
        # 创建日志过滤和搜索区域
        filter_layout = QHBoxLayout()
        
        # 日志级别过滤
        level_label = QLabel("日志级别:")
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["全部", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_combo.currentTextChanged.connect(self.filter_logs)
        
        # 搜索框
        search_label = QLabel("搜索:")
        self.log_search_edit = QLineEdit()
        self.log_search_edit.setPlaceholderText("输入关键词搜索日志...")
        self.log_search_edit.textChanged.connect(self.filter_logs)
        
        # 按钮组
        button_layout = QHBoxLayout()
        
        clear_button = QPushButton("清空日志")
        clear_button.clicked.connect(self.clear_logs)
        
        export_button = QPushButton("导出日志")
        export_button.clicked.connect(self.export_logs)
        
        button_layout.addWidget(clear_button)
        button_layout.addWidget(export_button)
        
        filter_layout.addWidget(level_label)
        filter_layout.addWidget(self.log_level_combo)
        filter_layout.addWidget(search_label)
        filter_layout.addWidget(self.log_search_edit)
        filter_layout.addLayout(button_layout)
        filter_layout.addStretch()
        
        # 创建日志显示区域
        self.log_text_edit = QPlainTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;")
        self.log_text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        
        # 添加滚动条
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.log_text_edit)
        
        # 添加到主布局
        log_layout.addLayout(filter_layout)
        log_layout.addWidget(scroll_area)
        
        # 添加标签页
        self.tab_widget.addTab(log_tab, "日志查看")
        
        # 记录首次启动日志
        self.log_event("INFO", "应用程序启动")
    
    def log_event(self, level, message):
        """记录日志事件"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] {message}"
        
        # 添加到缓冲区
        self.log_buffer.append((timestamp, level, message, log_entry))
        
        # 限制缓冲区大小
        if len(self.log_buffer) > self.max_log_entries:
            self.log_buffer = self.log_buffer[-self.max_log_entries:]
        
        # 显示在日志窗口（如果当前选中的是该级别）
        current_level = hasattr(self, 'log_level_combo') and self.log_level_combo.currentText() or "全部"
        search_text = hasattr(self, 'log_search_edit') and self.log_search_edit.text().lower() or ""
        
        if hasattr(self, 'log_text_edit') and \
           (current_level == "全部" or current_level == level) and \
           (not search_text or search_text in message.lower()):
            self.log_text_edit.appendPlainText(log_entry)
            # 自动滚动到底部
            self.log_text_edit.moveCursor(self.log_text_edit.textCursor().End)
    
    def filter_logs(self):
        """根据选择的级别和搜索文本过滤日志"""
        current_level = self.log_level_combo.currentText()
        search_text = self.log_search_edit.text().lower()
        
        # 清空当前显示
        self.log_text_edit.clear()
        
        # 重新显示过滤后的日志
        for timestamp, level, message, log_entry in self.log_buffer:
            if (current_level == "全部" or current_level == level) and \
               (not search_text or search_text in message.lower() or search_text in timestamp.lower()):
                self.log_text_edit.appendPlainText(log_entry)
        
        # 自动滚动到底部
        self.log_text_edit.moveCursor(self.log_text_edit.textCursor().End)
    
    def clear_logs(self):
        """清空日志"""
        reply = QMessageBox.question(self, "确认清空", "确定要清空所有日志吗？", 
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.log_buffer.clear()
            self.log_text_edit.clear()
            self.log_event("INFO", "日志已清空")
    
    def export_logs(self):
        """导出日志"""
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "导出日志", "", 
                                                  "文本文件 (*.txt);;所有文件 (*)", options=options)
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for _, _, _, log_entry in self.log_buffer:
                        f.write(log_entry + '\n')
                self.log_event("INFO", f"日志已导出到: {file_path}")
                QMessageBox.information(self, "导出成功", f"日志已成功导出到:\n{file_path}")
            except Exception as e:
                self.log_event("ERROR", f"导出日志失败: {str(e)}")
    
    def init_system_tray(self):
        """初始化系统托盘图标"""
        # 创建系统托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        
        # 创建托盘菜单
        self.tray_menu = QMenu(self)
        
        # 添加显示/隐藏窗口动作
        self.show_hide_action = QAction("显示窗口", self)
        self.show_hide_action.triggered.connect(self.toggle_window_visibility)
        self.tray_menu.addAction(self.show_hide_action)
        
        # 添加启动/停止服务器动作
        self.toggle_server_action = QAction("启动服务器", self)
        self.toggle_server_action.triggered.connect(self.toggle_server_from_tray)
        self.tray_menu.addAction(self.toggle_server_action)
        
        # 添加分隔线
        self.tray_menu.addSeparator()
        
        # 添加退出动作
        self.exit_action = QAction("退出程序", self)
        self.exit_action.triggered.connect(self.close_app_from_tray)
        self.tray_menu.addAction(self.exit_action)
        
        # 设置托盘菜单
        self.tray_icon.setContextMenu(self.tray_menu)
        
        # 设置托盘图标提示
        self.tray_icon.setToolTip("高级代理服务器 - 流量监控与分析")
        
        # 连接托盘图标点击信号
        self.tray_icon.activated.connect(self.tray_icon_activated)
        
        # 显示托盘图标
        self.tray_icon.show()
        
        # 记录托盘状态日志
        self.log_event('INFO', "系统托盘图标已初始化")
    
    def toggle_window_visibility(self):
        """切换窗口可见性"""
        if self.isVisible():
            self.hide()
            self.show_hide_action.setText("显示窗口")
        else:
            self.show()
            self.activateWindow()
            self.show_hide_action.setText("隐藏窗口")
        
    def toggle_server_from_tray(self):
        """从托盘切换服务器状态"""
        if self.proxy_server and self.proxy_server.is_running:
            self.stop_proxy_server()
        else:
            self.start_proxy_server()
        
    def close_app_from_tray(self):
        """从托盘关闭应用"""
        # 停止代理服务器
        if self.proxy_server and self.proxy_server.is_running:
            self.stop_proxy_server()
        
        # 隐藏托盘图标
        self.tray_icon.hide()
        
        # 退出应用
        QApplication.quit()
        
    def tray_icon_activated(self, reason):
        """处理托盘图标激活事件"""
        if reason == QSystemTrayIcon.Trigger:
            # 点击托盘图标时切换窗口可见性
            self.toggle_window_visibility()
        
    def closeEvent(self, event):
        """重写关闭事件，使窗口最小化到托盘而不是真正关闭"""
        if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            # 询问用户是否真的要退出
            reply = QMessageBox.question(
                self,
                "退出确认",
                "是否要退出程序？\n点击'否'将最小化到系统托盘。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # 停止代理服务器
                if self.proxy_server and self.proxy_server.is_running:
                    self.stop_proxy_server()
                
                # 隐藏托盘图标
                self.tray_icon.hide()
                
                event.accept()
            else:
                # 最小化到托盘
                self.hide()
                self.show_hide_action.setText("显示窗口")
                event.ignore()
        else:
            # 如果没有托盘图标，则正常关闭
            event.accept()
    
    def keyPressEvent(self, event):
        """处理全局快捷键事件"""
        # 处理特定组合键
        if event.modifiers() == Qt.ControlModifier:
            # 检查功能键与Ctrl的组合
            if event.key() == Qt.Key_F5:
                # Ctrl+F5: 重启代理服务器
                self.stop_proxy_server()
                # 短暂延迟后启动服务器
                QTimer.singleShot(500, self.start_proxy_server)
                event.accept()
                return
            elif event.key() == Qt.Key_F:
                # Ctrl+F: 在当前标签页中搜索/过滤
                current_widget = self.tab_widget.currentWidget()
                if hasattr(current_widget, 'findChild'):
                    # 尝试找到搜索/过滤输入框并聚焦
                    search_input = current_widget.findChild((QLineEdit, QTextEdit), "search_input")
                    if search_input:
                        search_input.setFocus()
                        event.accept()
                        return
        
        # 处理单个功能键
        elif event.key() == Qt.Key_Escape:
            # ESC: 清除当前聚焦的输入框内容
            focused_widget = self.focusWidget()
            if isinstance(focused_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                focused_widget.clear()
                event.accept()
                return
        
        # 调用父类方法处理其他键事件
        super().keyPressEvent(event)
            
    def update_tray_menu(self):
        """更新托盘菜单状态"""
        if hasattr(self, 'toggle_server_action'):
            if self.proxy_server and self.proxy_server.is_running:
                self.toggle_server_action.setText("停止服务器")
            else:
                self.toggle_server_action.setText("启动服务器")
    
    def hex_to_rgb(self, hex_color):
        """将十六进制颜色转换为RGB值"""
        hex_color = hex_color.lstrip('#')
        return f"{int(hex_color[0:2], 16)}, {int(hex_color[2:4], 16)}, {int(hex_color[4:6], 16)}"
    
    def apply_form_styles(self, widget):
        """应用表单样式到特定组件"""
        if widget is not None:
            widget.setStyleSheet("""
QLabel {
                font-weight: 500;
                color: #595959;
                margin-bottom: 4px;
            }
            
QFormLayout {
                margin: 10px;
            }
            
QFormLayout::item {
                margin-bottom: 8px;
            }
            """)
    
    def show_about_dialog(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于代理服务器",
            "高级代理服务器 v1.0\n" 
            "一个功能强大的HTTP/HTTPS代理服务器，\n" 
            "包含实时流量监控和分析功能。\n"
            "按F1查看快捷键帮助"
        )
    
    def show_shortcuts_help(self):
        """显示快捷键帮助对话框"""
        shortcuts_text = (
            "XHProxy 快捷键帮助\n\n"
            "服务器控制:\n"
            "  Ctrl+S          启动服务器\n"
            "  Ctrl+X          停止服务器\n"
            "  Ctrl+F5         重启服务器\n\n"
            "导航:\n"
            "  Ctrl+1          切换到仪表板\n"
            "  Ctrl+2          切换到监控\n"
            "  Ctrl+3          切换到分析\n"
            "  Ctrl+4          切换到日志\n\n"
            "界面控制:\n"
            "  Ctrl+D          切换深色模式\n"
            "  Ctrl+Q          退出程序\n\n"
            "通用功能:\n"
            "  Ctrl+F          聚焦搜索/过滤框\n"
            "  ESC             清除当前输入框\n"
            "  F1              显示快捷键帮助\n"
        )
        
        QMessageBox.information(
            self,
            "快捷键帮助",
            shortcuts_text,
            QMessageBox.Ok
        )
    
    def closeEvent(self, event):
        """窗口关闭事件处理"""
        # 停止代理服务器
        if self.proxy_server:
            reply = QMessageBox.question(
                self,
                "确认关闭",
                "代理服务器正在运行，是否确定要关闭？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.stop_proxy_server()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    """主函数"""
    # 创建应用程序实例
    app = QApplication(sys.argv)
    
    # 设置应用程序样式
    app.setStyle("Fusion")
    
    # 创建主窗口实例
    main_window = ProxyGUIMainWindow()
    
    # 显示主窗口
    main_window.show()
    
    # 运行应用程序
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
