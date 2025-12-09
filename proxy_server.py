"""
简易HTTP代理服务器
支持HTTP和HTTPS请求的转发功能，包含详细的错误处理和日志记录
"""

import socket
import sys
import threading
import time
import logging
import re
import argparse
import traceback
import queue
import select
import gc
from datetime import datetime
from collections import deque

# 配置日志
logger = logging.getLogger('proxy_server')
def setup_logging(debug=False):
    # 确保日志级别正确设置
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("proxy_server.log"),
            logging.StreamHandler()
        ]
    )
    """设置日志配置
    
    Args:
        debug: 是否启用调试日志
    """
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    
    # 创建格式器
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # 清除已有的处理器
    if logger.handlers:
        logger.handlers.clear()
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.addHandler(console_handler)
    
    # 文件处理器 - 按日期命名
    log_filename = f"proxy_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_filename)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # 文件总是记录所有日志
    logger.addHandler(file_handler)

# 保持原始logging模块可用，不替换为logger变量

class ConnectionPool:
    """连接池管理类，用于复用连接"""
    def __init__(self, max_connections=100, connection_timeout=10, timeout=None):
        self.max_connections = max_connections
        # 支持两种参数名，timeout作为connection_timeout的别名
        self.connection_timeout = connection_timeout if timeout is None else timeout
        self.pool = {}
        self.lock = threading.RLock()
    
    def get_connection(self, host, port):
        """从连接池获取连接，如果没有则创建新连接"""
        key = (host, port)
        
        with self.lock:
            # 检查连接池是否有可用连接
            if key in self.pool:
                connections = self.pool[key]
                while connections:
                    try:
                        conn, timestamp = connections.pop()
                        # 检查连接是否过期（超过30秒）
                        if time.time() - timestamp < 30:
                            # 测试连接是否仍然有效
                            conn.settimeout(2)
                            try:
                                # 非阻塞检查连接
                                conn.setblocking(False)
                                conn.recv(1, socket.MSG_PEEK)
                                conn.setblocking(True)
                                logging.debug(f"复用连接到 {host}:{port}")
                                return conn
                            except (socket.error, socket.timeout):
                                # 连接已失效
                                conn.close()
                                continue
                    except:
                        continue
            
            # 创建新连接
            try:
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.settimeout(self.connection_timeout)
                conn.connect((host, port))
                logging.debug(f"创建新连接到 {host}:{port}")
                return conn
            except Exception as e:
                logging.error(f"创建连接到 {host}:{port} 失败: {e}")
                return None
    
    def release_connection(self, conn, host, port):
        """释放连接回连接池"""
        if not conn:
            return
            
        key = (host, port)
        try:
            with self.lock:
                if key not in self.pool:
                    self.pool[key] = []
                    
                # 限制每个主机的连接数
                if len(self.pool[key]) < 10:
                    self.pool[key].append((conn, time.time()))
                    logging.debug(f"连接已释放到 {host}:{port}")
                else:
                    conn.close()
        except Exception as e:
            logging.error(f"释放连接到 {host}:{port} 失败: {e}")
            try:
                conn.close()
            except:
                pass
    
    def close_all(self):
        """关闭所有连接"""
        with self.lock:
            for key, connections in self.pool.items():
                host, port = key
                for conn, _ in connections:
                    try:
                        conn.close()
                    except:
                        pass
                self.pool[key] = []

class ThreadPool:
    """线程池管理类"""
    def __init__(self, max_threads=50):
        self.max_threads = max_threads
        self.task_queue = queue.Queue()
        self.threads = []
        self.running = False
        self.lock = threading.RLock()
    
    def start(self):
        """启动线程池"""
        self.running = True
        for _ in range(self.max_threads):
            thread = threading.Thread(target=self._worker)
            thread.daemon = True
            thread.start()
            self.threads.append(thread)
    
    def stop(self):
        """停止线程池"""
        self.running = False
        # 等待所有线程完成
        for thread in self.threads:
            thread.join(timeout=2)
    
    def _worker(self):
        """工作线程函数"""
        while self.running:
            try:
                # 从队列获取任务，设置超时以定期检查running标志
                task, args, kwargs = self.task_queue.get(timeout=0.5)
                try:
                    task(*args, **kwargs)
                except Exception as e:
                    logging.error(f"执行任务时出错: {e}")
                finally:
                    self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                break
    
    def submit(self, task, *args, **kwargs):
        """提交任务到线程池"""
        if self.running:
            self.task_queue.put((task, args, kwargs))
            return True
        return False
    
    def queue_size(self):
        """返回队列中等待的任务数"""
        return self.task_queue.qsize()

