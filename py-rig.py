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

# Import required libraries
import os, sys, time, ssl, json, socket, psutil, struct, threading, queue, argparse, platform, ctypes, traceback
from datetime import datetime; from cffi import FFI; from colorama import init, Back, Fore, Style; init()

# All global variables
hash_counter = 0
hash_counter_lock = threading.Lock()
seed_changed_flag = threading.Event()
global_randomx_cache = None
global_randomx_dataset = None
global_randomx_lock = threading.Lock()
global_seed_hash = None
# OPTIMIZED: Increased BATCH_MIN for better efficiency with 3 cores
BATCH_MIN = 800
SUBMIT_TIMEOUT = 15
# OPTIMIZED: Increased BATCH_MAX for aggressive mining
BATCH_MAX = 10000
# OPTIMIZED: Reduced target batch time for higher throughput
TARGET_BATCH_TIME = 0.12
current_seed_hash = None
pool_socket = None
pool_host = None
pool_port = None
# OPTIMIZED: Higher starting batch size for 3 cores aggressive mode
current_batch_size = 4000
batch_lock = threading.Lock()
# OPTIMIZED: Reduced queue size to minimize memory overhead
job_queue = queue.Queue(maxsize=100)
submit_queue = queue.Queue(maxsize=500)
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
parser = argparse.ArgumentParser(description="PYRIG (V.4.0) - CPU Miner", formatter_class=argparse.ArgumentDefaultsHelpFormatter, epilog="Example: python pyrig.py -o gulf.moneroocean.stream:10128 -u <wallet> -p x --mode full --threads 4")
# Pool connection options
group_pool = parser.add_argument_group("Pool connection")
group_pool.add_argument("-o", "--url", dest="url", metavar="LINK",
    default="gulf.moneroocean.stream:10128",
    help="Pool URL in format host:port (stratum+tcp:// prefix optional)")
group_pool.add_argument("-u", "--user", dest="user", metavar="USER",
    default="43jBxAR5zV4HvJLpMzECjt6QLs3zEhrhfKqxaRVGfY2f614Do1NbFgZekjtdE9fDRw6R4fP2q2N2i7427bsLTSxdCGFVfmr",
    help="Wallet address and optional worker name for pool authentication")
group_pool.add_argument("-p", "--pass", dest="password", metavar="PASS",
    default="x",
    help="Password for pool authentication")
# TLS options
group_tls = parser.add_argument_group("TLS/SSL options")
group_tls.add_argument("--tls", action="store_true", dest="tls",
    help="Enable TLS/SSL encryption for pool connection (default)")
group_tls.add_argument("--no-tls", action="store_false", dest="tls",
    help="Disable TLS/SSL encryption for pool connection")
group_tls.add_argument("--tls-insecure", action="store_true",
    help="Disable TLS certificate verification (use with caution)")
# Mining options
group_mining = parser.add_argument_group("Mining options")
group_mining.add_argument("-t", "--threads", dest="threads", type=int, metavar="N",
    default=max(1, os.cpu_count() - 1),
    help="Number of mining threads")
group_mining.add_argument("--mode", dest="mode", choices=["full", "light"], metavar="(FULL/LIGHT)",
    default="full",
    help="RandomX mining mode: 'full' for 2GB dataset or 'light' for cache-only")
group_mining.add_argument("--init-timeout", dest="init_timeout", type=int, metavar="SEC",
    default=120,
    help="Timeout in seconds for RandomX VM initialization")
group_mining.add_argument("--submit-throttle", dest="submit_throttle", type=int, metavar="VAL",
    default=None,
    help="Maximum difficulty threshold for share submission to reduce network load")
# Logging options
group_log = parser.add_argument_group("Logging options")
group_log.add_argument("--debug", action="store_true",
    help="Enable debug logging for troubleshooting")
group_log.add_argument("--verbose", action="store_true",
    help="Enable verbose logging with detailed mining information")
group_log.add_argument("--background", action="store_true",
    help="Run miner in background mode with minimal console output")
args = parser.parse_args()

# Class functions for background logger
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
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.RED}[ERR]{Style.RESET_ALL} {message}")
    @staticmethod
    def warning(message):
        """Log warning message (always displayed unless in background mode)"""
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.YELLOW}[WRN]{Style.RESET_ALL} {message}")
    @staticmethod
    def info(message):
        """Log informational message"""
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} {message}")

# Utility functions for hex/byte conversion
def hex_to_bytes(hex_str):
    """Convert hexadecimal string to bytes"""
    return bytes.fromhex(hex_str)
