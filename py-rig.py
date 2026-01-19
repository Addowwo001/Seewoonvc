# Copyright (C) 2026 PY-RIG
#
# This file is part of PY-RIG.
#
# PY-RIG is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PY-RIG is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PY-RIG.  If not, see <https://www.gnu.org/licenses/>.

#--> Import libraries
import os
import sys
import time
import hashlib
import re
import ssl
import json
import socket
import signal
import struct
import threading
import queue
import argparse
from datetime import datetime
from cffi import FFI
#--> Completed...

#--> Global variables
job_queue = queue.Queue()
submit_queue = queue.Queue()
mining_threads = []
shutdown_flag = threading.Event()
stats = {
    "hashes": 0,
    "accepted": 0,
    "rejected": 0,
    "last_share": time.time(),
    "start_time": time.time()
}
#--> Completed...

#--> Argument parser
parser = argparse.ArgumentParser(description="PY-RIG (V.1.0.0) - Monero CPU Miner")
parser.add_argument("-o", "--url", dest="url", 
                    default="gulf.moneroocean.stream:10032",  # Port Stratum default
                    help="Mining pool address (e.g., pool.example.com:10032)")
parser.add_argument("-u", "--user", dest="user",
                    default="45i4a8BfMon3kNy1ZhRhSV61QE4vo2trf6GsV1FdXgk5bi6ZVH9zTVcFtimwk6cKcRLpsr3ChWgg56mcRFi6VjPg9NjXGcJ",
                    help="Monero wallet address")
parser.add_argument("-p", "--password", dest="password", default="x",
                    help="Password for the mining pool")
parser.add_argument("--tls", action="store_true",
                    help="Enable SSL/TLS connection")
parser.add_argument("--tls-insecure", action="store_true",
                    help="Disable SSL certificate verification")
parser.add_argument("--mode", dest="mode", default="light",
                    choices=["full", "light"],
                    help="RandomX mode: full (2GB) or light (256MB)")
parser.add_argument("-t", "--threads", dest="threads", type=int,
                    default=max(1, os.cpu_count() - 1),
                    help=f"CPU threads to use (default: {max(1, os.cpu_count() - 1)})")
parser.add_argument("--debug", action="store_true",
                    help="Enable debug output")
args = parser.parse_args()
#--> Completed...

#--> Display program information
print(f"*"*15)
print(f"* --PY-RIG (V.1.0.0) - Monero CPU Miner")
print(f"*"*13)
print(f"* --ABOUT-US    : https://github.com/codeanli/py-rig")
print(f"* --DONATE-XMR  : 4AS5AA8LP1YCoJ7r62VWkUgDALCRV6CvnWhL3zPAFD2RDWsPZG2kMpr6Te5jHyM4J8RMZP1xCS7VU7QS7QdPAyGJUjZT1PA")
print(f"*"*13)
print(f"* [CONFIGURATION]")
print(f"* --POOL     : {args.url}")
print(f"* --WALLET   : {args.user}")
print(f"* --MODE     : {args.mode}")
print(f"* --THREADS  : {args.threads}")
print(f"* --TLS/SSL  : {args.tls}")
print(f"*"*15)
#--> Complete...

#--> Helper functions
def stratum_send(sock, message):
    """Send JSON message to stratum server"""
    if args.debug:
        print(f"[NET] Sending: {message}")
    data = json.dumps(message) + "\n"
    try:
        sock.sendall(data.encode("utf-8"))
    except Exception as e:
        print(f"[NET] Send error: {e}")
        raise
def stratum_recv(sock):
    """Receive JSON message(s) from stratum server"""
    buffer = b""
    while not shutdown_flag.is_set():
        try:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Connection to pool closed")
            buffer += chunk

            # Proses semua baris yang sudah lengkap
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(line.decode("utf-8"))
                    if args.debug:
                        print(f"[NET] Received: {message}")
                    return message
                except json.JSONDecodeError as e:
                    print(f"[NET] JSON decode error: {e}")
                    continue

        except socket.timeout:
            continue
        except Exception as e:
            print(f"[NET] Receive error: {e}")
            raise