class ProxyServer:
    def __init__(self, host='0.0.0.0', port=8080, buffer_size=4096, modify_headers=True, max_retries=3, connection_timeout=10, max_threads=50, max_connections=100, enable_keepalive=True):
        """初始化代理服务器
        
        Args:
            host: 监听主机
            port: 监听端口
            buffer_size: 缓冲区大小
            modify_headers: 是否修改响应头
            max_retries: 连接重试次数
            connection_timeout: 连接超时时间（秒）
            max_threads: 最大线程数
            max_connections: 最大连接池大小
            enable_keepalive: 是否启用TCP keepalive
        """
        # 基本配置
        self.host = host
        self.port = port
        self.buffer_size = buffer_size
        self.modify_headers = modify_headers
        self.max_retries = max_retries
        self.connection_timeout = connection_timeout
        self.max_threads = max_threads
        self.max_connections = max_connections
        self.enable_keepalive = enable_keepalive
        
        # 服务器状态
        self.server_socket = None
        self.threads = []
        self.running = False
        
        # 性能优化组件
        self.connection_pool = ConnectionPool(max_connections=max_connections, connection_timeout=300)
        self.thread_pool = ThreadPool(max_threads=max_threads)
        
        # 统计信息
        self.stats_lock = threading.RLock()
        self.request_history_lock = threading.RLock()  # 添加请求历史锁
        self.error_history_lock = threading.RLock()  # 添加错误历史锁
        self.connection_pool_lock = threading.RLock()  # 添加连接池锁
        
        self.request_history = []  # 添加请求历史列表
        self.error_history = []  # 添加错误历史列表
        self.error_types = {}  # 添加错误类型统计
        
        # 带宽计算所需的变量
        self.last_bandwidth_update = datetime.now()
        self.last_bytes_received = 0
        self.last_bytes_sent = 0
        
        # 初始化统计信息
        self.stats = {
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
            'start_time': datetime.now(),
            'current_bandwidth_down': 0,
            'current_bandwidth_up': 0,
            'bandwidth_history_down': [],
            'bandwidth_history_up': []
        }
        
        # 确保关键方法存在，添加默认实现作为后备方案
        if not hasattr(self, 'forward_request'):
            logging.warning("使用默认的forward_request实现")
            self.forward_request = self._default_forward_request
        
        if not hasattr(self, 'tunnel_data'):
            logging.warning("使用默认的tunnel_data实现")
            self.tunnel_data = self._default_tunnel_data
    
    def _default_forward_request(self, client_socket, request_data, host, port, is_websocket=False):
        """默认的HTTP请求转发实现"""
        server_socket = None
        try:
            # 验证host和port的有效性
            if not host or host.strip() == '':
                raise ValueError("无效的主机名")
            
            # 确保port是有效的整数
            try:
                port = int(port)
                if not (1 <= port <= 65535):
                    raise ValueError(f"无效的端口号: {port}")
            except (ValueError, TypeError):
                raise ValueError(f"端口号必须是1-65535之间的整数: {port}")
            
            # 记录连接信息用于调试
            logging.debug(f"尝试连接到 {host}:{port}")
            
            # 创建到目标服务器的连接
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.settimeout(self.connection_timeout)
            try:
                server_socket.connect((host, port))
            except socket.gaierror as e:
                # 专门处理DNS解析错误
                error_msg = f"DNS解析失败 (连接 {host}:{port}): {e}"
                logging.error(error_msg)
                self._record_error('dns_resolution_failed', f"{host}:{port} - {e}")
                # 发送502错误响应给客户端
                self._send_error_response(client_socket, 502, "Bad Gateway - DNS Resolution Failed")
                raise
            except socket.error as e:
                # 处理其他连接错误
                error_msg = f"连接到 {host}:{port} 失败: {e}"
                logging.error(error_msg)
                self._record_error('connection_failed', f"{host}:{port} - {e}")
                # 发送502错误响应给客户端
                self._send_error_response(client_socket, 502, "Bad Gateway - Connection Failed")
                raise
            
            # 发送请求
            server_socket.sendall(request_data)
            
            # 接收并转发响应
            while True:
                response_data = server_socket.recv(self.buffer_size)
                if not response_data:
                    break
                client_socket.sendall(response_data)
                
                # 更新统计信息
                with self.stats_lock:
                    self.stats['bytes_received'] += len(response_data)
                    self.stats['bytes_sent'] += len(response_data)
                    self.stats['successful_requests'] += 1
                    
        except ValueError as e:
            # 参数验证错误
            logging.error(f"请求参数错误: {e}")
            self._record_error('invalid_parameters', str(e))
            # 发送400错误响应给客户端
            self._send_error_response(client_socket, 400, f"Bad Request - {str(e)}")
            with self.stats_lock:
                self.stats['error_count'] += 1
                self.stats['failed_requests'] += 1
        except Exception as e:
            # 其他未预期的错误
            logging.error(f"默认HTTP转发失败: {e}")
            with self.stats_lock:
                self.stats['error_count'] += 1
                self.stats['failed_requests'] += 1
        finally:
            # 确保所有套接字都被正确关闭
            try:
                if server_socket is not None:
                    server_socket.close()
            except Exception as close_error:
                logging.debug(f"关闭服务器套接字时出错: {close_error}")
            
            try:
                if client_socket is not None:
                    client_socket.close()
            except Exception as close_error:
                logging.debug(f"关闭客户端套接字时出错: {close_error}")
    
    def _default_tunnel_data(self, client_socket, server_socket, request_info):
        """默认的HTTPS隧道数据转发实现"""
        try:
            # 使用双向数据转发
            client_to_server = threading.Thread(target=self._forward_data, args=(client_socket, server_socket, '客户端', '服务器'))
            server_to_client = threading.Thread(target=self._forward_data, args=(server_socket, client_socket, '服务器', '客户端'))
            
            client_to_server.daemon = True
            server_to_client.daemon = True
            
            client_to_server.start()
            server_to_client.start()
            
            # 等待任一方向的数据传输结束
            client_to_server.join()
            server_to_client.join()
            
            with self.stats_lock:
                self.stats['successful_requests'] += 1
                
        except Exception as e:
            logging.error(f"默认HTTPS隧道转发失败: {e}")
            with self.stats_lock:
                self.stats['error_count'] += 1
                self.stats['failed_requests'] += 1
        finally:
            try:
                client_socket.close()
            except:
                pass
            try:
                server_socket.close()
            except:
                pass
    
    def _forward_data(self, source_socket, destination_socket, source_name, destination_name):
        """通用的数据转发方法"""
        try:
            while self.running:
                data = source_socket.recv(self.buffer_size)
                if not data:
                    break
                destination_socket.sendall(data)
                
                # 更新统计信息
                bytes_received = len(data)
                bytes_sent = len(data)
                
                with self.stats_lock:
                    self.stats['bytes_received'] += bytes_received
                    self.stats['bytes_sent'] += bytes_sent
                
                # 更新带宽统计信息
                self._update_bandwidth_stats(bytes_sent, bytes_received)
                    
        except socket.timeout:
            # 超时是正常的，不记录为错误
            pass
        except Exception as e:
            # 忽略连接重置等错误，因为这可能是正常关闭
            if isinstance(e, BrokenPipeError) or '远程主机强迫关闭了一个现有的连接' in str(e):
                pass
            else:
                 logging.debug(f"{source_name}到{destination_name}数据转发错误: {e}")
    
    def _update_bandwidth_stats(self, bytes_sent, bytes_received):
        """更新带宽统计信息
        
        Args:
            bytes_sent: 发送的字节数
            bytes_received: 接收的字节数
        """
        current_time = datetime.now()
        
        # 初始化带宽相关统计字段（如果不存在）
        with self.stats_lock:
            if 'current_bandwidth_down' not in self.stats:
                self.stats['current_bandwidth_down'] = 0
                self.stats['current_bandwidth_up'] = 0
                self.stats['bandwidth_history_down'] = []
                self.stats['bandwidth_history_up'] = []
                self.last_bandwidth_update = current_time
                self.last_bytes_received = 0
                self.last_bytes_sent = 0
            
            time_diff = (current_time - self.last_bandwidth_update).total_seconds()
            
            # 至少每秒更新一次带宽
            if time_diff >= 1.0:
                # 计算下载和上传带宽
                actual_bytes_down = self.stats['bytes_received'] - self.last_bytes_received
                actual_bytes_up = self.stats['bytes_sent'] - self.last_bytes_sent
                
                # 更新带宽值
                self.stats['current_bandwidth_down'] = actual_bytes_down / time_diff
                self.stats['current_bandwidth_up'] = actual_bytes_up / time_diff
                
                # 记录历史（限制历史记录长度）
                self.stats['bandwidth_history_down'].append({
                    'timestamp': current_time,
                    'value': self.stats['current_bandwidth_down']
                })
                self.stats['bandwidth_history_up'].append({
                    'timestamp': current_time,
                    'value': self.stats['current_bandwidth_up']
                })
                
                # 限制历史记录长度
                if len(self.stats['bandwidth_history_down']) > 300:  # 保留5分钟的数据（每秒一个点）
                    self.stats['bandwidth_history_down'] = self.stats['bandwidth_history_down'][-300:]
                if len(self.stats['bandwidth_history_up']) > 300:
                    self.stats['bandwidth_history_up'] = self.stats['bandwidth_history_up'][-300:]
                
                # 更新基准值
                self.last_bytes_received = self.stats['bytes_received']
                self.last_bytes_sent = self.stats['bytes_sent']
                self.last_bandwidth_update = current_time
                
                # 更新最后统计时间
                self.stats['last_stats_update'] = current_time
    
    def get_stats(self):
        """获取当前统计信息的副本，用于GUI显示
        
        Returns:
            dict: 当前统计信息的副本
        """
        with self.stats_lock:
            # 创建一个深拷贝以避免线程安全问题
            stats_copy = dict(self.stats)
            # 确保带宽历史字段存在
            if 'bandwidth_history_down' not in stats_copy:
                stats_copy['bandwidth_history_down'] = []
                stats_copy['bandwidth_history_up'] = []
            # 复制不可变的历史数据
            stats_copy['bandwidth_history_down'] = list(stats_copy['bandwidth_history_down'])
            stats_copy['bandwidth_history_up'] = list(stats_copy['bandwidth_history_up'])
        return stats_copy
    
    def get_request_history(self):
        """获取请求历史记录，用于GUI显示
        
        Returns:
            list: 请求历史记录的副本，确保包含target_host和target_ip字段
        """
        if not hasattr(self, 'request_history_lock') or not hasattr(self, 'request_history'):
            return []
        
        with self.request_history_lock:
            # 确保每条记录都包含target_host和target_ip字段
            enhanced_history = []
            for record in self.request_history:
                # 创建副本以避免修改原始记录
                record_copy = dict(record)
                # 确保有target_host和target_ip键
                if 'target_host' not in record_copy:
                    record_copy['target_host'] = 'unknown'
                if 'target_ip' not in record_copy:
                    record_copy['target_ip'] = 'unknown'
                enhanced_history.append(record_copy)
            return enhanced_history
    
    def get_error_history(self):
        """获取错误历史记录，用于GUI显示
        
        Returns:
            list: 错误历史记录的副本
        """
        if not hasattr(self, 'error_history_lock') or not hasattr(self, 'error_history'):
            return []
        
        with self.error_history_lock:
            return list(self.error_history)
    
    def get_error_types(self):
        """获取错误类型统计，用于GUI显示
        
        Returns:
            dict: 错误类型统计的副本
        """
        if not hasattr(self, 'error_history_lock') or not hasattr(self, 'error_types'):
            return {}
        
        with self.error_history_lock:
            return dict(self.error_types)
    
    def _is_socket_in_pool(self, socket_obj):
        """检查套接字是否在连接池中
        
        Args:
            socket_obj: 要检查的套接字对象
            
        Returns:
            bool: 套接字是否在连接池中
        """
        if not hasattr(self, 'connection_pool_lock') or not hasattr(self, 'connection_pool'):
            return False
            
        with self.connection_pool_lock:
            for conn_list in self.connection_pool.values():
                if socket_obj in conn_list:
                    return True
            return False
    
    def _check_host_access(self, host):
        """检查主机是否允许访问（基于黑白名单）
        
        Args:
            host: 要检查的主机名
            
        Returns:
            bool: 是否允许访问
        """
        # 如果白名单不为空，只有在白名单中的主机才能访问
        if self.host_whitelist and host not in self.host_whitelist:
            return False
            
        # 如果主机在黑名单中，则拒绝访问
        if host in self.host_blacklist:
            return False
            
        return True
    
    def report_stats(self):
        """定期报告代理服务器统计信息"""
        while self.running:
            time.sleep(60)  # 每分钟报告一次
            with self.stats_lock:
                logging.info(f"=== 代理服务器统计信息 ===")
                logging.info(f"总请求数: {self.stats['requests_count']}")
                logging.info(f"发送字节数: {self.stats['bytes_sent']}")
                logging.info(f"接收字节数: {self.stats['bytes_received']}")
                logging.info(f"活跃连接数: {self.stats['active_connections']}")
                logging.info(f"========================")
    
    def _modify_response_headers(self, response_data, host):
        """修改响应头信息
        
        Args:
            response_data: 原始响应数据
            host: 目标主机
            
        Returns:
            修改后的响应数据
        """
        try:
            # 尝试解码响应数据以修改头信息
            # 确保只对bytes类型调用decode
            if isinstance(response_data, bytes):
                response_text = response_data.decode('utf-8', errors='ignore')
            else:
                response_text = str(response_data)
            
            # 检查是否包含响应头
            if '\r\n\r\n' in response_text:
                headers_part, body_part = response_text.split('\r\n\r\n', 1)
                
                # 添加代理标识头
                if not re.search(r'^X-Proxy-By:', headers_part, re.MULTILINE):
                    headers_part += '\r\nX-Proxy-By: SimplePythonProxy'
                
                # 重新组合响应
                return (headers_part + '\r\n\r\n' + body_part).encode('utf-8', errors='ignore')
            
            # 如果没有标准的响应头结构，直接返回原始数据
            return response_data
        except Exception as e:
            logging.debug(f"修改响应头时出错: {e}")
            return response_data
            
    def report_stats(self):
        """定期报告代理服务器统计信息"""
        while self.running:
            time.sleep(60)  # 每分钟报告一次
            with self.stats_lock:
                logging.info(f"=== 代理服务器统计信息 ===")
                logging.info(f"总请求数: {self.stats['requests_count']}")
                logging.info(f"发送字节数: {self.stats['bytes_sent']}")
                logging.info(f"接收字节数: {self.stats['bytes_received']}")
                logging.info(f"活跃连接数: {self.stats['active_connections']}")
                logging.info(f"========================")
    
    def start(self):
        """启动代理服务器"""
        # 初始化服务器套接字为None
        self.server_socket = None
        
        try:
            # 创建并配置服务器套接字
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # 启用TCP keepalive
            if self.enable_keepalive:
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                if hasattr(socket, 'TCP_KEEPCNT'):
                    self.server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
            
            self.server_socket.settimeout(1.0)  # 设置超时以便可以检查running标志
            
            # 绑定端口并开始监听
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(100)
            
            self.running = True
            
            # 启动线程池
            self.thread_pool.start()
            
            # 启动统计报告线程
            self.stats_thread = threading.Thread(target=self.report_stats, daemon=True)
            self.stats_thread.start()
            self.threads.append(self.stats_thread)
            
            logging.info(f"高性能代理服务器启动在 http://{self.host}:{self.port}")
            logging.info(f"请将浏览器或应用的代理设置为 {self.host}:{self.port}")
            logging.info(f"响应头修改: {'开启' if self.modify_headers else '关闭'}")
            logging.info(f"连接超时: {self.connection_timeout}秒, 最大重试: {self.max_retries}次")
            logging.info(f"线程池大小: {self.thread_pool.max_threads}, 连接池大小: {self.connection_pool.max_connections}")
            
            # 使用select实现非阻塞IO
            inputs = [self.server_socket]
            
            # 主循环，接受客户端连接 - 使用无限循环确保不会退出
            while True:
                # 强制保持running标志为True
                self.running = True
                
                try:
                    # 检查服务器套接字是否有效
                    valid_inputs = []
                    try:
                        # 确保inputs列表中的套接字都是有效的
                        for sock in inputs:
                            if sock and hasattr(sock, 'fileno') and sock.fileno() != -1:
                                valid_inputs.append(sock)
                    except:
                        # 如果检查失败，重置为只包含服务器套接字
                        try:
                            if self.server_socket and hasattr(self.server_socket, 'fileno') and self.server_socket.fileno() != -1:
                                valid_inputs = [self.server_socket]
                        except:
                            # 如果服务器套接字也无效，退出循环
                            logging.error("服务器套接字无效，尝试重新创建...")
                            break
                    
                    # 如果没有有效的输入，跳过此次循环
                    if not valid_inputs:
                        time.sleep(0.1)
                        continue
                    
                    # 使用select监听有效的可读事件
                    try:
                        readable, writable, exceptional = select.select(valid_inputs, [], valid_inputs, 0.5)
                    except select.error as e:
                        logging.error(f"Select操作错误: {e}")
                        time.sleep(0.1)
                        continue
                    
                    for sock in readable:
                        if sock is self.server_socket:
                            try:
                                client_socket, client_address = self.server_socket.accept()
                                client_socket.settimeout(self.connection_timeout)
                                
                                # 更新连接统计
                                try:
                                    with self.stats_lock:
                                        self.stats['active_connections'] += 1
                                except Exception as lock_e:
                                    logging.error(f"更新统计时出错: {lock_e}")
                                
                                logging.info(f"接受到来自 {client_address} 的连接")
                                
                                # 使用线程池处理客户端请求
                                try:
                                    if not self.thread_pool.submit(self.handle_client, client_socket, client_address):
                                        # 如果线程池已停止，关闭客户端连接
                                        try:
                                            client_socket.close()
                                            try:
                                                with self.stats_lock:
                                                    self.stats['active_connections'] -= 1
                                            except:
                                                pass
                                        except:
                                            pass
                                except Exception as pool_e:
                                    logging.error(f"提交任务到线程池时出错: {pool_e}")
                                    try:
                                        client_socket.close()
                                    except:
                                        pass
                            except Exception as e:
                                logging.error(f"接受客户端连接时出错: {e}")
                                continue
                    
                except socket.timeout:
                    # 超时是正常的，继续循环
                    continue
                except socket.error as e:
                    logging.error(f"接受连接时出错: {e}")
                    # 短暂暂停后继续
                    time.sleep(0.1)
                except Exception as e:
                    logging.error(f"处理连接时发生未预期的错误: {e}")
                    time.sleep(0.1)
                    # 确保即使发生异常，服务器也会继续运行
                    continue
            
        except socket.error as e:
            logging.error(f"绑定端口 {self.port} 失败: {e}")
            raise RuntimeError(f"无法在 {self.host}:{self.port} 启动服务器: {e}")
        except Exception as e:
            logging.error(f"服务器启动失败: {e}")
            # 只有在启动阶段失败时才关闭套接字
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
            raise
    
    def stop(self):
        """优雅地停止代理服务器"""
        logging.info("正在停止代理服务器...")
        
        # 设置停止标志
        self.running = False
        
        # 关闭服务器套接字
        if self.server_socket:
            try:
                self.server_socket.close()
                logging.info("服务器套接字已关闭")
            except Exception as e:
                logging.error(f"关闭服务器套接字时出错: {e}")
        
        # 停止线程池
        self.thread_pool.stop()
        
        # 关闭连接池中的所有连接
        self.connection_pool.close_all()
        
        # 等待所有线程结束
        active_threads = 0
        for thread in self.threads:
            if thread.is_alive():
                active_threads += 1
                try:
                    thread.join(0.5)  # 每个线程最多等待0.5秒
                except:
                    pass
        
        # 打印最终统计
        with self.stats_lock:
            logging.info(f"=== 代理服务器最终统计 ===")
            logging.info(f"总请求数: {self.stats['requests_count']}")
            logging.info(f"成功请求: {self.stats['successful_requests']}")
            logging.info(f"失败请求: {self.stats['failed_requests']}")
            logging.info(f"总错误数: {self.stats['error_count']}")
            logging.info(f"总重试次数: {self.stats['retry_count']}")
            logging.info(f"发送字节数: {self.stats['bytes_sent']}")
            logging.info(f"接收字节数: {self.stats['bytes_received']}")
            logging.info(f"连接池命中: {self.stats.get('connection_pool_hits', 0)}")
            logging.info(f"连接池未命中: {self.stats.get('connection_pool_misses', 0)}")
            logging.info(f"WebSocket连接: {self.stats.get('websocket_connections', 0)}")
            logging.info(f"未关闭的线程数: {active_threads}")
            logging.info(f"========================")
        
        if hasattr(self, 'error_types') and self.error_types:
            logging.info("=== 错误类型统计 ===")
            for error_type, count in self.error_types.items():
                logging.info(f"{error_type}: {count}")
            logging.info(f"========================")
        
        logging.info("代理服务器已停止")
    
    def _record_error(self, error_type, error_message):
        """记录错误信息和统计
        
        Args:
            error_type: 错误类型
            error_message: 错误消息
        """
        # 更新总体错误统计
        with self.stats_lock:
            self.stats['error_count'] += 1
        
        # 更新GUI错误类型统计和历史记录
        with self.error_history_lock:
            if error_type not in self.error_types:
                self.error_types[error_type] = 0
            self.error_types[error_type] += 1
            
            # 添加到错误历史记录
            error_record = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'type': error_type,
                'message': error_message
            }
            self.error_history.append(error_record)
        
        # 增强的日志记录策略
        # 关键错误记录为ERROR级别
        critical_errors = [
            'connect_error', 'bind_error', 'timeout_error', 
            'client_timeout', 'client_socket_error', 'client_processing_error',
            'https_connect_timeout', 'https_connect_error', 'https_processing_error',
            'forward_timeout', 'forward_socket_error', 'tunnel_general_error'
        ]
        
        # 连接池相关错误记录为WARNING级别
        warning_errors = [
            'connection_pool_full', 'connection_pool_timeout',
            'connection_pool_hits', 'connection_pool_misses'
        ]
        
        if error_type in critical_errors:
            logging.error(f"{error_type}: {error_message}")
        elif error_type in warning_errors:
            logging.warning(f"{error_type}: {error_message}")
        else:
            logging.debug(f"{error_type}: {error_message}")
    
    def handle_client(self, client_socket, client_address):
        """处理客户端连接
        
        Args:
            client_socket: 客户端套接字
            client_address: 客户端地址
        """
        request_successful = False
        request_record = {
            'client': f"{client_address[0]}:{client_address[1]}",  # 添加client字段供GUI使用
            'port': 'unknown',  # 添加port字段供GUI使用
            'client_ip': client_address[0],
            'client_port': client_address[1],
            'start_time': datetime.now(),
            'status': 'processing',
            'request_type': 'unknown',
            'target_host': 'unknown',
            'target_port': 'unknown',
            'target_ip': 'unknown',  # 添加target_ip字段供GUI使用
            'bytes_received': 0,
            'bytes_sent': 0
        }
        
        try:
            # 接收客户端请求
            try:
                request_data = client_socket.recv(self.buffer_size)
                if not request_data:
                    logging.debug(f"来自 {client_address} 的空请求")
                    request_record['status'] = 'empty'
                    return
                
                # 更新请求统计
                with self.stats_lock:
                    self.stats['requests_count'] += 1
                    self.stats['bytes_received'] += len(request_data)
                
                request_record['bytes_received'] = len(request_data)
                
                # 尝试解析请求以获取更多信息（用于GUI显示）
                try:
                    # 确保只对bytes类型调用decode
                    if isinstance(request_data, bytes):
                        request_str = request_data.decode('utf-8', errors='ignore')
                    else:
                        request_str = str(request_data)
                    if request_str.startswith('CONNECT'):
                        request_record['request_type'] = 'HTTPS'
                        # 提取目标主机和端口
                        connect_match = re.search(r'CONNECT\s+([^:\s]+):(\d+)', request_str)
                        if connect_match:
                            request_record['target_host'] = connect_match.group(1)
                            request_record['target_port'] = connect_match.group(2)
                            # 尝试将主机名解析为IP地址
                            try:
                                host = connect_match.group(1)
                                # 使用socket.getaddrinfo获取IP地址
                                addr_info = socket.getaddrinfo(host, None, socket.AF_INET)
                                if addr_info:
                                    request_record['target_ip'] = addr_info[0][4][0]  # 获取IPv4地址
                            except Exception as e:
                                logging.debug(f"无法解析主机名 {host} 的IP地址: {e}")
                    else:
                        request_record['request_type'] = 'HTTP'
                        # 提取目标主机
                        host_match = re.search(r'Host:\s+([^\r\n]+)', request_str, re.IGNORECASE)
                        if host_match:
                            host_port = host_match.group(1)
                            if ':' in host_port:
                                host, port = host_port.split(':', 1)
                                request_record['target_host'] = host
                                request_record['target_port'] = port
                                # 尝试将主机名解析为IP地址
                                try:
                                    addr_info = socket.getaddrinfo(host, None, socket.AF_INET)
                                    if addr_info:
                                        request_record['target_ip'] = addr_info[0][4][0]  # 获取IPv4地址
                                except Exception as e:
                                    logging.debug(f"无法解析主机名 {host} 的IP地址: {e}")
                            else:
                                request_record['target_host'] = host_port
                                request_record['target_port'] = '80'
                                # 尝试将主机名解析为IP地址
                                try:
                                    addr_info = socket.getaddrinfo(host_port, None, socket.AF_INET)
                                    if addr_info:
                                        request_record['target_ip'] = addr_info[0][4][0]  # 获取IPv4地址
                                except Exception as e:
                                    logging.debug(f"无法解析主机名 {host_port} 的IP地址: {e}")
                except:
                    pass
                self.process_request(client_socket, request_data, client_address)
                request_successful = True
                request_record['status'] = 'completed'
                
            except socket.timeout:
                error_msg = f"客户端 {client_address} 连接超时"
                logging.warning(error_msg)
                self._record_error('client_timeout', error_msg)
                self._send_error_response(client_socket, 408, "Request Timeout")
                request_record['status'] = 'timeout'
            except socket.error as e:
                error_msg = f"客户端 {client_address} 连接错误: {e}"
                logging.error(error_msg)
                self._record_error('client_socket_error', str(e))
                self._send_error_response(client_socket, 500, "Internal Server Error")
                request_record['status'] = 'socket_error'
                request_record['error'] = str(e)
            except Exception as e:
                error_msg = f"处理客户端 {client_address} 请求时出错: {e}"
                logging.error(f"{error_msg}\n{traceback.format_exc()}")
                self._record_error('client_processing_error', str(e))
                self._send_error_response(client_socket, 500, "Internal Server Error")
                request_record['status'] = 'error'
                request_record['error'] = str(e)
                
        finally:
            # 更新统计
            with self.stats_lock:
                self.stats['active_connections'] -= 1
                if request_successful:
                    self.stats['successful_requests'] += 1
                else:
                    self.stats['failed_requests'] += 1
                    
            # 完成请求记录
            request_record['end_time'] = datetime.now()
            request_record['duration'] = (request_record['end_time'] - request_record['start_time']).total_seconds()
            
            # 添加到请求历史（用于GUI显示）
            with self.request_history_lock:
                request_record['timestamp'] = request_record['start_time'].strftime('%Y-%m-%d %H:%M:%S')
                self.request_history.append(request_record)
            
            # 关闭客户端连接
            try:
                client_socket.close()
            except:
                pass
    
    def forward_request(self, client_socket, request_data, host, port, is_websocket=False):
        """转发HTTP请求到目标服务器
        
        Args:
            client_socket: 客户端套接字
            request_data: 请求数据
            host: 目标主机
            port: 目标端口
            is_websocket: 是否为WebSocket请求
        """
        server_socket = None
        request_successful = False
        try:
            # 尝试从连接池获取连接
            server_socket = self.connection_pool.get_connection(host, port)
            
            if server_socket:
                with self.stats_lock:
                    self.stats['connection_pool_hits'] += 1
            else:
                # 创建新连接
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.settimeout(self.connection_timeout)
                server_socket.connect((host, port))
                with self.stats_lock:
                    self.stats['connection_pool_misses'] += 1
            
            # 发送请求到目标服务器
            server_socket.sendall(request_data)
            
            # 接收响应并转发给客户端
            if is_websocket:
                # WebSocket连接需要保持双向通信
                self.tunnel_data(client_socket, server_socket)
            else:
                # 普通HTTP请求只需要转发响应
                while True:
                    response_data = server_socket.recv(self.buffer_size)
                    if not response_data:
                        break
                    
                    # 更新统计信息
                    with self.stats_lock:
                        self.stats['bytes_sent'] += len(response_data)
                    
                    # 修改响应头（如果启用）
                    if self.modify_headers:
                        response_data = self.modify_response_headers(response_data)
                    
                    # 转发响应给客户端
                    client_socket.sendall(response_data)
            
            request_successful = True
            
        except socket.timeout:
            self._record_error('forward_timeout', f"连接到 {host}:{port} 超时")
        except socket.error as e:
            self._record_error('forward_socket_error', f"连接到 {host}:{port} 时发生套接字错误: {str(e)}")
        except Exception as e:
            self._record_error('forward_general_error', f"转发请求到 {host}:{port} 时发生错误: {str(e)}")
        finally:
            # 将连接放回连接池或关闭
            if server_socket:
                try:
                    # 检查连接是否仍然可用
                    server_socket.settimeout(0.1)
                    server_socket.recv(1, socket.MSG_PEEK)
                    # 如果没有异常，将连接放回连接池
                    self.connection_pool.return_connection(host, port, server_socket)
                except:
                    # 如果连接不可用，关闭它
                    server_socket.close()
        
        return request_successful

    def _send_error_response(self, client_socket, status_code, status_message):
        """发送错误响应给客户端
        
        Args:
            client_socket: 客户端套接字
            status_code: HTTP状态码
            status_message: 状态消息
        """
        try:
            response = (f"HTTP/1.1 {status_code} {status_message}\r\n"  
                      f"Content-Type: text/html\r\n"  
                      f"Connection: close\r\n"  
                      f"X-Proxy-By: SimplePythonProxy\r\n"  
                      f"\r\n"  
                      f"<html><body><h1>{status_code} {status_message}</h1><p>代理服务器错误</p></body></html>")
            client_socket.sendall(response.encode('utf-8'))
        except:
            # 忽略发送错误响应时的异常
            pass
    
    def process_request(self, client_socket, request_data, client_address):
        """处理HTTP请求并转发到目标服务器"""
        try:
            # 解码请求数据以解析
            # 确保只对bytes类型调用decode
            if isinstance(request_data, bytes):
                request_text = request_data.decode('utf-8', errors='ignore')
            else:
                request_text = str(request_data)
            
            # 检查是否为CONNECT方法（HTTPS隧道）
            if request_text.startswith('CONNECT'):
                self.handle_https_connect(client_socket, request_text, client_address)
            else:
                # 处理普通HTTP请求
                self.handle_http_request(client_socket, request_data, request_text, client_address)
                
        except Exception as e:
            logging.error(f"处理请求时出错: {e}")
            # 发送错误响应给客户端
            self._send_error_response(client_socket, 500, "Internal Server Error")
    
    def handle_http_request(self, client_socket, request_data, request_text, client_address):
        """处理普通HTTP请求，支持WebSocket和连接池"""
        # 解析请求行以获取目标主机和端口
        try:
            # 提取请求行
            request_lines = request_text.split('\r\n')
            if not request_lines:
                return
                
            request_line = request_lines[0]
            logging.info(f"{client_address} 请求: {request_line}")
            
            # 解析主机和端口
            host = None
            port = 80  # 默认HTTP端口
            
            # 尝试从Host头获取主机信息
            for line in request_lines:
                if line.startswith('Host:'):
                    host_line = line.split(':', 1)[1].strip()
                    if ':' in host_line:
                        host, port_str = host_line.split(':', 1)
                        port = int(port_str)
                    else:
                        host = host_line
                    break
            
            if not host:
                # 如果没有找到Host头，尝试从URL中解析
                parts = request_line.split(' ')
                if len(parts) >= 2:
                    url = parts[1]
                    if url.startswith('http://'):
                        url = url[7:]  # 去掉http://
                    host_port = url.split('/')[0]
                    if ':' in host_port:
                        host, port_str = host_port.split(':', 1)
                        port = int(port_str)
                    else:
                        host = host_port
            
            if not host:
                raise ValueError("无法从请求中解析主机信息")
            
            # 检测是否为WebSocket请求
            is_websocket = False
            for line in request_lines:
                if line.strip().lower() == 'upgrade: websocket':
                    is_websocket = True
                    with self.stats_lock:
                        self.stats['websocket_connections'] += 1
                    break
            
            # 转发请求到目标服务器（添加安全检查）
            if hasattr(self, 'forward_request'):
                self.forward_request(client_socket, request_data, host, port, is_websocket)
            else:
                logging.error("方法 forward_request 不存在，使用简单请求转发")
                # 简单的HTTP请求转发作为后备方案
                try:
                    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    server_socket.settimeout(self.connection_timeout)
                    server_socket.connect((host, port))
                    server_socket.sendall(request_data)
                    
                    # 接收并转发响应
                    while True:
                        response_data = server_socket.recv(self.buffer_size)
                        if not response_data:
                            break
                        client_socket.sendall(response_data)
                except Exception as e:
                    logging.error(f"简单请求转发出错: {e}")
                    error_response = "HTTP/1.1 502 Bad Gateway\r\n\r\n"
                    try:
                        client_socket.sendall(error_response.encode())
                    except:
                        pass
                finally:
                    if 'server_socket' in locals():
                        try:
                            server_socket.close()
                        except:
                            pass
            
        except Exception as e:
            logging.error(f"处理HTTP请求时出错: {e}")
            error_response = "HTTP/1.1 400 Bad Request\r\n\r\n"
            try:
                client_socket.sendall(error_response.encode())
            except:
                pass
    
    def handle_https_connect(self, client_socket, request_data, client_address):
        """处理HTTPS CONNECT请求
        
        Args:
            client_socket: 客户端套接字
            request_data: 请求数据
            client_address: 客户端地址
        """
        # 确保只对bytes类型调用decode
        if isinstance(request_data, bytes):
            request_str = request_data.decode('utf-8', errors='ignore')
        else:
            request_str = str(request_data)
        request_info = {
            'client_ip': client_address[0],
            'client_port': client_address[1],
            'request_type': 'HTTPS',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 解析目标主机和端口
        match = re.search(r'CONNECT\s+([^:\s]+):(\d+)', request_str)
        if not match:
            error_msg = f"无效的HTTPS连接请求: {request_str[:100]}..."
            logging.error(error_msg)
            self._record_error('invalid_connect_request', error_msg)
            self._send_error_response(client_socket, 400, "Bad Request")
            return
        
        host = match.group(1)
        port = int(match.group(2))
        request_info['target_host'] = host
        request_info['target_port'] = port
        
        # 检查黑白名单（添加安全检查）
        try:
            if hasattr(self, '_check_host_access') and not self._check_host_access(host):
                error_msg = f"主机 {host} 不在允许列表中"
                logging.warning(error_msg)
                if hasattr(self, '_record_error'):
                    self._record_error('host_access_denied', error_msg)
                if hasattr(self, '_send_error_response'):
                    self._send_error_response(client_socket, 403, "Forbidden")
                return
        except Exception as e:
            logging.debug(f"主机访问检查出错，跳过检查: {e}")
        
        # 更新统计
        with self.stats_lock:
            self.stats['total_https_requests'] += 1
        
        # 尝试连接目标服务器
        retry_count = 0
        server_socket = None
        connected = False
        
        while retry_count <= self.max_retries and not connected:
            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.settimeout(self.connection_timeout)
                server_socket.connect((host, port))
                connected = True
                logging.debug(f"成功连接到HTTPS服务器 {host}:{port}")
                break
                
            except socket.timeout:
                error_msg = f"连接HTTPS服务器 {host}:{port} 超时"
                retry_count += 1
                if retry_count <= self.max_retries:
                    logging.warning(f"{error_msg}, 第 {retry_count} 次重试...")
                    with self.stats_lock:
                        self.stats['retry_count'] += 1
                    time.sleep(0.5)
                else:
                    logging.error(f"{error_msg}, 已达到最大重试次数")
                    self._record_error('https_connect_timeout', error_msg)
                    self._send_error_response(client_socket, 504, "Gateway Timeout")
                    return
                    
            except socket.error as e:
                error_msg = f"连接HTTPS服务器 {host}:{port} 失败: {e}"
                retry_count += 1
                if retry_count <= self.max_retries:
                    logging.warning(f"{error_msg}, 第 {retry_count} 次重试...")
                    with self.stats_lock:
                        self.stats['retry_count'] += 1
                    time.sleep(0.5)
                else:
                    logging.error(f"{error_msg}, 已达到最大重试次数")
                    self._record_error('https_connect_error', str(e))
                    self._send_error_response(client_socket, 502, "Bad Gateway")
                    return
        
        if not connected:
            return
        
        # 发送200 OK响应给客户端
        try:
            response = b"HTTP/1.1 200 Connection Established\r\n\r\n"
            client_socket.sendall(response)
            
            # 开始数据隧道（添加安全检查）
            if hasattr(self, 'tunnel_data'):
                self.tunnel_data(client_socket, server_socket, request_info)
            else:
                logging.error("方法 tunnel_data 不存在，无法建立数据隧道")
                # 简单的数据转发作为后备方案
                try:
                    while True:
                        # 客户端到服务器
                        client_data = client_socket.recv(self.buffer_size)
                        if not client_data:
                            break
                        server_socket.sendall(client_data)
                        # 服务器到客户端
                        server_data = server_socket.recv(self.buffer_size)
                        if not server_data:
                            break
                        client_socket.sendall(server_data)
                except Exception as e:
                    logging.debug(f"简单数据转发出错: {e}")
            
        except Exception as e:
            error_msg = f"处理HTTPS连接时出错: {e}"
            logging.error(f"{error_msg}\n{traceback.format_exc()}")
            self._record_error('https_processing_error', str(e))
            try:
                self._send_error_response(client_socket, 500, "Internal Server Error")
            except:
                pass
        finally:
            try:
                if server_socket:
                    server_socket.close()
            except:
                pass

def handle_client_safely(proxy, client_socket, client_address):
    """安全处理客户端连接的包装函数"""
    try:
        proxy.handle_client(client_socket, client_address)
    except Exception as e:
        logging.error(f"处理客户端连接时发生异常: {e}\n{traceback.format_exc()}")
        # 确保客户端连接被关闭
        try:
            client_socket.close()
            # 更新统计信息
            try:
                with proxy.stats_lock:
                    if proxy.stats['active_connections'] > 0:
                        proxy.stats['active_connections'] -= 1
            except:
                pass
        except:
            pass

def main():
    """主函数，解析命令行参数并启动代理服务器"""
    # 设置全局异常处理
    def global_exception_handler(exctype, value, tb):
        logging.error(f"全局异常: {exctype.__name__}: {value}\n{''.join(traceback.format_tb(tb))}")
        # 不退出程序，继续运行
    
    # 安装全局异常处理
    sys.excepthook = global_exception_handler
    
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='简易HTTP/HTTPS代理服务器')
    parser.add_argument('-p', '--port', type=int, default=8080, help='代理服务器监听端口（默认: 8080）')
    parser.add_argument('-H', '--host', type=str, default='0.0.0.0', help='代理服务器监听地址（默认: 0.0.0.0）')
    parser.add_argument('-b', '--buffer-size', type=int, default=4096, help='缓冲区大小（默认: 4096）')
    parser.add_argument('--no-modify-headers', action='store_true', help='禁用响应头修改')
    parser.add_argument('-r', '--retries', type=int, default=3, help='最大重试次数（默认: 3）')
    parser.add_argument('-t', '--timeout', type=int, default=10, help='连接超时时间（秒，默认: 10）')
    parser.add_argument('--debug', action='store_true', help='启用调试日志')
    
    # 解析参数
    args = parser.parse_args()
    
    # 设置日志
    setup_logging(args.debug)
    
    logging.info(f"启动代理服务器，按 Ctrl+C 停止")
    
    # 服务器启动循环 - 如果发生错误，自动重启
    while True:
        proxy = None
        try:
            # 创建代理服务器实例
            proxy = ProxyServer(
                host=args.host,
                port=args.port,
                buffer_size=args.buffer_size,
                modify_headers=not args.no_modify_headers,
                max_retries=args.retries,
                connection_timeout=args.timeout
            )
            
            # 重写handle_client方法，使用安全包装
            original_handle_client = proxy.handle_client
            proxy.handle_client = lambda sock, addr, p=proxy: handle_client_safely(p, sock, addr)
            
            # 启动服务器
            proxy.start()
            
        except KeyboardInterrupt:
            logging.info("接收到中断信号，正在停止服务器...")
            if proxy:
                try:
                    proxy.stop()
                except:
                    pass
            break
        except Exception as e:
            logging.error(f"服务器发生严重错误，正在重启: {e}\n{traceback.format_exc()}")
            if proxy:
                try:
                    proxy.stop()
                except:
                    pass
            logging.info("5秒后重启服务器...")
            time.sleep(5)
            continue

