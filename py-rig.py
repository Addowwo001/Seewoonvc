# Copyright (C) 2026 PYRIG
#
# This file is part of PYRIG.
#
# PYRIG is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PYRIG is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PYRIG. If not, see <https://www.gnu.org/licenses/>.

# Import libraries
import traceback, os, sys, time, ssl, json, socket, psutil, struct, threading, queue, argparse, platform, ctypes
from datetime import datetime; from cffi import FFI; from colorama import init, Fore, Style; init()

# All global variables
hash_counter = 0
hash_counter_lock = threading.Lock()
seed_changed_flag = threading.Event()
global_randomx_cache = None
global_randomx_dataset = None
global_randomx_lock = threading.Lock()
global_seed_hash = None
BATCH_MIN = 100
SUBMIT_TIMEOUT = 15
BATCH_MAX = 5000
TARGET_BATCH_TIME = 0.2
last_batch_reset = time.time()
BATCH_RESET_INTERVAL = 300
current_seed_hash = None
batch_avg_time = TARGET_BATCH_TIME
pool_socket = None
pool_host = None
pool_port = None
current_batch_size = 1500
batch_lock = threading.Lock()
job_queue = queue.Queue(maxsize=500)
submit_queue = queue.Queue(maxsize=1000)
net_queue = queue.Queue()
submit_response_queue = queue.Queue()
mining_threads = []
shutdown_flag = threading.Event()
submit_id_counter = 0
pending_submits = {}
pending_submits_lock = threading.Lock()
latest_job = None
stats_lock = threading.Lock()
stats = {
    "hashes": 0, "accepted": 0, "rejected": 0,
    "last_share": time.time(), "start_time": time.time(),
    "hash_history": [],
    "last_stat_print": 0
}
job_version = 0
job_lock = threading.Lock()
session_id = None
target_cache = {}
target_cache_lock = threading.Lock()
current_valid_job_id = None
previous_valid_job_id = None
current_valid_job_lock = threading.Lock()

# Argument Parser
parser = argparse.ArgumentParser(description="PYRIG (V.4.0) - CPU Miner", formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("-o", "--url", dest="url",
    default="gulf.moneroocean.stream:443",
    help="Pool URL in format host:port (stratum+tcp:// prefix optional)")
parser.add_argument("-u", "--user", dest="user",
    default="43jBxAR5zV4HvJLpMzECjt6QLs3zEhrhfKqxaRVGfY2f614Do1NbFgZekjtdE9fDRw6R4fP2q2N2i7427bsLTSxdCGFVfmr",
    help="Wallet address and optional worker name for pool authentication")
parser.add_argument("-p", "--password", dest="password",
    default="x",
    help="Password for pool authentication (default: 'x')")
parser.add_argument("--tls", action="store_true", dest="tls",
    help="Enable TLS/SSL encryption for pool connection (default)")
parser.add_argument("--tls-insecure", action="store_true",
    help="Disable TLS certificate verification (use with caution)")
parser.add_argument("--mode", dest="mode", choices=["full", "light"],
    default="full",
    help="RandomX mining mode: 'full' for 2GB dataset or 'light' for cache-only")
parser.add_argument("-t", "--threads", dest="threads", type=int,
    default=f"{max(1, os.cpu_count() - 1)}",
    help="Number of mining threads (default: CPU cores - 1)")
parser.add_argument("--debug", action="store_true",
    help="Enable debug logging for troubleshooting")
parser.add_argument("--verbose", action="store_true",
    help="Enable verbose logging with detailed mining information")
parser.add_argument("--init-timeout", dest="init_timeout", type=int,
    default=120,
    help="Timeout in seconds for RandomX VM initialization")
parser.add_argument("--background", action="store_true",
    help="Run miner in background mode with minimal console output")
parser.add_argument("--submit-throttle", dest="submit_throttle", type=int,
    default=None,
    help="Maximum difficulty threshold for share submission to reduce network load")
args = parser.parse_args()

# Background logger utility class
class BackgroundLogger:
    """Logging utility with background mode awareness"""
    @staticmethod
    def debug(message):
        """Log debug message only when debug mode is enabled"""
        if args.debug and not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Fore.YELLOW}[DEBUG]{Style.RESET_ALL} {message}")
    @staticmethod
    def error(message):
        """Log error message (always displayed)"""
        print(Fore.RED + f"[{datetime.now().strftime('%H:%M:%S')}]  [ERROR] {message} " + Style.RESET_ALL)

# Display program logo
def shorten_wallet(addr, start=24, end=4):
    """Shorten wallet address for display if too long"""
    if len(addr) <= start + end:
        return addr
    return addr[:start] + "..." + addr[-end:]