def parse_pool_url(url, use_tls):
    """Parse mining pool URL"""
    if '://' in url:
        url = url.split('://')[1]
    
    if ':' in url:
        host, port = url.split(':', 1)
        port = int(port)
    else:
        host = url
        port = 443 if use_tls else 10032  # Default Stratum port
    
    return host, port
def create_pool_connection(host, port, use_tls, tls_insecure=False):
    """Establish connection to mining pool"""
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
        print(f"[NET] Connection error: {e}")
        sys.exit(1)
def hex_to_bytes(hex_str):
    """Convert hex string to bytes"""
    return bytes.fromhex(hex_str)
def bytes_to_hex(byte_data):
    """Convert bytes to hex string"""
    return byte_data.hex()
def reverse_hex_bytes(hex_str):
    """Reverse byte order in hex string (for Monero)"""
    bytes_data = hex_to_bytes(hex_str)
    reversed_bytes = bytes_data[::-1]
    return bytes_to_hex(reversed_bytes)
def print_stats():
    """Print mining statistics with rolling hashrate"""
    global stats
    now = time.time()
    elapsed = now - stats.get("last_stat_time", now)

    # Inisialisasi jika belum ada
    if "hashes_since_last" not in stats:
        stats["hashes_since_last"] = 0
        stats["last_stat_time"] = now
        return

    # Hitung hashrate tiap interval (misalnya 5 detik)
    if elapsed >= 5:
        hashrate = stats["hashes_since_last"] / elapsed
        accepted = stats["accepted"]
        rejected = stats["rejected"]
        
        # Format hashrate dengan satuan yang sesuai
        if hashrate >= 1000000:
            hashrate_str = f"{hashrate/1000000:.2f} MH/s"
        elif hashrate >= 1000:
            hashrate_str = f"{hashrate/1000:.2f} kH/s"
        else:
            hashrate_str = f"{hashrate:.2f} H/s"
        
        # Print statistics dengan format mirip XMRig
        print(f"[{datetime.now().strftime('%H:%M:%S')}] speed 10s/60s/15m {hashrate_str} n/a n/a max n/a")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] accepted: {accepted}/{accepted+rejected} diff n/a")
        uptime = int(now - stats["start_time"])
        print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) uptime {uptime}s")
        
        # Reset untuk interval berikutnya
        stats["last_stat_time"] = now
        stats["hashes_since_last"] = 0
def shutdown_miner(sock=None, signum=None, frame=None):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] SIGINT received")
    shutdown_flag.set()

    for thread in mining_threads:
        thread.join(timeout=2)

    if sock:
        try:
            sock.close()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Pool connection closed")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Error closing socket: {e}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Miner terminated")
    sys.exit(0)
#--> Completed...

#--> Parse pool URL
try:
    pool_host, pool_port = parse_pool_url(args.url, args.tls)
    #print(f" *   HOST     : {pool_host}")
    #print(f" *   PORT     : {pool_port}")
    #print(" * ")
except Exception as e:
    print(f"[ERROR] Invalid pool URL: {e}")
    sys.exit(1)
#--> Completed...

#--> Load RandomX library (flexible search)
def find_dll(filename, search_dirs):
    """Cari file DLL di beberapa folder dan subfolder"""
    for base_dir in search_dirs:
        for root, dirs, files in os.walk(base_dir):
            if filename in files:
                return os.path.join(root, filename)
    return None

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
    print("[ERROR] RandomX library not found in build/, build/windows/, assets/, or project root")
    sys.exit(1)

print(f"[INFO] Loading RandomX library from {dll_path}")

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
    void randomx_init_dataset(randomx_dataset* dataset, randomx_cache* cache,
                              unsigned long startItem, unsigned long itemCount);
    void randomx_release_dataset(randomx_dataset* dataset);
    
    randomx_vm* randomx_create_vm(randomx_flags flags,
                                  randomx_cache* cache,
                                  randomx_dataset* dataset);
    void randomx_destroy_vm(randomx_vm* machine);
    
    void randomx_calculate_hash(randomx_vm* machine,
                                const void* input,
                                size_t inputSize,
                                void* output);
