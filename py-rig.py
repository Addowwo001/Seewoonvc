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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PYRIG.  If not, see <https://www.gnu.org/licenses/>.

# Import daftar pustaka
import os, sys, time, ssl, json, socket, psutil, struct, threading, queue, argparse, platform, ctypes
from datetime import datetime; from cffi import FFI

# Semua global variables
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
parser = argparse.ArgumentParser(description="PY-RIG (V.2.0.1) - Monero CPU Miner", formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("-o", "--url", dest="url",
    default="66.23.199.44:20001",
    help="Pool URL in format host:port (stratum+tcp:// prefix optional)")
parser.add_argument("-u", "--user", dest="user",
    default="43jBxAR5zV4HvJLpMzECjt6QLs3zEhrhfKqxaRVGfY2f614Do1NbFgZekjtdE9fDRw6R4fP2q2N2i7427bsLTSxdCGFVfmr",
    help="Wallet address and optional worker name for pool authentication")
parser.add_argument("-p", "--password", dest="password",
    default="d=10000",
    help="Password for pool authentication (default: 'x')")
parser.add_argument("--tls", action="store_true", dest="tls",
    default=True,
    help="Enable TLS/SSL encryption for pool connection (default)")
parser.add_argument("--no-tls", action="store_false", dest="tls",
    help="Disable TLS/SSL encryption for pool connection")
parser.add_argument("--tls-insecure", action="store_true",
    default=False,
    help="Disable TLS certificate verification (use with caution)")
parser.add_argument("--mode", dest="mode", choices=["full", "light"],
    default="light",
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

# Class fungsi untuk background logger
class BackgroundLogger:
    """Logging utility with background mode awareness"""
    @staticmethod
    def debug(message):
        """Log debug message only when debug mode is enabled"""
        if args.debug and not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [DEBUG] {message}")
    @staticmethod
    def error(message):
        """Log error message (always displayed)"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [ERROR] {message}")

# Display logo program
def display_startup_banner():
    """Display miner startup banner with configuration summary"""
    if args.background:
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {'*' * 15}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * PYRIG (V.2.0.1) / Python Version ({sys.version.split()[0]})")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {'*' * 15}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * ABOUT     : https://github.com/codeanli/py-rig")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * DONATE    : USDT: 0x7FF1753ac9fb1fb2008f1328bd79d0d70B7D3831 (BSC)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {'*' * 15}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * CONFIGURATION")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * POOL ADDRESS    : stratum+tcp://{args.url}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * WALLET ADDRESS  : {args.user}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * RANDOMX MODE    : {args.mode} | Total memory ({psutil.virtual_memory().total // 1024**3}GB)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * CPU THREADS     : {args.threads}T | ({platform.processor()}, {psutil.cpu_count(logical=False)}C, {psutil.cpu_count(logical=True)}T)")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * TLS/SSL CONNECT : {args.tls}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * TIMEOUT INIT    : {args.init_timeout}s | RandomX init timeout default 120s")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  * Throttle Submit : {args.submit_throttle}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {'*' * 15}")
display_startup_banner()

# Fungsi pendukung program
def stratum_send(sock, message):
    """
    Send JSON message to stratum pool server
    Args:
        sock: Socket connection to pool
        message: Dictionary containing stratum protocol message
    """
    if args.debug and not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [DEBUG] NET ->  {json.dumps(message)}")
    data = json.dumps(message) + "\n"
    try:
        sock.sendall(data.encode("utf-8"))
    except Exception as e:
        BackgroundLogger.error(f"Send failed: {e}")
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
        BackgroundLogger.error(f"Connection failed: {e}")
        sys.exit(1)
def hex_to_bytes(hex_str):
    """Convert hexadecimal string to bytes"""
    return bytes.fromhex(hex_str)
def bytes_to_hex(byte_data):
    """Convert bytes to hexadecimal string"""
    return byte_data.hex()
def format_difficulty(diff):
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
        print(f"[DEBUG-DIFFICULTY] Raw hex: 0x{target_hex}")
        print(f"[DEBUG-DIFFICULTY] Difficulty: {difficulty:,}")
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
            print("[WRN] Seed hash changed! Re-initializing RandomX VMs...")
        seed_changed_flag.set()
    current_seed_hash = new_seed
    jobs_queued = 0
    for _ in range(args.threads):  # ← Put N times!
        try:
            job_queue.put_nowait(job_data)
            jobs_queued += 1
        except queue.Full:
            if args.debug:
                print(f"[DEBUG] Job queue full after {jobs_queued} jobs")
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
        print(f"[DEBUG-TARGET-CONV] Difficulty from pool: {difficulty:,}")
        print(f"[DEBUG-TARGET-CONV] Calculated target: 0x{target_64bit:016x}")
        print(f"[DEBUG-TARGET-CONV] For mining: hash must be < 0x{target_64bit:016x}")
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
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [APP] speed now {format_hashrate(rate_now)} 10s {format_hashrate(rate_10s)} 60s {format_hashrate(rate_60s)}")
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
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [APP] shutting down...")
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
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] Environment optimized for mining")
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
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] attempting to reconnect...")
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
                "agent": "XMRig/6.12.1 (Windows NT 10.0; Win64; x64) msvc/2019"
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
            error_msg = response.get("error") if response else "timeout"
            BackgroundLogger.error(f"Reconnect failed: {error_msg}")
            return None
        result_data = response.get("result", {})
        if isinstance(result_data, dict):
            new_session_id = result_data.get("id")
        else:
            new_session_id = str(result_data) if result_data else None
        if not new_session_id:
            BackgroundLogger.error("No session id in reconnect response")
            return None
        session_id = new_session_id
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] reconnected, new session: {session_id}")
        return new_socket
    except Exception as e:
        BackgroundLogger.error(f"Reconnect error: {e}")
        return None
def stratum_recv_loop(sock):
    """
    Receive loop for stratum protocol messages
    Args:
        sock: Socket connection to pool
    """
    buffer = b""
    sock.settimeout(2)
    while not shutdown_flag.is_set():
        try:
            data = sock.recv(4096)
            if not data:
                BackgroundLogger.error("Pool closed connection")
                shutdown_flag.set()
                break
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode())
                    if args.debug and not args.background:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [DEBUG] NET   {json.dumps(msg)}")
                    msg_id = msg.get("id")
                    if msg_id is not None and msg_id >= 1000:
                        submit_response_queue.put(msg)
                    else:
                        net_queue.put(msg)
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            if shutdown_flag.is_set():
                BackgroundLogger.debug("Socket timeout, shutdown detected")
                break
            continue
        except ConnectionError as e:
            BackgroundLogger.error(f"Connection error: {e}")
            shutdown_flag.set()
            break
        except Exception as e:
            BackgroundLogger.error(f"Receive failed: {e}")
            shutdown_flag.set()
            break
def mining_worker(worker_id, flags, initial_seed):
    global current_batch_size, hash_counter

    try:
        SetThreadPriority = ctypes.windll.kernel32.SetThreadPriority
        GetCurrentThread = ctypes.windll.kernel32.GetCurrentThread
        SetThreadPriority(GetCurrentThread(), 2)
    except Exception:
        pass

    vm = RandomXVM(flags, initial_seed, worker_id)
    if not vm.wait_until_ready(args.init_timeout):
        print(f"[ERROR] Worker {worker_id} VM init timeout")
        return

    current_job_data = None
    current_target_int = None
    current_blob_template = None
    current_nonce_offset = 39
    hash_output_buffer = ffi.new("char[32]")

    while not shutdown_flag.is_set():

        # ===== SEED CHANGE =====
        if seed_changed_flag.is_set():
            vm.destroy()
            vm = RandomXVM(flags, current_seed_hash, worker_id)
            if not vm.wait_until_ready(args.init_timeout):
                return
            seed_changed_flag.clear()
            current_job_data = None
            continue

        # ===== GET JOB =====
        try:
            new_job = job_queue.get_nowait()
        except queue.Empty:
            new_job = None

        if new_job and (current_job_data is None or new_job["job_id"] != current_job_data["job_id"]):
            current_job_data = new_job
            current_blob_template = bytearray(hex_to_bytes(new_job["blob"]))
            current_nonce_offset = new_job.get("nonce_offset", 39)

            target_bytes = convert_compact_target_to_bytes(new_job["target"])
            current_target_int = int.from_bytes(target_bytes, "big")

        if current_job_data is None:
            time.sleep(0.001)
            continue

        # =========================
        # MINING BATCH
        # =========================

        with batch_lock:
            batch_size = max(current_batch_size, BATCH_MIN)

        batch_start = time.time()
        local_count = 0

        for i in range(batch_size):
            if shutdown_flag.is_set():
                break

            nonce = vm.get_next_nonce()
            current_blob_template[current_nonce_offset:current_nonce_offset + 4] = struct.pack("<I", nonce)

            try:
                randomx.randomx_calculate_hash(
                    vm.vm,
                    bytes(current_blob_template),
                    len(current_blob_template),
                    hash_output_buffer
                )
            except Exception:
                continue

            hash_bytes = bytes(ffi.buffer(hash_output_buffer, 32))
            hash_int = int.from_bytes(hash_bytes[0:8], "little")

            # realtime counter (smooth)
            local_count += 1
            if local_count >= 50:
                with hash_counter_lock:
                    hash_counter += local_count
                local_count = 0

            # submit share
            if hash_int < current_target_int and meets_submit_throttle(hash_int, args.submit_throttle):
                try:
                    submit_queue.put_nowait({
                        "job_id": current_job_data["job_id"],
                        "nonce": struct.pack("<I", nonce).hex(),
                        "result": bytes_to_hex(hash_bytes),
                        "worker_id": worker_id,
                        "timestamp": time.time()
                    })
                except queue.Full:
                    pass

        # flush remainder
        if local_count:
            with hash_counter_lock:
                hash_counter += local_count

        # =========================
        # ADAPTIVE BATCH (STABLE)
        # =========================

        batch_time = time.time() - batch_start

        if batch_time > 0:
            scale = TARGET_BATCH_TIME / batch_time
            scale = max(0.7, min(1.4, scale))   # smooth like xmrig

            new_batch = int(batch_size * scale)
            new_batch = max(BATCH_MIN, min(new_batch, BATCH_MAX))

            with batch_lock:
                current_batch_size = new_batch

            if args.debug and worker_id == 0 and not args.background:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"[BATCH] {batch_size} → {new_batch} ({batch_time:.3f}s)"
                )

    vm.destroy()
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {worker_id}: stopped")
def job_queue_monitor():
    """Monitor and maintain adequate job supply in job queue"""
    while not shutdown_flag.is_set():
        current_size = job_queue.qsize()
        if current_size < args.threads and latest_job is not None:
            needed = args.threads - current_size
            with job_lock:
                current_version = job_version
            for _ in range(needed):
                try:
                    job_queue.put_nowait({**latest_job, "version": current_version})
                except queue.Full:
                    break
        time.sleep(0.05)
def submit_worker(sock):
    """
    Worker thread for submitting shares to pool
    Args:
        sock: Socket connection to pool
    """
    global submit_id_counter, session_id, pool_socket
    while not shutdown_flag.is_set():
        try:
            submit_data = submit_queue.get(timeout=1)
        except queue.Empty:
            continue
        with current_valid_job_lock:
            if submit_data["job_id"] not in [current_valid_job_id, previous_valid_job_id]:
                update_stats(rejected=1)
                if args.debug:
                    print(f"[STALE] Dropping outdated job {submit_data['job_id'][:8]}...")
                continue
        if session_id is None:
            continue
        submit_id_counter += 1
        msg_id = 1000 + submit_id_counter
        submit_msg = {
            "id": msg_id,
            "method": "submit",
            "params": {
                "id": session_id,
                "job_id": submit_data["job_id"],
                "nonce": submit_data["nonce"],
                "result": submit_data["result"]
            }
        }
        with pending_submits_lock:
            pending_submits[msg_id] = {
                "time": time.time(),
                "data": submit_data
            }
        if args.debug and not args.background:
            age = time.time() - submit_data.get("timestamp", time.time())
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [DEBUG] NET   SUBMIT: {submit_data['job_id'][:8]}... (age: {age:.2f}s)")
        stratum_send(sock, submit_msg)
        try:
            response = submit_response_queue.get(timeout=SUBMIT_TIMEOUT)
        except queue.Empty:
            if args.debug and not args.background:
                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [DEBUG-SUBMIT] No response for submit {msg_id} (timeout)")
            continue
        resp_id = response.get("id")
        if response.get("error"):
            error_code = response["error"].get("code")
            error_msg = response["error"].get("message", "")
            if error_code == -1 and "unauthenticated" in error_msg.lower():
                if not args.background:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] session expired, reconnecting...")
                new_sock = reconnect_to_pool()
                if new_sock:
                    pool_socket = new_sock
                    sock = new_sock
                with pending_submits_lock:
                    pending_count_before_clear = len(pending_submits)
                    pending_submits.clear()
                submit_id_counter = 0
                update_stats(rejected=1)
                if args.debug and not args.background and pending_count_before_clear > 0:
                    print(f"[DEBUG] Cleared {pending_count_before_clear} pending submits (unknown status)")
                continue
            else:
                if not args.background:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  miner    share rejected: {error_msg}")
                update_stats(rejected=1)
                continue
        with pending_submits_lock:
            pending_submits.pop(resp_id, None)
        update_stats(accepted=1)
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  miner    share accepted")
def cleanup_pending_submits():
    """Periodically remove stale entries from pending_submits"""
    while not shutdown_flag.is_set():
        shutdown_flag.wait(30)
        if shutdown_flag.is_set():
            break
        now = time.time()
        with pending_submits_lock:
            stale = [msg_id for msg_id, data in pending_submits.items()
                     if now - data["time"] > SUBMIT_TIMEOUT * 2]
            for msg_id in stale:
                del pending_submits[msg_id]
            if stale and args.debug and not args.background:
                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CLEANUP] Removed {len(stale)} stale pending submits")
def main():
    """Main miner initialization and control loop"""
    global session_id, latest_job, pool_socket, pool_host, pool_port
    optimize_mining_environment()
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] Starting PY-RIG Miner")
    try:
        pool_host, pool_port = parse_pool_url(args.url, args.tls)
        pool_socket = create_pool_connection(pool_host, pool_port, args.tls, args.tls_insecure)
        pool_socket.settimeout(30)
    except Exception as e:
        BackgroundLogger.error(f"Connection failed: {e}")
        sys.exit(1)
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] use pool {pool_host}:{pool_port}")
    recv_thread = threading.Thread(target=stratum_recv_loop, args=(pool_socket,), daemon=True)
    recv_thread.start()
    try:
        login_msg = {
            "id": 1,
            "method": "login",
            "params": {
                "login": args.user,
                "pass": args.password,
                "agent": "XMRig/6.12.1 (Windows NT 10.0; Win64; x64) msvc/2019"
            }
        }
        stratum_send(pool_socket, login_msg)
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
            error_msg = response.get("error") if response else "timeout"
            BackgroundLogger.error(f"Login failed: {error_msg}")
            sys.exit(1)
        result_data = response.get("result", {})
        if isinstance(result_data, dict):
            session_id = result_data.get("id")
        else:
            session_id = str(result_data) if result_data else None
        if not session_id:
            session_id = response.get("id")
        if not session_id:
            BackgroundLogger.error("No session id received")
            sys.exit(1)
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] use algo randomx")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] connected")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] session {session_id}")
        if isinstance(result_data, dict) and "job" in result_data:
            job = result_data["job"]
        else:
            job = None
            job_timeout = time.time() + 10
            while time.time() < job_timeout and not shutdown_flag.is_set():
                try:
                    msg = net_queue.get(timeout=1)
                    if msg.get("method") == "job":
                        job = msg["params"]
                        break
                except queue.Empty:
                    continue
            if not job:
                BackgroundLogger.error("No initial job")
                sys.exit(1)
        seed_hash = job["seed_hash"]
        latest_job = job
        if args.verbose and not args.background:
            difficulty = calculate_monero_difficulty(job['target'])
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [VERBOSE] net      initial job difficulty: {difficulty:,} (target: 0x{job['target']})")
        set_new_job(job)
    except Exception as e:
        BackgroundLogger.error(f"Login failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    available_flags = randomx.randomx_get_flags()
    rx_flags = randomx.RANDOMX_FLAG_DEFAULT
    if args.mode == "full":
        rx_flags |= randomx.RANDOMX_FLAG_FULL_MEM
    if available_flags & randomx.RANDOMX_FLAG_JIT:
        rx_flags |= randomx.RANDOMX_FLAG_JIT
    if available_flags & randomx.RANDOMX_FLAG_HARD_AES:
        rx_flags |= randomx.RANDOMX_FLAG_HARD_AES
    if available_flags & randomx.RANDOMX_FLAG_LARGE_PAGES:
        rx_flags |= randomx.RANDOMX_FLAG_LARGE_PAGES
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] use profile rx")
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] {args.threads} threads")
    for i in range(args.threads):
        thread = threading.Thread(target=mining_worker, args=(i, rx_flags, seed_hash), daemon=True)
        thread.start()
        mining_threads.append(thread)
    queue_monitor = threading.Thread(target=job_queue_monitor, daemon=True)
    queue_monitor.start()
    submit_thread = threading.Thread(target=submit_worker, args=(pool_socket,), daemon=True)
    submit_thread.start()
    cleanup_thread = threading.Thread(target=cleanup_pending_submits, daemon=True)
    cleanup_thread.start()
    sampler_thread = threading.Thread(target=hashrate_sampler, daemon=True)
    sampler_thread.start()
    def stats_printer():
        time.sleep(15)
        while not shutdown_flag.is_set():
            print_stats()
            time.sleep(5)
    if not args.background:
        stats_thread = threading.Thread(target=stats_printer, daemon=True)
        stats_thread.start()
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [APP] MINING STARTED")
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {'*' * 15}")
    try:
        while not shutdown_flag.is_set():
            try:
                message = net_queue.get(timeout=1)
                if message.get("method") == "job":
                    job = message["params"]
                    latest_job = job
                    set_new_job(job)
                    if not args.background:
                        difficulty = int(job["target"], 16)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [NET] new job from {pool_host}:{pool_port} difficulty: {format_difficulty(difficulty)}")
                elif message.get("method") == "keepalived":
                    stratum_send(pool_socket, {"id": None, "method": "keepalived"})
            except queue.Empty:
                time.sleep(0.01)
                continue
            except Exception as e:
                BackgroundLogger.error(f"Network error: {e}")
                break
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_miner(pool_socket)
    return pool_socket
def find_dll(filename, search_dirs):
    """
    Search for DLL file in specified directories
    Args:
        filename: Name of DLL file to find
        search_dirs: List of directories to search
    Returns:
        Full path to DLL or None if not found
    """
    for base_dir in search_dirs:
        for root, dirs, files in os.walk(base_dir):
            if filename in files:
                return os.path.join(root, filename)
    return None

# Fungsi utama - cari pustaka .dll RandomX
base_dir = os.path.dirname(__file__)
candidate_dirs = [
    os.path.join(base_dir, "build"),
    os.path.join(base_dir, "build", "windows"),
    os.path.join(base_dir, "build", "windows", "tevador"),
    os.path.join(base_dir, "assets"),
    base_dir
]
dll_path = find_dll("librandomx.dll", candidate_dirs)
if not dll_path:
    BackgroundLogger.error("RandomX library not found")
    sys.exit(1)
if not args.background:
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] Loading RandomX library from {dll_path}")

# RandomX binding file CFFI kemudian load pustaka
ffi = FFI()
ffi.cdef("""
    typedef enum {
        RANDOMX_FLAG_DEFAULT = 0,
        RANDOMX_FLAG_LARGE_PAGES = 1,
        RANDOMX_FLAG_HARD_AES = 2,
        RANDOMX_FLAG_FULL_MEM = 4,
        RANDOMX_FLAG_JIT = 8,
        RANDOMX_FLAG_SECURE = 16
    } randomx_flags;
    typedef struct randomx_cache randomx_cache;
    typedef struct randomx_dataset randomx_dataset;
    typedef struct randomx_vm randomx_vm;
    randomx_flags randomx_get_flags(void);
    randomx_cache* randomx_alloc_cache(randomx_flags flags);
    void randomx_init_cache(randomx_cache* cache, const void* key, size_t keySize);
    void randomx_release_cache(randomx_cache* cache);
    randomx_dataset* randomx_alloc_dataset(randomx_flags flags);
    unsigned long randomx_dataset_item_count(void);
    void randomx_init_dataset(randomx_dataset* dataset, randomx_cache* cache, unsigned long startItem, unsigned long itemCount);
    void randomx_release_dataset(randomx_dataset* dataset);
    randomx_vm* randomx_create_vm(randomx_flags flags, randomx_cache* cache, randomx_dataset* dataset);
    void randomx_destroy_vm(randomx_vm* machine);
    void randomx_calculate_hash(randomx_vm* machine, const void* input, size_t inputSize, void* output);
""")
try:
    randomx = ffi.dlopen(dll_path)
except Exception as e:
    BackgroundLogger.error(f"Failed to load RandomX: {e}")
    sys.exit(1)
if not args.background:
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] RandomX library loaded successfully")

# Class fungsi RandomXVM
class RandomXVM:
    """RandomX virtual machine wrapper with lifecycle management"""
    def __init__(self, flags, seed_hex, worker_id=0):
        """
        Initialize RandomX VM
        Args:
            flags: RandomX configuration flags
            seed_hex: Seed for cache initialization
            worker_id: Worker identifier for logging
        """
        global global_randomx_cache, global_randomx_dataset, global_seed_hash
        self.flags = flags
        self.seed = hex_to_bytes(seed_hex)
        self.worker_id = worker_id
        self.initialized = False
        self.cache = None
        self.dataset = None
        self.vm = None
        self.worker_nonce_lock = threading.Lock()
        self.worker_nonce = worker_id * 1000000  # Start dari nilai unik per worker
        self.init_in_background()
    def init_in_background(self):
        """Initialize VM components with shared cache/dataset"""
        def init_task():
            global global_randomx_cache, global_randomx_dataset, global_seed_hash
            try:
                mode_str = "FULL" if self.flags & randomx.RANDOMX_FLAG_FULL_MEM else "LIGHT"
                if not args.background:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: starting VM initialization ({mode_str} mode)")
                start_time = time.time()
                with global_randomx_lock:
                    if global_randomx_cache is None or global_seed_hash != self.seed.hex():
                        if global_randomx_cache is not None:
                            if global_randomx_dataset is not None:
                                randomx.randomx_release_dataset(global_randomx_dataset)
                                global_randomx_dataset = None
                            randomx.randomx_release_cache(global_randomx_cache)
                        if not args.background:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: creating SHARED cache...")
                        global_randomx_cache = randomx.randomx_alloc_cache(self.flags)
                        randomx.randomx_init_cache(global_randomx_cache, self.seed, len(self.seed))
                        global_seed_hash = self.seed.hex()
                        if not args.background:
                            cache_time = time.time() - start_time
                            print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] shared cache initialized ({cache_time:.1f}s)")
                    self.cache = global_randomx_cache
                    if self.flags & randomx.RANDOMX_FLAG_FULL_MEM:
                        if global_randomx_dataset is None:
                            if not args.background:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: allocating SHARED 2GB dataset...")
                            global_randomx_dataset = randomx.randomx_alloc_dataset(self.flags)
                            if not global_randomx_dataset:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [WARN] Dataset allocation failed, falling back to LIGHT mode")
                                self.flags &= ~randomx.RANDOMX_FLAG_FULL_MEM
                                self.dataset = ffi.NULL
                            else:
                                item_count = randomx.randomx_dataset_item_count()
                                if not args.background:
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] initializing dataset ({item_count:,} items)...")
                                ds_start = time.time()
                                randomx.randomx_init_dataset(global_randomx_dataset, global_randomx_cache, 0, item_count)
                                ds_time = time.time() - ds_start
                                if not args.background:
                                    if ds_time < 3:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] Huge pages active ({ds_time:.1f}s dataset init)")
                                    elif ds_time < 10:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [INF] Normal memory ({ds_time:.1f}s dataset init)")
                                    else:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [WARN] Slow memory/no huge pages ({ds_time:.1f}s init)")
                                self.dataset = global_randomx_dataset
                        else:
                            self.dataset = global_randomx_dataset
                            if not args.background:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: using shared dataset (already initialized)")
                    else:
                        self.dataset = ffi.NULL
                self.vm = randomx.randomx_create_vm(self.flags, self.cache, self.dataset)
                if not self.vm:
                    raise RuntimeError("VM creation failed")
                self.initialized = True
                init_time = time.time() - start_time
                if not args.background:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: ready ({init_time:.1f}s total)")
            except Exception as e:
                BackgroundLogger.error(f"Worker {self.worker_id} init failed: {e}")
                import traceback
                traceback.print_exc()
                self.initialized = False
        thread = threading.Thread(target=init_task, daemon=True)
        thread.start()
    def wait_until_ready(self, timeout=None):
        """
        Wait for VM initialization to complete
        Args:
            timeout: Maximum wait time in seconds
        Returns:
            Boolean indicating if VM is ready
        """
        start_time = time.time()
        while not self.initialized and not shutdown_flag.is_set():
            elapsed = time.time() - start_time
            if timeout and elapsed >= timeout:
                return False
            time.sleep(0.1)
        return self.initialized
    def get_next_nonce(self):
        """Get next nonce value for this worker with thread safety"""
        with self.worker_nonce_lock:
            nonce = self.worker_nonce
            self.worker_nonce = (self.worker_nonce + 1) & 0xFFFFFFFF
            return nonce
    def destroy(self):
        """Clean up VM resources - cache & dataset are SHARED, don't release!"""
        try:
            if self.vm:
                randomx.randomx_destroy_vm(self.vm)
                self.vm = None
            self.cache = None
            self.dataset = None
        except Exception as e:
            if args.debug:
                BackgroundLogger.debug(f"VM destroy error: {e}")
        self.initialized = False

# Pintu masuk utama (entry point)
if __name__ == "__main__":
    pool_socket = None
    try:
        pool_socket = main()
    except KeyboardInterrupt:
        if pool_socket:
            shutdown_miner(pool_socket)
    except Exception as e:
        BackgroundLogger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