def display_startup_banner():
    """Display miner startup banner with configuration summary"""
    if args.background:
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * {Fore.GREEN}PYRIG (V.4.0){Style.RESET_ALL} / Python Interpreter Version ({sys.version.split()[0]})")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  *")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * ABOUT           : https://github.com/codeanli/pyrig")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * DONATE          : USDT: 0x7FF1753ac9fb1fb2008f1328bd79d0d70B7D3831 (BSC)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * SYSTEM INFO     : Microsoft ({platform.system}/{platform.node} {platform.release} {platform.version} {platform.machine})")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  *")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * CONFIGURATION")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * POOL ADDRESS    : stratum+tcp://{args.url}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * WALLET ADDRESS  : {shorten_wallet(args.user)}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * RANDOMX MODE    : {args.mode} ('full' for 2GB dataset or 'light' for cache-only)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * CPU THREADS     : {args.threads}T ({platform.processor()}, {psutil.cpu_count(logical=False)}C, {psutil.cpu_count(logical=True)}T)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * TLS/SSL CONNECT : {args.tls} (TLS insecure mode {Fore.GREEN}{args.tls_insecure}{Style.RESET_ALL}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * TIMEOUT INIT    : {args.init_timeout}s (RandomX init timeout default 120s)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * THROTTLE SUBMIT : {args.submit_throttle}")
display_startup_banner()

# Program support functions
def stratum_send(sock, message):
    """
    Send JSON message to stratum pool server
    Args:
        sock: Socket connection to pool
        message: Dictionary containing stratum protocol message
    """
    if args.debug and not args.background:
        BackgroundLogger.debug(f"{Fore.MAGENTA}[NET]{Style.RESET_ALL} sending: {json.dumps(message)}")
    data = json.dumps(message) + "\n"
    try:
        sock.sendall(data.encode("utf-8"))
    except Exception as e:
        BackgroundLogger.error(f"send failed: {e}")
        raise
def parse_pool_url(url, use_tls):
    """
    Parse pool URL into host and port components
    Args:
        url: Pool URL string
        use_tls: Boolean indicating if TLS should be used
    Returns:
        Tuple of (host, port)
    """
    if '://' in url:
        url = url.split('://')[1]
    if ':' in url:
        host, port = url.split(':', 1)
        port = int(port)
    else:
        host = url
        port = 443 if use_tls else 10032
    return host, port
def hashrate_reporter():
    """Send hashreport every 30s to mining pool"""
    global hash_counter
    time.sleep(10)
    last_reported = 0
    while not shutdown_flag.is_set():
        time.sleep(30)
        with hash_counter_lock:
            hashes = hash_counter
        delta = hashes - last_reported
        last_reported = hashes
        hr = int(delta / 30)
        if session_id:
            msg = {
                "method": "hashrate",
                "params": {
                    "id": session_id,
                    "hashrate": hr
                }
            }
            try:
                stratum_send(msg)
            except Exception as e:
                if args.debug:
                    print(f"[DEBUG] hashrate send failed: {e}")