""")

try:
    randomx = ffi.dlopen(dll_path)
    print(f"[INFO] RandomX library loaded")
except Exception as e:
    print(f"[ERROR] Error loading RandomX: {e}")
    sys.exit(1)
#--> Completed...

#--> RandomX VM Manager
class RandomXVM:
    """Manage RandomX VM with cache/dataset"""
    def __init__(self, flags, seed_hex):
        self.flags = flags
        self.seed = hex_to_bytes(seed_hex)
        self.cache = None
        self.dataset = None
        self.vm = None
        self.init_vm()
    
    def init_vm(self):
        """Initialize RandomX VM with current seed"""
        if self.cache:
            randomx.randomx_release_cache(self.cache)
        
        self.cache = randomx.randomx_alloc_cache(self.flags)
        randomx.randomx_init_cache(self.cache, self.seed, len(self.seed))
        
        if self.flags & randomx.RANDOMX_FLAG_FULL_MEM:
            if self.dataset:
                randomx.randomx_release_dataset(self.dataset)
            self.dataset = randomx.randomx_alloc_dataset(self.flags)
            item_count = randomx.randomx_dataset_item_count()
            randomx.randomx_init_dataset(self.dataset, self.cache, 0, item_count)
        else:
            self.dataset = ffi.NULL
        
        if self.vm:
            randomx.randomx_destroy_vm(self.vm)
        
        self.vm = randomx.randomx_create_vm(self.flags, self.cache, self.dataset)
    
    def calculate_hash(self, blob_bytes):
        """Calculate RandomX hash for given blob"""
        output = ffi.new("char[32]")
        randomx.randomx_calculate_hash(self.vm, blob_bytes, len(blob_bytes), output)
        result_bytes = bytes(ffi.buffer(output, 32))
        return result_bytes
    
    def destroy(self):
        """Cleanup RandomX resources"""
        if self.vm:
            randomx.randomx_destroy_vm(self.vm)
        if self.dataset:
            randomx.randomx_release_dataset(self.dataset)
        if self.cache:
            randomx.randomx_release_cache(self.cache)
#--> Completed...

#--> Mining worker thread
def mining_worker(worker_id, flags, initial_seed):
    """Mining worker thread function"""
    # Create VM for this worker
    vm = RandomXVM(flags, initial_seed)
    nonce_start = worker_id * 1000000  # Different nonce range per worker
    
    try:
        while not shutdown_flag.is_set():
            try:
                # Get job from queue (non-blocking)
                job = job_queue.get(timeout=1)
                job_id = job["job_id"]
                blob_template = hex_to_bytes(job["blob"])
                target = int(job["target"], 16)
                
                # Update VM if seed changed
                if job.get("seed_hash") and job["seed_hash"] != initial_seed:
                    initial_seed = job["seed_hash"]
                    vm.seed = hex_to_bytes(initial_seed)
                    vm.init_vm()
                
                # Mining loop
                nonce = nonce_start
                for _ in range(1000):  # Batch size
                    if shutdown_flag.is_set():
                        break
                    
                    # Insert nonce into blob
                    nonce_bytes = struct.pack("<I", nonce)
                    nonce_offset = job.get("nonce_offset", 39)
                    blob_mod = bytearray(blob_template)
                    blob_mod[nonce_offset:nonce_offset+4] = nonce_bytes
                    blob = bytes(blob_mod)
                    
                    # Calculate hash
                    hash_result = vm.calculate_hash(blob)
                    
                    # Update stats
                    stats["hashes"] += 1              # total lifetime
                    stats["hashes_since_last"] += 1   # untuk rolling hashrate
                    
                    # Check if hash meets target
                    hash_int = int.from_bytes(hash_result, byteorder='little')
                    if hash_int < target:
                        # Found valid share
                        result_hex = bytes_to_hex(hash_result)
                        nonce_hex = format(nonce, "08x")
                        
                        # Add to submit queue
                        submit_queue.put({
                            "job_id": job_id,
                            "nonce": nonce_hex,
                            "result": result_hex,
                            "worker_id": worker_id
                        })
                        break
                    
                    nonce += 1
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Worker {worker_id} error: {e}")
                continue
    
    finally:
        vm.destroy()
#--> Completed...

#--> Submit worker thread
def submit_worker(sock):
    """Handle share submissions to pool"""
    while not shutdown_flag.is_set():
        try:
            submit_data = submit_queue.get(timeout=5)
            
            submit_msg = {
                "id": 1,
                "method": "submit",
                "params": {
                    "id": "worker_" + str(submit_data["worker_id"]),
                    "job_id": submit_data["job_id"],
                    "nonce": submit_data["nonce"],
                    "result": submit_data["result"]
                }
            }
            
            stratum_send(sock, submit_msg)
            response = stratum_recv(sock)
            
            if response.get("result", {}).get("status") == "OK":
                stats["accepted"] += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) accepted share")
            else:
                stats["rejected"] += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) rejected share ({response.get('error')})")
            
            stats["last_share"] = time.time()
            
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Submit error: {e}")
            continue
#--> Completed...

#--> Main function
def main():
    """Main mining function"""
    #--> Connect to pool
    try:
        pool_socket = create_pool_connection(pool_host, pool_port, args.tls, args.tls_insecure)
        pool_socket.settimeout(5)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] (use pool) {pool_host}:{pool_port}")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        sys.exit(1)
    
    #--> Login to pool
    try:
        login_msg = {
            "id": 1,
            "method": "login",
            "params": {
                "login": args.user,
                "pass": args.password,
                "agent": "py-rig/1.0.0"
            }
        }
        stratum_send(pool_socket, login_msg)
        response = stratum_recv(pool_socket)
        
        if response.get("error"):
            print(f"[ERROR] Login failed: {response['error']}")
            sys.exit(1)
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] (use algo) randomx")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] (networks) connected")
        job_id = response["result"]["job"]["job_id"]
        blob = response["result"]["job"]["blob"]
        target = response["result"]["job"]["target"]
        seed_hash = response["result"]["job"]["seed_hash"]
        
        # Put initial job in queue
        job_queue.put({
            "job_id": job_id,
            "blob": blob,
            "target": target,
            "seed_hash": seed_hash
        })
        
    except Exception as e:
        print(f"[ERROR] Login error: {e}")
        sys.exit(1)
    
    #--> Setup RandomX flags
    rx_flags = randomx.RANDOMX_FLAG_DEFAULT
    if args.mode == "full":
        rx_flags |= randomx.RANDOMX_FLAG_FULL_MEM
    
    # Show CPU features
    available_flags = randomx.randomx_get_flags()
    features = []
    if available_flags & randomx.RANDOMX_FLAG_LARGE_PAGES:
        features.append("LARGE_PAGES")
    if available_flags & randomx.RANDOMX_FLAG_HARD_AES:
        features.append("HARD_AES")
    if available_flags & randomx.RANDOMX_FLAG_JIT:
        features.append("JIT")
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) detected")
    if features:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) features: {', '.join(features)}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) {args.threads} threads")
    
    #--> Start mining threads
    print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) READY")
    for i in range(args.threads):
        thread = threading.Thread(
            target=mining_worker,
            args=(i, rx_flags, seed_hash),
            daemon=True
        )
        thread.start()
        mining_threads.append(thread)
    
    #--> Start submit thread
    submit_thread = threading.Thread(
        target=submit_worker,
        args=(pool_socket,),
        daemon=True
    )
    submit_thread.start()
    
    #--> Start stats thread
    def stats_worker():
        while not shutdown_flag.is_set():
            print_stats()
            time.sleep(5)
    
    stats_thread = threading.Thread(target=stats_worker, daemon=True)
    stats_thread.start()
    
    #--> Main job listener
    print(f"[{datetime.now().strftime('%H:%M:%S')}] (CPU-RX/0) STARTED")
    print("")
    
    try:
        while not shutdown_flag.is_set():
            try:
                # Listen for new jobs
                message = stratum_recv(pool_socket)
                
                if message.get("method") == "job":
                    job = message["params"]
                    job_queue.put(job)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] (networks) new job from {pool_host}:{pool_port}")
                
                elif message.get("method") == "keepalived":
                    # Respond to keepalive
                    stratum_send(pool_socket, {"id": None, "method": "keepalived"})
                
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] net  error: {e}")
                break
    
    except KeyboardInterrupt:
        pass
    
    finally:
        shutdown_miner(pool_socket)
    return pool_socket
#--> Completed...

#--> Entry point
if __name__ == "__main__":
    pool_socket = None
    try:
        pool_socket = main()
    except Exception as e:
        print(f"[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        if pool_socket:
            shutdown_miner(pool_socket)
        sys.exit(1)
#--> Completed...