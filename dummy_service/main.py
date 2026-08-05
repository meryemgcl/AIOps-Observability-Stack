"""
Dummy Service - Hata Simülatörü
Rastgele ERROR/WARN logları üreten ve Prometheus metrikleri yayınlayan bir servis.
"""
import random
import time
import logging
import threading
from fastapi import FastAPI
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

import os

os.makedirs("/app/logs", exist_ok=True)

# Loglama ayarları
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/logs/dummy.log")
    ]
)
logger = logging.getLogger("dummy_service")

app = FastAPI(title="AIOps Dummy Service")

# --- Prometheus Metrikleri ---
REQUEST_COUNT = Counter('dummy_requests_total', 'Toplam istek sayısı', ['status'])
ERROR_COUNT = Counter('dummy_errors_total', 'Toplam hata sayısı', ['error_type'])
CPU_USAGE = Gauge('dummy_cpu_usage_percent', 'Simüle edilen CPU kullanım yüzdesi')
MEMORY_USAGE = Gauge('dummy_memory_usage_mb', 'Simüle edilen bellek kullanımı (MB)')

# --- Hata Senaryoları ---
ERROR_SCENARIOS = [
    ("ERROR", "OutOfMemoryError: Java heap space exhausted. Used: 3900MB / 4096MB"),
    ("ERROR", "DatabaseConnectionError: Connection pool exhausted. Max connections: 100"),
    ("ERROR", "HTTPError 500: Upstream service /api/payment timed out after 30s"),
    ("ERROR", "NullPointerException in UserService.getProfile() at line 142"),
    ("WARN",  "HighLatency: Endpoint /api/search responded in 4200ms (threshold: 2000ms)"),
    ("WARN",  "DiskUsage: Partition /data is 89% full. Cleanup recommended."),
    ("WARN",  "RetryAttempt: Failed to connect to Redis. Attempt 3/5"),
    ("INFO",  "UserLogin: user_id=1045 logged in successfully from IP 192.168.1.10"),
    ("INFO",  "RequestProcessed: GET /api/products completed in 145ms"),
    ("INFO",  "ScheduledJob: DataCleanup completed. Deleted 2304 stale records."),
]

def simulate_metrics():
    """Sürekli olarak rastgele metrik ve log üretir."""
    while True:
        # Metrikleri güncelle
        cpu = random.uniform(20, 95)
        mem = random.uniform(512, 3800)
        CPU_USAGE.set(cpu)
        MEMORY_USAGE.set(mem)

        # Rastgele bir senaryo seç
        level, message = random.choice(ERROR_SCENARIOS)

        if level == "ERROR":
            logger.error(message)
            ERROR_COUNT.labels(error_type=message.split(":")[0]).inc()
            REQUEST_COUNT.labels(status="error").inc()
        elif level == "WARN":
            logger.warning(message)
            REQUEST_COUNT.labels(status="warning").inc()
        else:
            logger.info(message)
            REQUEST_COUNT.labels(status="success").inc()

        # Yüksek CPU durumunda ek uyarı
        if cpu > 85:
            logger.warning(f"HighCPU: CPU usage is critically high at {cpu:.1f}%")

        time.sleep(random.uniform(2, 8))


@app.on_event("startup")
def startup_event():
    """Uygulama başlayınca simülatörü arka planda başlat."""
    thread = threading.Thread(target=simulate_metrics, daemon=True)
    thread.start()
    logger.info("Dummy Service started. Log simulation is running.")


@app.get("/")
def root():
    return {"status": "running", "service": "AIOps Dummy Service"}


@app.get("/metrics")
def metrics():
    """Prometheus için metrik endpoint'i."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/trigger/{scenario}")
def trigger_scenario(scenario: str):
    """Belirli bir hatayı manuel olarak tetiklemek için endpoint."""
    scenarios = {
        "oom": ("ERROR", "CRITICAL OutOfMemoryError: System memory exhausted! Killing processes."),
        "db":  ("ERROR", "CRITICAL DatabaseError: Primary DB is unreachable. Failover initiated."),
        "disk": ("WARN", "CRITICAL DiskUsage: Partition /data is 98% full! Write operations failing."),
    }
    if scenario not in scenarios:
        return {"error": "Bilinmeyen senaryo. Seçenekler: oom, db, disk"}

    level, message = scenarios[scenario]
    if level == "ERROR":
        logger.error(message)
        ERROR_COUNT.labels(error_type=scenario).inc()
    else:
        logger.warning(message)

    return {"triggered": scenario, "message": message}