def create_pool_connection(host, port, use_tls, tls_insecure=False):
    """
    Create socket connection to mining pool
    Args:
        host: Pool server hostname
        port: Pool server port
        use_tls: Enable TLS encryption
        tls_insecure: Disable certificate verification
    Returns:
        Connected socket object
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((host, port))
        if use_tls:
            context = ssl.create_default_context()
            if tls_insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            ssl_sock = context.wrap_socket(sock, server_hostname=host)
            return ssl_sock
        return sock
    except Exception as e:
        BackgroundLogger.error(f"connection failed: {e}")
        sys.exit(1)
def hex_to_bytes(hex_str):
    """Convert hexadecimal string to bytes"""
    return bytes.fromhex(hex_str)
def bytes_to_hex(byte_data):
    """Convert bytes to hexadecimal string"""
    return byte_data.hex()
def format_difficulty(diff):
    """
    Format difficulty value with appropriate units (k, M, G)
    Args:
        diff: Difficulty value as integer
    Returns:
        Formatted difficulty string with unit suffix
    """
    if diff >= 1_000_000_000:
        return f"{diff/1_000_000_000:.2f}G"
    elif diff >= 1_000_000:
        return f"{diff/1_000_000:.2f}M"
    elif diff >= 1_000:
        return f"{diff/1_000:.2f}k"
    else:
        return str(diff)
def calculate_monero_difficulty(target_hex):
    """
    Calculate mining difficulty from pool's difficulty value
    For Monero/RandomX, pool sends DIFFICULTY, not target!
    Args:
        target_hex: Hexadecimal DIFFICULTY string (8 chars)
    Returns:
        Difficulty as integer
    """
    if len(target_hex) != 8:
        raise ValueError(f"Invalid difficulty length: {len(target_hex)} chars (expected 8)")
    difficulty = int(target_hex, 16)
    if args.debug and not args.background:
        BackgroundLogger.debug(f"Raw hex: 0x{target_hex}")
        BackgroundLogger.debug(f"Difficulty: {difficulty:,}")
    return difficulty
def set_new_job(job):
    """
    Process and distribute new mining job to workers
    Args:
        job: Dictionary containing job parameters from pool
    Returns:
        Boolean indicating if job was successfully queued
    """
    global job_version, current_valid_job_id, current_seed_hash
    new_job_version = job_version + 1
    with job_lock:
        job_version = new_job_version
    with current_valid_job_lock:
        global previous_valid_job_id
        previous_valid_job_id = current_valid_job_id
        current_valid_job_id = job["job_id"]
    job_data = {
        "job_id": job["job_id"],
        "blob": job["blob"],
        "target": job["target"],
        "seed_hash": job["seed_hash"],
        "nonce_offset": job.get("nonce_offset", 39),
        "height": job.get("height", 1),
        "version": new_job_version
    }
    new_seed = job["seed_hash"]
    if current_seed_hash and new_seed != current_seed_hash:
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [WRN] Seed hash changed! Re-initializing RandomX VMs...")
        seed_changed_flag.set()
    current_seed_hash = new_seed
    jobs_queued = 0
    for _ in range(args.threads):
        try:
            job_queue.put_nowait(job_data)
            jobs_queued += 1
        except queue.Full:
            if args.debug:
                BackgroundLogger.debug(f"Job queue full after {jobs_queued} jobs")
            break
    if jobs_queued == 0:
        return False
    else:
        return True
def convert_compact_target_to_bytes(target_hex):
    """
    Convert pool's DIFFICULTY to 8-byte TARGET for mining
    Args:
        target_hex: Hexadecimal difficulty string (8 chars)
    Returns:
        8-byte big-endian target representation
    Formula: target = max_target / difficulty
    max_target = 0xFFFFFFFFFFFFFFFF (2^64 - 1)
    """
    with target_cache_lock:
        if target_hex in target_cache:
            return target_cache[target_hex]
    if len(target_hex) != 8:
        raise ValueError(f"Invalid difficulty length: {len(target_hex)} chars (expected 8)")
    difficulty = int(target_hex, 16)
    if difficulty == 0:
        target_64bit = 0xFFFFFFFFFFFFFFFF
    else:
        MAX_TARGET_64BIT = 0xFFFFFFFFFFFFFFFF
        target_64bit = MAX_TARGET_64BIT // difficulty
    target_big_endian = target_64bit.to_bytes(8, 'big')
    with target_cache_lock:
        target_cache[target_hex] = target_big_endian
    if args.debug and not args.background:
        BackgroundLogger.debug(f"Difficulty from pool: {difficulty:,}")
        BackgroundLogger.debug(f"Calculated target: 0x{target_64bit:016x}")
        BackgroundLogger.debug(f"For mining: hash must be < 0x{target_64bit:016x}")
    return target_big_endian
def update_stats(hashes=0, accepted=0, rejected=0):
    """
    Update mining statistics
    Args:
        hashes: Number of hashes to add to total
        accepted: Number of accepted shares to add
        rejected: Number of rejected shares to add
    """
    with stats_lock:
        if hashes > 0:
            stats["hashes"] += hashes
        if accepted > 0:
            stats["accepted"] += accepted
            stats["last_share"] = time.time()
        if rejected > 0:
            stats["rejected"] += rejected
def hashrate_sampler():
    """Periodically sample hashrate for statistics"""
    last_hash = 0
    last_time = time.time()
    while not shutdown_flag.is_set():
        time.sleep(1)
        with hash_counter_lock:
            now_hash = hash_counter
        now_time = time.time()
        delta_hash = now_hash - last_hash
        delta_time = now_time - last_time
        if delta_time > 0:
            rate = delta_hash / delta_time
            with stats_lock:
                stats["hash_history"].append((rate, now_time))
                if len(stats["hash_history"]) > 120:
                    stats["hash_history"] = stats["hash_history"][-120:]
        last_hash = now_hash
        last_time = now_time
def print_stats():
    """Print current mining statistics"""
    if args.background or shutdown_flag.is_set():
        return
    now = time.time()
    with stats_lock:
        if now - stats.get("last_stat_print", 0) < 10:
            return
        stats["last_stat_print"] = now
        hash_history = stats["hash_history"].copy()
        accepted = stats["accepted"]
        rejected = stats["rejected"]
        last_share_time = stats["last_share"]
    if not hash_history:
        return
    def avg(window):
        cutoff = now - window
        vals = [h for h, t in hash_history if t >= cutoff]
        return sum(vals) / len(vals) if vals else 0
    rate_now = hash_history[-1][0]
    rate_10s = avg(10)
    rate_60s = avg(60)
    def format_hashrate(h):
        if h >= 1_000_000:
            return f"{h/1_000_000:.2f} MH/s"
        elif h >= 1_000:
            return f"{h/1_000:.2f} kH/s"
        else:
            return f"{h:.2f} H/s"
    print(f"[{datetime.now().strftime('%H:%M:%S')}]{Fore.BLUE}  [APP] {Style.RESET_ALL}speed now: {Fore.LIGHTYELLOW_EX}{format_hashrate(rate_now)}{Style.RESET_ALL} 10s: {Fore.LIGHTYELLOW_EX}{format_hashrate(rate_10s)}{Style.RESET_ALL} 60s: {Fore.LIGHTYELLOW_EX}{format_hashrate(rate_60s)}{Style.RESET_ALL}")
    total = accepted + rejected
    if total:
        ratio = accepted / total * 100
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}]      "
            f"accepted {accepted}/{total} ({ratio:.1f}%) "
            f"rejected {rejected}/{total}"
        )
    if accepted:
        ago = int(now - last_share_time)
        unit = "s" if ago < 60 else "m" if ago < 3600 else "h"
        value = ago if unit == "s" else ago // 60 if unit == "m" else ago // 3600
        print(f"[{datetime.now().strftime('%H:%M:%S')}]      last share {value}{unit} ago")
def shutdown_miner(sock=None):
    """Shutdown miner and cleanup resources"""
    global global_randomx_cache, global_randomx_dataset
    if shutdown_flag.is_set():
        return
    shutdown_flag.set()
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]{Fore.BLUE}  [APP] {Style.RESET_ALL}shutting down...")
    for thread in mining_threads:
        try:
            thread.join(timeout=2)
        except:
            pass
    with global_randomx_lock:
        if global_randomx_dataset is not None:
            try:
                randomx.randomx_release_dataset(global_randomx_dataset)
            except:
                pass
            global_randomx_dataset = None
        if global_randomx_cache is not None:
            try:
                randomx.randomx_release_cache(global_randomx_cache)
            except:
                pass
            global_randomx_cache = None
    if sock:
        try:
            sock.close()
        except:
            pass
def meets_submit_throttle(hash_int, throttle):
    """
    Check if share meets throttling criteria
    Args:
        hash_int: 64-bit hash value
        throttle: Maximum difficulty for submission
    Returns:
        Boolean indicating if share should be submitted
    """
    if throttle is None:
        return True
    MAX_TARGET_64 = 0xFFFFFFFFFFFFFFFF
    throttle_target = MAX_TARGET_64 // throttle
    return hash_int < throttle_target
def optimize_mining_environment():
    """Optimize system environment for mining performance"""
    try:
        p = psutil.Process()
        p.nice(psutil.REALTIME_PRIORITY_CLASS)
        kernel32 = ctypes.windll.kernel32
        kernel32.SetErrorMode(0x0001 | 0x0002 | 0x0004)
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]{Fore.CYAN}  [INF] {Style.RESET_ALL}environment optimized for mining successfully")
    except Exception as e:
        if args.debug:
            BackgroundLogger.debug(f"Optimization error: {e}")
def reconnect_to_pool():
    """
    Re-establish connection to mining pool after disconnect
    Returns:
        New socket connection or None if reconnection failed
    """
    global session_id, pool_socket, pool_host, pool_port
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]{Fore.MAGENTA}  [NET] {Style.RESET_ALL}attempting to reconnect...")
    try:
        try:
            pool_socket.close()
        except:
            pass
        new_socket = create_pool_connection(pool_host, pool_port, args.tls, args.tls_insecure)
        login_msg = {
            "id": 1,
            "method": "login",
            "params": {
                "login": args.user,
                "pass": args.password,
                "hashrate": args.threads * 150,
                "agent": "XMRig/6.20.0 (Windows NT 10.0; Win64; x64)"
            }
        }
        stratum_send(new_socket, login_msg)
        
        response = None
        timeout = time.time() + 10
        
        while time.time() < timeout and not shutdown_flag.is_set():
            try:
                msg = net_queue.get(timeout=1)
                if msg.get("id") == 1:
                    response = msg
                    break
            except queue.Empty:
                continue
        
        if not response or response.get("error"):
            error_msg = response.get