# 修改ThreadPool类的submit方法，确保线程执行安全
def safe_worker(func, *args):
    """安全执行工作函数的包装器"""
    try:
        func(*args)
    except Exception as e:
        logging.error(f"线程执行时发生异常: {e}\n{traceback.format_exc()}")

# 保存原始的submit方法
original_submit = None

def patch_thread_pool_submit(self):
    """在ProxyServer类初始化时替换线程池的submit方法"""
    global original_submit
    if not original_submit:
        original_submit = self.thread_pool.submit
    
    def safe_submit(func, *args):
        """安全执行工作函数的包装器"""
        try:
            func(*args)
        except Exception as e:
            logging.error(f"线程执行时发生异常: {e}\n{traceback.format_exc()}")
    
    # 替换线程池的submit方法
    self.thread_pool.submit = safe_submit

    # 注意：patch_thread_pool_submit函数结束在这里
    
    def tunnel_data(self, client_socket, server_socket, request_info):
        """在客户端和服务器之间建立数据隧道（用于HTTPS）
        
        Args:
            client_socket: 客户端套接字
            server_socket: 服务器套接字
            request_info: 请求信息字典
        """
        try:
            while self.running:
                # 检查套接字是否有效
                valid_sockets = []
                try:
                    # 检查客户端套接字是否有效
                    if client_socket and hasattr(client_socket, 'fileno') and client_socket.fileno() != -1:
                        valid_sockets.append(client_socket)
                    # 检查服务器套接字是否有效
                    if server_socket and hasattr(server_socket, 'fileno') and server_socket.fileno() != -1:
                        valid_sockets.append(server_socket)
                except:
                    # 如果检查失败，假设套接字无效
                    pass
                
                # 如果没有有效的套接字，退出循环
                if not valid_sockets:
                    break
                    
                # 使用select监听有效的套接字
                read_sockets, _, _ = select.select(valid_sockets, [], valid_sockets, self.connection_timeout)
                
                if not read_sockets:
                    # 超时，关闭连接
                    break
                
                # 处理客户端到服务器的数据
                if client_socket in read_sockets:
                    try:
                        client_data = client_socket.recv(self.buffer_size)
                        if not client_data:
                            break
                        server_socket.sendall(client_data)
                        bytes_received = len(client_data)
                        bytes_sent = len(client_data)
                        with self.stats_lock:
                            self.stats['bytes_received'] += bytes_received
                            self.stats['bytes_sent'] += bytes_sent
                        # 更新带宽统计信息
                        self._update_bandwidth_stats(bytes_sent, bytes_received)
                    except Exception as e:
                        error_msg = f"从客户端读取数据错误: {e}"
                        logging.error(error_msg)
                        self._record_error('tunnel_client_read_error', str(e))
                        break
                
                # 处理服务器到客户端的数据
                if server_socket in read_sockets:
                    try:
                        server_data = server_socket.recv(self.buffer_size)
                        if not server_data:
                            break
                        client_socket.sendall(server_data)
                        bytes_received = len(server_data)
                        bytes_sent = len(server_data)
                        with self.stats_lock:
                            self.stats['bytes_received'] += bytes_received
                            self.stats['bytes_sent'] += bytes_sent
                        # 更新带宽统计信息
                        self._update_bandwidth_stats(bytes_sent, bytes_received)
                    except Exception as e:
                        error_msg = f"从服务器读取数据错误: {e}"
                        logging.error(error_msg)
                        self._record_error('tunnel_server_read_error', str(e))
                        break
                
        except Exception as e:
            error_msg = f"数据隧道错误: {e}"
            logging.error(f"{error_msg}\n{traceback.format_exc()}")
            self._record_error('tunnel_general_error', str(e))
        finally:
            # 清理WebSocket统计
            if request_info.get('is_websocket', False):
                with self.stats_lock:
                    if self.stats['websocket_connections'] > 0:
                        self.stats['websocket_connections'] -= 1

