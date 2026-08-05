"""
AI Analyzer Engine
Loki'den hata loglarını çeker, LangChain + Groq ile analiz eder ve
Kök Neden Analizi (RCA) + Çözüm Önerileri üretir.
Ayrıca Discord webhook ile anlık bildirim gönderir.
"""
import os
import time
import json
import logging
import threading
import httpx
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from starlette.responses import Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ai_analyzer")

app = FastAPI(title="AIOps AI Analyzer")

# --- Prometheus Metrikleri ---
ANALYSIS_COUNT = Counter('aiops_analyses_total', 'Toplam AI analiz sayısı')
ALERT_COUNT = Counter('aiops_alerts_total', 'Gönderilen toplam uyarı sayısı')

# --- Ortam Değişkenleri ---
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
LOKI_URL           = os.getenv("LOKI_URL", "http://loki:3100")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
ANALYSIS_INTERVAL  = int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "60"))

# --- LangChain Kurulumu ---
RCA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Sen bir üst düzey Site Reliability Engineer (SRE) ve sistem analisti yapay zekasısın.
Sana verilen sistem loglarını analiz ederek:
1. **Kök Neden (Root Cause):** Hatanın asıl sebebini tespit et.
2. **Etki Analizi (Impact):** Bu hatanın sisteme ve kullanıcılara etkisini açıkla.
3. **Acil Çözüm (Immediate Fix):** Hemen yapılması gereken adımları yaz.
4. **Kalıcı Çözüm (Long-term Fix):** Tekrarlanmaması için önerileri listele.
5. **Risk Seviyesi (Risk Level):** CRITICAL / HIGH / MEDIUM / LOW olarak değerlendir.

Yanıtını her zaman Türkçe ver. Teknik ama anlaşılır bir dil kullan."""),
    ("human", "Analiz edilecek loglar:\n\n{logs}")
])


def get_llm():
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY bulunamadı. Mock yanıt üretilecek.")
        return None
    return ChatGroq(api_key=GROQ_API_KEY, model="llama3-8b-8192", temperature=0.1)


def fetch_error_logs() -> list[str]:
    """Son 5 dakikadaki ERROR ve WARN loglarını Loki'den çeker."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(minutes=5)

    params = {
        "query": '{container="aiops-dummy"} |= "ERROR" or {container="aiops-dummy"} |= "WARN"',
        "start": str(int(start.timestamp() * 1e9)),
        "end":   str(int(end.timestamp() * 1e9)),
        "limit": "100",
    }

    try:
        resp = httpx.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logs = []
        for stream in data.get("data", {}).get("result", []):
            for _, line in stream.get("values", []):
                logs.append(line)
        return logs
    except Exception as e:
        logger.error(f"Loki'den log çekilemedi: {e}")
        return []


def send_telegram_alert(analysis: str, log_count: int):
    """Telegram bot üzerinden analiz sonucunu gönderir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # MarkdownV2 veya HTML formatında mesaj hazırlayabiliriz
    message = (
        f"🚨 <b>AIOps - Sistem Anomali Raporu</b>\n"
        f"<i>Son 5 dakikada {log_count} hata logu tespit edildi.</i>\n\n"
        f"🤖 <b>AI Analiz Özeti:</b>\n"
        f"<pre>{analysis[:3000]}</pre>\n\n"
        f"🕰️ <i>AIOps AI Analyzer • {timestamp}</i>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram bildirimi başarıyla gönderildi.")
        ALERT_COUNT.inc()
    except Exception as e:
        logger.error(f"Telegram bildirimi gönderilemedi: {e}")


def run_analysis():
    """Ana analiz döngüsü: Log çek → AI'ya gönder → Sonucu logla → Discord'a bildir."""
    llm = get_llm()
    chain = (RCA_PROMPT | llm | StrOutputParser()) if llm else None

    while True:
        logger.info(f"🔍 Analiz döngüsü başladı...")
        logs = fetch_error_logs()

        if not logs:
            logger.info("Son 5 dakikada analiz edilecek hata logu bulunamadı.")
        else:
            log_text = "\n".join(logs[:50])  # İlk 50 logu gönder
            logger.info(f"⚠️  {len(logs)} hata logu tespit edildi. AI analizi başlatılıyor...")

            if chain:
                try:
                    analysis = chain.invoke({"logs": log_text})
                    ANALYSIS_COUNT.inc()
                    logger.info(f"\n{'='*60}\n🤖 AI KÖK NEDEN ANALİZİ:\n{analysis}\n{'='*60}")
                    send_telegram_alert(analysis, len(logs))
                except Exception as e:
                    logger.error(f"AI analizi başarısız: {e}")
            else:
                # Mock yanıt (API key yoksa)
                mock = (
                    "**Kök Neden:** GROQ_API_KEY tanımlı olmadığı için gerçek analiz yapılamadı.\n"
                    "**Eylem:** .env dosyanıza GROQ_API_KEY ekleyin ve sistemi yeniden başlatın."
                )
                logger.info(f"\n{'='*60}\n🤖 MOCK ANALİZ:\n{mock}\n{'='*60}")

        logger.info(f"⏳ Sonraki analiz {ANALYSIS_INTERVAL} saniye sonra...")
        time.sleep(ANALYSIS_INTERVAL)


@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=run_analysis, daemon=True)
    thread.start()
    logger.info("AI Analyzer başlatıldı.")


@app.get("/")
def root():
    return {"status": "running", "service": "AIOps AI Analyzer"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/analyze/now")
def analyze_now():
    """Analizi manuel olarak anında tetikler."""
    logs = fetch_error_logs()
    if not logs:
        return {"message": "Analiz edilecek hata logu bulunamadı.", "log_count": 0}

    llm = get_llm()
    if not llm:
        return {"message": "GROQ_API_KEY eksik, analiz yapılamadı.", "log_count": len(logs)}

    chain = RCA_PROMPT | llm | StrOutputParser()
    log_text = "\n".join(logs[:50])
    analysis = chain.invoke({"logs": log_text})
    ANALYSIS_COUNT.inc()
    send_telegram_alert(analysis, len(logs))
    return {"analysis": analysis, "log_count": len(logs)}
