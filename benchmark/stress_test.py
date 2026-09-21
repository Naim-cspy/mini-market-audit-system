import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
import concurrent.futures
import statistics
import logging
from app import app
from core.db import get_db_connection

logging.basicConfig(level=logging.ERROR)

CONCURRENT_USERS = 25
REQUESTS_PER_USER = 20
TOTAL_OPERATIONS = CONCURRENT_USERS * REQUESTS_PER_USER

def reset_benchmark_stock():
    """Ensure sufficient stock is available for high-throughput testing."""
    conn = get_db_connection()
    conn.execute("UPDATE inventory SET product_amount_left = 5000 WHERE product_amount_left < 1000;")
    conn.commit()
    conn.close()

def simulate_cashier(worker_id):
    """Simulates a cashier scanning products and executing checkouts."""
    client = app.test_client()
    latencies = []
    success_count = 0
    failure_count = 0

    barcodes = ["P001", "P002", "P003", "P004", "P005"]

    for i in range(REQUESTS_PER_USER):
        code = barcodes[(worker_id + i) % len(barcodes)]
        start = time.perf_counter()
        try:
            # 1. Barcode scan lookup (cached fast path)
            res = client.get(f"/api/v1/product/{code}")
            if res.status_code != 200:
                failure_count += 1
                continue

            # 2. Checkout operation (atomic transactional path)
            if i % 2 == 0:
                res_check = client.post("/api/v1/checkout", json={
                    "items": [{"product_id": code, "quantity": 1}],
                    "customer_id": f"STRESS_{worker_id}_{i}"
                })
                # Check for 200 OK (or 400 OutOfStock - both are handled valid responses)
                if res_check.status_code not in (200, 400):
                    failure_count += 1
                    continue

            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            success_count += 1
        except Exception:
            failure_count += 1

    return success_count, failure_count, latencies


def run_benchmark():
    reset_benchmark_stock()

    print("=" * 65)
    print("   HIGH-CONCURRENCY STRESS TEST (HEAVY WORKLOAD SIMULATION)")
    print("=" * 65)
    print(f"  Concurrent Virtual Cashiers: {CONCURRENT_USERS}")
    print(f"  Requests per Worker:         {REQUESTS_PER_USER}")
    print(f"  Total Simulated Operations:  {TOTAL_OPERATIONS}")
    print("  Testing: Zero-Drop Throughput & SQLite WAL Lock Resilience...")
    print("-" * 65)

    start_wall = time.perf_counter()
    all_latencies = []
    total_success = 0
    total_failures = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
        futures = [executor.submit(simulate_cashier, wid) for wid in range(CONCURRENT_USERS)]
        for f in concurrent.futures.as_completed(futures):
            s, fl, lats = f.result()
            total_success += s
            total_failures += fl
            all_latencies.extend(lats)

    total_time = time.perf_counter() - start_wall
    rps = total_success / total_time if total_time > 0 else 0

    all_latencies.sort()
    p50 = statistics.median(all_latencies) if all_latencies else 0
    p95 = all_latencies[int(len(all_latencies) * 0.95)] if all_latencies else 0
    p99 = all_latencies[int(len(all_latencies) * 0.99)] if all_latencies else 0
    avg = statistics.mean(all_latencies) if all_latencies else 0

    print("\n[BENCHMARK RESULTS]")
    print(f"  Total Requests Completed: {total_success} / {TOTAL_OPERATIONS}")
    print(f"  Dropped / Error Requests: {total_failures}")
    print(f"  Success Rate:             {(total_success / TOTAL_OPERATIONS) * 100:.2f}%")
    print(f"  Wall Time:                {total_time:.2f} seconds")
    print(f"  Throughput (RPS):         {rps:.1f} req/sec")
    print(f"  Latency Average:          {avg:.2f} ms")
    print(f"  Latency p50:              {p50:.2f} ms")
    print(f"  Latency p95:              {p95:.2f} ms")
    print(f"  Latency p99:              {p99:.2f} ms")
    print("-" * 65)
    if total_failures == 0:
        print("  VERDICT: 100% PASSED - ZERO LOCKS, ZERO DROPPED REQUESTS!")
    else:
        print(f"  VERDICT: {total_failures} ERRORS ENCOUNTERED")
    print("=" * 65)


if __name__ == "__main__":
    run_benchmark()