def bytes_to_hex(b):
    """Convert bytes to hexadecimal string"""
    return b.hex()

# Target conversion functions
def convert_compact_target_to_bytes(compact_hex):
    """
    Convert compact target format to 32-byte target
    Args:
        compact_hex: Compact target in hex format
    Returns:
        32-byte target representation
    """
    with target_cache_lock:
        if compact_hex in target_cache:
            return target_cache[compact_hex]
    compact = int(compact_hex, 16)
    size = compact >> 24
    word = compact & 0x00FFFFFF
    if size <= 3:
        word >>= 8 * (3 - size)
        target_int = word
    else:
        target_int = word << (8 * (size - 3))
    target_bytes = target_int.to_bytes(32, byteorder='big')
    with target_cache_lock:
        target_cache[compact_hex] = target_bytes
    return target_bytes

def meets_submit_throttle(hash_int, throttle):
    """
    Check if hash meets submit throttle threshold
    Args:
        hash_int: Hash value as integer
        throttle: Maximum difficulty threshold
    Returns:
        Boolean indicating if hash should be submitted
    """
    if throttle is None:
        return True
    hash_diff = (2**64 - 1) / hash_int if hash_int > 0 else float('inf')
    return hash_diff >= throttle

# Network communication functions
def send_json_rpc(sock, method, params=None, msg_id=1):
    """
    Send JSON-RPC request to pool
    Args:
        sock: Socket connection to pool
        method: RPC method name
        params: Method parameters
        msg_id: Message identifier
    """
    payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    message = json.dumps(payload) + "\n"
    sock.sendall(message.encode('utf-8'))
    BackgroundLogger.debug(f"SENT: {message.strip()}")

def receive_json_rpc(sock):
    """
    Receive JSON-RPC response from pool
    Args:
        sock: Socket connection to pool
    Returns:
        Parsed JSON response or None
    """
    buffer = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line:
                try:
                    data = json.loads(line.decode('utf-8'))
                    BackgroundLogger.debug(f"RECV: {json.dumps(data)}")
                    return data
                except json.JSONDecodeError:
                    BackgroundLogger.warning(f"Invalid JSON received: {line}")

def connect_to_pool(url, use_tls=True, tls_insecure=False):
    """
    Establish connection to mining pool
    Args:
        url: Pool URL (host:port)
        use_tls: Enable TLS encryption
        tls_insecure: Disable certificate verification
    Returns:
        Connected socket or None on failure
    """
    global pool_host, pool_port
    url = url.replace("stratum+tcp://", "").replace("stratum+ssl://", "")
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        port = int(port_str)
    else:
        host = url
        port = 3333
    pool_host = host
    pool_port = port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        if use_tls:
            context = ssl.create_default_context()
            if tls_insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.connect((host, port))
        sock.settimeout(None)
        return sock
    except Exception as e:
        BackgroundLogger.error(f"Connection failed: {e}")
        return None

# Job distribution function
def distribute_job_to_workers(job):
    """
    Distribute new mining job to worker threads
    Args:
        job: Job data dictionary
    """
    global job_version, current_valid_job_id, current_seed_hash
    with job_lock:
        new_seed = job["seed_hash"]
        with current_valid_job_lock:
            global previous_valid_job_id
            previous_valid_job_id = current_valid_job_id
            current_valid_job_id = job["job_id"]
        if current_seed_hash and new_seed != current_seed_hash:
            if not args.background:
                BackgroundLogger.info(f"Seed change detected: {new_seed[:16]}...")
            seed_changed_flag.set()
        current_seed_hash = new_seed
        for _ in range(args.threads * 2):
            try:
                job_queue.put_nowait(job)
            except queue.Full:
                break