def run_proxy_server(args):
    """运行代理服务器实例"""
    proxy = None
    try:
        # 创建并启动代理服务器
        proxy = ProxyServer(
            host=args.host,
            port=args.port,
            buffer_size=args.buffer_size,
            modify_headers=not args.no_modify_headers,
            max_retries=args.retries,
            connection_timeout=args.timeout
        )
        
        logging.info(f"启动代理服务器，按 Ctrl+C 停止")
        proxy.start()
        # 检查是否需要重启（如果ProxyServer类缺少关键方法，也应该重启）
        if not hasattr(proxy, 'tunnel_data') or not hasattr(proxy, 'forward_request'):
            logging.error("检测到代理服务器缺少关键方法，需要重启以修复")
            return True  # 需要重启
        return False  # 正常退出
    except KeyboardInterrupt:
        logging.info("接收到中断信号，正在停止服务器...")
        return False  # 用户中断，不重启
    except Exception as e:
        logging.error(f"服务器运行时发生错误: {e}\n{traceback.format_exc()}")
        return True  # 异常退出，需要重启
    finally:
        if proxy:
            try:
                proxy.stop()
            except:
                logging.error("停止服务器时发生错误")

def main():
    """主函数，解析命令行参数并启动代理服务器（支持自动重启）"""
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='高性能HTTP/HTTPS代理服务器')
    parser.add_argument('-p', '--port', type=int, default=8080, help='代理服务器监听端口（默认: 8080）')
    parser.add_argument('-H', '--host', type=str, default='0.0.0.0', help='代理服务器监听地址（默认: 0.0.0.0）')
    parser.add_argument('-b', '--buffer-size', type=int, default=4096, help='缓冲区大小（默认: 4096）')
    parser.add_argument('--no-modify-headers', action='store_true', help='禁用响应头修改')
    parser.add_argument('-r', '--retries', type=int, default=3, help='最大重试次数（默认: 3）')
    parser.add_argument('-t', '--timeout', type=int, default=10, help='连接超时时间（秒，默认: 10）')
    parser.add_argument('--debug', action='store_true', help='启用调试日志')
    parser.add_argument('--no-restart', action='store_true', help='禁用自动重启功能')
    
    # 解析参数
    args = parser.parse_args()
    
    # 设置日志
    setup_logging(args.debug)
    
    restart_count = 0
    max_restarts = 10  # 限制最大重启次数
    min_restart_interval = 1  # 最小重启间隔（秒）
    max_restart_interval = 5  # 最大重启间隔（秒）
    
    logging.info("代理服务器启动系统初始化完成")
    logging.info(f"自动重启功能: {'禁用' if args.no_restart else '启用'}")
    
    try:
        while True:
            logging.info(f"启动代理服务器实例 (重启计数: {restart_count})")
            needs_restart = run_proxy_server(args)
            
            # 检查是否需要重启
            if not needs_restart or args.no_restart:
                break
            
            restart_count += 1
            if restart_count > max_restarts:
                logging.error(f"已达到最大重启次数 ({max_restarts})，停止自动重启")
                break
            
            # 计算重启间隔，实现指数退避
            wait_time = min(min_restart_interval * (2 ** (restart_count - 1)), max_restart_interval)
            logging.info(f"{wait_time:.1f}秒后将自动重启代理服务器...")
            time.sleep(wait_time)
            
            # 清理内存，防止资源泄漏
            logging.info("执行垃圾回收，清理资源...")
            gc.collect()
            
    except KeyboardInterrupt:
        logging.info("接收到全局中断信号，退出程序")
    
    logging.info("代理服务器主程序已退出")

if __name__ == "__main__":
    main()