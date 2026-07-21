import asyncio
import httpx
import pdfplumber
import tempfile
import os
import json
from loguru import logger
from crawler.fii_dii_crawler import BROWSER_HEADERS
from utils.llm import call_llm_chat

async def download_and_parse_pdf(pdf_url: str, _retries: int = 3) -> str:
    """Download PDF from NSE and extract text using pdfplumber.

    Retries on failure (incl. SSL cert errors) before giving up. Confirmed
    live on the 2026-07-21 TVS Motor earnings event: the PDF fetch failed
    with `SSL: CERTIFICATE_VERIFY_FAILED (self-signed certificate in chain)`
    on the first attempt, yet `openssl s_client` and a standalone httpx
    request against the exact same URL moments later both verified the
    chain fine (proper DigiCert-issued cert) — i.e. NSE's Akamai edge/WAF
    intermittently serves a bad TLS handshake to what it suspects is bot
    traffic (the preceding cookie-warmup request in this same function
    already gets a 403 from NSE some of the time), not a genuinely broken
    cert. A short retry recovers cleanly; do NOT disable cert verification
    to "fix" this — that would blind us to a real MITM if one ever occurs.
    """
    if not pdf_url:
        return ""

    if not pdf_url.startswith("http"):
        pdf_url = f"https://www.nseindia.com{pdf_url}"

    for attempt in range(_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                # Need to get session cookie first for NSE
                await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
                await asyncio.sleep(1.0)

                r = await client.get(pdf_url, headers={
                    **BROWSER_HEADERS,
                    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                })
                if r.status_code != 200:
                    logger.error(f"[pdf_parser] Failed to download PDF {r.status_code}")
                    return ""

                pdf_bytes = r.content

                # Save to temp file to read with pdfplumber
                fd, path = tempfile.mkstemp(suffix=".pdf")
                try:
                    with os.fdopen(fd, 'wb') as f:
                        f.write(pdf_bytes)

                    text = ""
                    with pdfplumber.open(path) as pdf:
                        for i, page in enumerate(pdf.pages):
                            if i >= 10: # limit to first 10 pages for speed/tokens
                                break
                            page_text = page.extract_text()
                            if page_text:
                                text += page_text + "\n"
                    return text.strip()
                finally:
                    os.remove(path)
        except Exception as exc:
            if attempt < _retries - 1:
                logger.warning(
                    f"[pdf_parser] PDF fetch failed (attempt {attempt + 1}/{_retries}), "
                    f"retrying: {pdf_url}: {exc}"
                )
                await asyncio.sleep(3 * (attempt + 1))
                continue
            logger.error(f"[pdf_parser] Exception processing PDF {pdf_url}: {exc}")
            return ""
    return ""

async def analyze_announcement_llm(symbol: str, headline: str, pdf_text: str) -> dict:
    """Analyze the text with Mantle/gpt-oss-120b to get trading impact."""
    if not pdf_text:
        pdf_text = "No PDF text available. Analyze based on headline only."
    
    # Truncate text to avoid token limits
    pdf_text = pdf_text[:8000]
        
    sys_prompt = (
        "You are an expert quantitative analyst for Indian Equities. "
        "Analyze the following corporate announcement and PDF content. "
        "Return ONLY a JSON response in this exact format, with no markdown formatting or extra text:\n"
        "{\n"
        '  "impact_score": <int 0 to 100>,\n'
        '  "trading_signal": "<BUY|SELL|HOLD>",\n'
        '  "summary": "<Concise 2-sentence summary of the actual business impact>"\n'
        "}\n"
        "Score > 80 implies a strong BUY or SELL."
    )
    
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Symbol: {symbol}\nHeadline: {headline}\n\nDocument Text:\n{pdf_text}"}
    ]
    
    try:
        resp = await call_llm_chat(messages, max_tokens=300)
        # cleanup response
        resp = resp.replace("```json", "").replace("```", "").strip()
        data = json.loads(resp)
        return data
    except Exception as e:
        logger.error(f"[pdf_parser] LLM analysis failed: {e}")
        return {"impact_score": 0, "trading_signal": "HOLD", "summary": f"LLM error: {e}"}

async def process_nse_announcement(symbol: str, headline: str, pdf_url: str) -> dict:
    """Full pipeline: Download -> Extract -> Analyze"""
    logger.info(f"[pdf_parser] Processing announcement for {symbol}: {headline}")
    pdf_text = await download_and_parse_pdf(pdf_url)
    analysis = await analyze_announcement_llm(symbol, headline, pdf_text)
    return analysis

if __name__ == "__main__":
    # Test script
    async def run_test():
        # Using a dummy URL that would fail, but testing the logic
        res = await process_nse_announcement("RELIANCE.NS", "Acquisition of new plant", "")
        print(res)
    asyncio.run(run_test())