# Main mining loop
def main():
    """Main program entry point - coordinates mining operations"""
    global pool_socket, session_id, latest_job
    if not args.background:
        print(f"""
{Fore.CYAN}╔═══════════════════════════════════════════════════════════════╗
║                      PYRIG v4.0 - OPTIMIZED                   ║
║                   High-Performance CPU Miner                  ║
╚═══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Mode: {Fore.GREEN}FULL (2GB){Style.RESET_ALL} | Threads: {Fore.YELLOW}{args.threads}{Style.RESET_ALL} | Optimization: {Fore.RED}AGGRESSIVE{Style.RESET_ALL}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Batch Config: MIN={BATCH_MIN} MAX={BATCH_MAX} START={current_batch_size}")
    
    pool_socket = connect_to_pool(args.url, args.tls, args.tls_insecure)
    if not pool_socket:
        sys.exit(1)
    
    send_json_rpc(pool_socket, "login", {
        "login": args.user,
        "pass": args.password,
        "agent": "PYRIG/4.0-Optimized"
    })
    
    response = receive_json_rpc(pool_socket)
    if not response or "result" not in response:
        BackgroundLogger.error("Login failed")
        sys.exit(1)
    
    result = response["result"]
    session_id = result.get("id")
    job = result.get("job")
    
    if not job:
        BackgroundLogger.error("No initial job received")
        sys.exit(1)
    
    latest_job = job
    distribute_job_to_workers(job)
    
    if not args.background:
        BackgroundLogger.info(f"Connected to pool: {pool_host}:{pool_port}")
        BackgroundLogger.info(f"Initial job: {job['job_id'][:16]}... (diff: {job.get('target', 'unknown')})")
    
    flags = randomx.RANDOMX_FLAG_FULL_MEM if args.mode == "full" else 0
    if platform.system() == "Windows":
        flags |= randomx.RANDOMX_FLAG_LARGE_PAGES
    flags |= randomx.RANDOMX_FLAG_HARD_AES | randomx.RANDOMX_FLAG_JIT
    
    for i in range(args.threads):
        t = threading.Thread(target=mining_worker, args=(i, flags, job["seed_hash"]), daemon=True)
        t.start()
        mining_threads.append(t)
    
    threading.Thread(target=submit_worker, args=(pool_socket,), daemon=True).start()
    threading.Thread(target=network_receiver, args=(pool_socket,), daemon=True).start()
    threading.Thread(target=stats_printer, daemon=True).start()
    threading.Thread(target=job_queue_monitor, daemon=True).start()
    
    while not shutdown_flag.is_set():
        try:
            msg = net_queue.get(timeout=1)
            if "method" in msg and msg["method"] == "job":
                job = msg["params"]
                latest_job = job
                distribute_job_to_workers(job)
                if args.verbose and not args.background:
                    BackgroundLogger.info(f"New job: {job['job_id'][:16]}... (diff: {job.get('target', 'unknown')})")
        except queue.Empty:
            continue
        except KeyboardInterrupt:
            break
    
    return pool_socket

# Network receiver thread
def network_receiver(sock):
    """
    Receive and process messages from pool
    Args:
        sock: Pool socket connection
    """
    while not shutdown_flag.is_set():
        try:
            data = receive_json_rpc(sock)
            if not data:
                BackgroundLogger.error("Pool connection lost")
                shutdown_flag.set()
                break
            if "method" in data:
                net_queue.put(data)
            elif "id" in data:
                submit_response_queue.put(data)
        except Exception as e:
            if not shutdown_flag.is_set():
                BackgroundLogger.error(f"Network error: {e}")
            break

# Submit worker thread
def submit_worker(sock):
    """
    Handle share submission to pool
    Args:
        sock: Pool socket connection
    """
    global submit_id_counter
    while not shutdown_flag.is_set():
        try:
            submit_data = submit_queue.get(timeout=1)
        except queue.Empty:
            continue
        
        with current_valid_job_lock:
            if submit_data["job_id"] not in [current_valid_job_id, previous_valid_job_id]:
                BackgroundLogger.debug(f"Stale share discarded: {submit_data['job_id'][:16]}...")
                continue
        
        with pending_submits_lock:
            submit_id_counter += 1
            submit_id = submit_id_counter
            pending_submits[submit_id] = submit_data
        
        try:
            send_json_rpc(sock, "submit", {
                "id": session_id,
                "job_id": submit_data["job_id"],
                "nonce": submit_data["nonce"],
                "result": submit_data["result"]
            }, submit_id)
        except Exception as e:
            BackgroundLogger.error(f"Submit failed: {e}")
            with pending_submits_lock:
                pending_submits.pop(submit_id, None)
        
        try:
            response = submit_response_queue.get(timeout=SUBMIT_TIMEOUT)
            resp_id = response.get("id")
            
            with pending_submits_lock:
                original_submit = pending_submits.pop(resp_id, None)
            
            if original_submit:
                if "error" in response and response["error"]:
                    with stats_lock:
                        stats["rejected"] += 1
                    if not args.background:
                        error_msg = response["error"].get("message", "Unknown error")
                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.RED}[REJ]{Style.RESET_ALL} Share rejected: {error_msg}")
                else:
                    with stats_lock:
                        stats["accepted"] += 1
                        stats["last_share"] = time.time()
                    if not args.background:
                        worker_id = original_submit.get("worker_id", "?")
                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.GREEN}[ACC]{Style.RESET_ALL} Share accepted (worker {worker_id})")
        except queue.Empty:
            BackgroundLogger.warning("Submit response timeout")
            with pending_submits_lock:
                pending_submits.pop(submit_id, None)

# Stats printer thread
def stats_printer():
    """Print periodic mining statistics"""
    while not shutdown_flag.is_set():
        time.sleep(10)
        with stats_lock:
            now = time.time()
            elapsed = now - stats["start_time"]
            if elapsed < 1:
                continue
            
            current_hashes = hash_counter
            stats["hash_history"].append((now, current_hashes))
            stats["hash_history"] = [h for h in stats["hash_history"] if now - h[0] < 60]
            
            if len(stats["hash_history"]) >= 2:
                time_span = stats["hash_history"][-1][0] - stats["hash_history"][0][0]
                hash_span = stats["hash_history"][-1][1] - stats["hash_history"][0][1]
                hashrate_60s = hash_span / time_span if time_span > 0 else 0
            else:
                hashrate_60s = current_hashes / elapsed
            
            if not args.background:
                print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.CYAN}[STAT]{Style.RESET_ALL} Speed: {Fore.GREEN}{hashrate_60s:.1f} H/s{Style.RESET_ALL} | Shares: {Fore.YELLOW}{stats['accepted']}{Style.RESET_ALL}/{Fore.RED}{stats['rejected']}{Style.RESET_ALL} | Batch: {current_batch_size}")

# Mining worker thread
def mining_worker(worker_id, flags, initial_seed):
    """
    Individual mining worker thread
    Args:
        worker_id: Unique identifier for this worker
        flags: RandomX configuration flags
        initial_seed: Initial seed hash for VM initialization
    """
    global current_batch_size, hash_counter
    try:
        SetThreadPriority = ctypes.windll.kernel32.SetThreadPriority
        GetCurrentThread = ctypes.windll.kernel32.GetCurrentThread
        SetThreadPriority(GetCurrentThread(), 2)
    except Exception:
        pass
    vm = RandomXVM(flags, initial_seed, worker_id)
    if not vm.wait_until_ready(args.init_timeout):
        BackgroundLogger.error(f"Worker {worker_id} VM init timeout")
        return
    current_job_data = None
    current_target_int = None
    current_blob_template = None
    current_nonce_offset = 39
    hash_output_buffer = ffi.new("char[32]")
    while not shutdown_flag.is_set():
        if seed_changed_flag.is_set():
            vm.destroy()
            vm = RandomXVM(flags, current_seed_hash, worker_id)
            if not vm.wait_until_ready(args.init_timeout):
                return
            seed_changed_flag.clear()
            current_job_data = None
            continue
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
            # OPTIMIZED: Reduced sleep time for faster job pickup
            time.sleep(0.0005)
            continue
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
            local_count += 1
            # OPTIMIZED: Increased threshold to reduce lock contention
            if local_count >= 150:
                with hash_counter_lock:
                    hash_counter += local_count
                local_count = 0
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
        if local_count:
            with hash_counter_lock:
                hash_counter += local_count
        batch_time = time.time() - batch_start
        if batch_time > 0:
            scale = TARGET_BATCH_TIME / batch_time
            # OPTIMIZED: More aggressive scaling for faster adaptation
            scale = max(0.75, min(1.6, scale))
            new_batch = int(batch_size * scale)
            new_batch = max(BATCH_MIN, min(new_batch, BATCH_MAX))
            with batch_lock:
                current_batch_size = new_batch
            if args.debug and worker_id == 0 and not args.background:
                BackgroundLogger.debug(f"BATCH {batch_size} -> {new_batch} ({batch_time:.3f}s)")
    vm.destroy()
    if not args.background:
        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {worker_id}: stopped")

def job_queue_monitor():
    """Monitor and maintain adequate job supply in job queue"""
    while not shutdown_flag.is_set():
        current_size = job_queue.qsize()
        if current_size < args.threads and latest_job is not None:
            for _ in range(args.threads * 2):
                try:
                    job_queue.put_nowait(latest_job)
                except queue.Full:
                    break
        time.sleep(0.1)

def shutdown_miner(sock):
    """
    Gracefully shutdown miner
    Args:
        sock: Pool socket connection
    """
    if not args.background:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}]  {Back.YELLOW}[WRN]{Style.RESET_ALL} Shutting down...")
    shutdown_flag.set()
    for t in mining_threads:
        t.join(timeout=2)
    if sock:
        try:
            sock.close()
        except:
            pass
    with stats_lock:
        elapsed = time.time() - stats["start_time"]
        avg_hashrate = hash_counter / elapsed if elapsed > 0 else 0
        if not args.background:
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Session stats:")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL}   Runtime: {elapsed/3600:.2f}h")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL}   Avg hashrate: {avg_hashrate:.1f} H/s")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL}   Accepted: {stats['accepted']}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL}   Rejected: {stats['rejected']}")

# RandomX library loading
if not args.background:
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Loading RandomX library...")
system = platform.system()
if system == "Windows":
    dll_path = "librandomx.dll"
elif system == "Linux":
    dll_path = "./librandomx.so"
elif system == "Darwin":
    dll_path = "./librandomx.dylib"
else:
    BackgroundLogger.error(f"Unsupported OS: {system}")
    sys.exit(1)
if not os.path.exists(dll_path):
    BackgroundLogger.error(f"RandomX library not found: {dll_path}")
    sys.exit(1)
ffi = FFI()
ffi.cdef("""
    typedef enum {
        RANDOMX_FLAG_DEFAULT = 0,
        RANDOMX_FLAG_LARGE_PAGES = 1,
        RANDOMX_FLAG_HARD_AES = 2,
        RANDOMX_FLAG_FULL_MEM = 4,
        RANDOMX_FLAG_JIT = 8,
        RANDOMX_FLAG_SECURE = 16,
        RANDOMX_FLAG_ARGON2_SSSE3 = 32,
        RANDOMX_FLAG_ARGON2_AVX2 = 64,
        RANDOMX_FLAG_ARGON2 = 96
    } randomx_flags;
    typedef struct randomx_cache randomx_cache;
    typedef struct randomx_dataset randomx_dataset;
    typedef struct randomx_vm randomx_vm;
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
    print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} library loaded successfully")

# RandomXVM class functions
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
        # OPTIMIZED: Increased nonce spacing to prevent collision between workers
        self.worker_nonce = worker_id * 20000000
        self.init_in_background()
    
    def init_in_background(self):
        """Initialize VM components with shared cache/dataset"""
        def init_task():
            global global_randomx_cache, global_randomx_dataset, global_seed_hash
            try:
                mode_str = "FULL" if self.flags & randomx.RANDOMX_FLAG_FULL_MEM else "LIGHT"
                if not args.background:
                    with stats_lock:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: starting VM initialization {Fore.LIGHTCYAN_EX}({mode_str} mode){Style.RESET_ALL}")
                start_time = time.time()
                with global_randomx_lock:
                    if global_randomx_cache is None or global_seed_hash != self.seed.hex():
                        if global_randomx_cache is not None:
                            if global_randomx_dataset is not None:
                                randomx.randomx_release_dataset(global_randomx_dataset)
                                global_randomx_dataset = None
                            randomx.randomx_release_cache(global_randomx_cache)
                        if not args.background:
                            with stats_lock:
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
                                print(f"[{datetime.now().strftime('%H:%M:%S')}]  [WRN] Dataset allocation failed, falling back to LIGHT mode")
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
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Huge pages active ({ds_time:.1f}s dataset init)")
                                    elif ds_time < 10:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  {Back.BLUE}[INF]{Style.RESET_ALL} Normal memory ({ds_time:.1f}s dataset init)")
                                    else:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}]  [WRN] Slow memory/no huge pages ({ds_time:.1f}s init)")
                                self.dataset = global_randomx_dataset
                        else:
                            self.dataset = global_randomx_dataset
                            if not args.background:
                                with stats_lock:
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}]  [CPU] worker {self.worker_id}: using shared dataset (already initialized)")
                    else:
                        self.dataset = ffi.NULL
                self.vm = randomx.randomx_create_vm(self.flags, self.cache, self.dataset)
                if not self.vm:
                    raise RuntimeError("VM creation failed")
                self.initialized = True
                init_time = time.time() - start_time
                if not args.background:
                    with stats_lock:
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

# Main entry point
if __name__ == "__main__":
    pool_socket = None
    try:
        pool_socket = main()
    except KeyboardInterrupt:
        if pool_socket:
            shutdown_miner(pool_socket)
    except Exception as e:
        BackgroundLogger.error(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
