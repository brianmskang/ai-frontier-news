import asyncio
import os
import csv
import sqlite3
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright
import google.generativeai as genai
from dotenv import load_dotenv
import json
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests
from dateutil import parser

# 1. 환경 설정 로드
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("❌ Error: .env 파일에 GEMINI_API_KEY가 없습니다.")
    exit(1)

genai.configure(api_key=API_KEY)

# 2. DB 초기화
DB_PATH = "news.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            title_en TEXT,
            title_kr TEXT,
            url TEXT UNIQUE,
            summary_en TEXT,
            summary_kr TEXT,
            published_date TEXT,
            scraped_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

# 3. Gemini 요약 함수 (날짜 추출 추가)
async def summarize_article(text):
    # 최신 모델 사용 (Gemini 2.5 Flash Lite)
    model = genai.GenerativeModel('models/gemini-2.5-flash-lite')
    prompt = f"""
    You are an expert tech news editor. Analyze the following text and extract the information in JSON format.
    
    Text:
    {text[:25000]} 

    Output Format (JSON):
    {{
        "title_en": "English Headline",
        "title_kr": "Korean Headline (Translated)",
        "summary_kr": "1. 요약 1\\n2. 요약 2\\n3. 요약 3",
        "published_date": "YYYY-MM-DD"
    }}
    Instructions:
    - Extract the publication date from the text if available. Format as YYYY-MM-DD.
    - If no date is found, leave "published_date" empty.
    """
    try:
        response = await model.generate_content_async(prompt)
        content = response.text
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        return json.loads(content)
    except Exception as e:
        print(f"  ⚠️ Gemini Error: {e}")
        return None

# 4. 스크래퍼 핵심 로직
async def scrape_and_process():
    print("🚀 Scraper Started...")
    init_db()
    
    try:
        sources_df = pd.read_csv("sources.csv")
    except Exception as e:
        print(f"❌ Error reading sources.csv: {e}")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for index, row in sources_df.iterrows():
            source_name = row['Source_Name']
            url = row['URL']
            print(f"\n🔎 Checking: {source_name}")

            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            try:
                article_url = None
                pub_date = None # 실제 발행일 변수

                # 1. OpenAI: RSS Strategy
                if "openai.com" in url:
                    print("  📡 Fetching OpenAI RSS Feed...")
                    try:
                        rss_url = "https://openai.com/news/rss.xml"
                        headers = {'User-Agent': 'Mozilla/5.0'}
                        resp = requests.get(rss_url, headers=headers, timeout=10)
                        
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.content, 'xml')
                            items = soup.find_all('item')
                            if items:
                                rss_link = items[0].find('link').text.strip()
                                print(f"  🎯 RSS Found: {rss_link}")
                                article_url = rss_link
                                
                                # Extract Date from RSS
                                pub_date_raw = items[0].find('pubDate')
                                if pub_date_raw:
                                    try:
                                        pub_date = parser.parse(pub_date_raw.text).strftime("%Y-%m-%d")
                                        print(f"  📅 Date found (RSS): {pub_date}")
                                    except: pass
                    except Exception as e:
                        print(f"  ⚠️ RSS Error: {e}")

                # 2. Anthropic: PublicationList Strategy
                if not article_url and "anthropic.com" in url:
                    print("  🕵️‍♂️ Probing Anthropic List...")
                    try:
                        await page.goto(url, timeout=30000, wait_until='domcontentloaded')
                        try:
                            await page.wait_for_selector("ul[class*='PublicationList']", timeout=5000)
                        except: pass
                        
                        anthropic_links = await page.locator("ul[class*='PublicationList'] li a").all()
                        if not anthropic_links:
                             anthropic_links = await page.locator("main ul li a").all()

                        for link in anthropic_links:
                            href = await link.get_attribute("href")
                            if href and ("/research/" in href or "/news/" in href or "/index/" in href):
                                article_url = href
                                if not article_url.startswith("http"):
                                    article_url = urljoin(url, article_url)
                                print(f"  🎯 Anthropic Target: {article_url}")
                                
                                # Extract Date from HTML (<time> tag inside the link)
                                try:
                                    time_el = link.locator("time")
                                    if await time_el.count() > 0:
                                        date_text = await time_el.first.inner_text()
                                        pub_date = parser.parse(date_text).strftime("%Y-%m-%d")
                                        print(f"  📅 Date found (HTML): {pub_date}")
                                except Exception as e:
                                    print(f"  ⚠️ Date parse error: {e}")
                                break
                    except Exception as e:
                        print(f"  ⚠️ Anthropic Error: {e}")

                # 3. General Fallback
                if not article_url:
                    if page.url != url:
                        await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except: pass

                    links = await page.locator("a").all()
                    keywords = ["/news/", "/blog/", "/research/", "/announcements/", "/posts/", "2024", "2025", "2026"]
                    for link in links:
                        href = await link.get_attribute("href")
                        if not href: continue
                        if any(x in href.lower() for x in ["login", "signup", "policy"]): continue
                        if any(k in href for k in keywords) and len(href) > 25:
                             article_url = href
                             break
                
                if not article_url:
                    print("  ❌ No valid link found.")
                    continue

                if not article_url.startswith("http"):
                     article_url = urljoin(url, article_url)

                # Duplicate Check
                cursor.execute("SELECT id FROM articles WHERE url = ?", (article_url,))
                if cursor.fetchone():
                    print("  ⏭️ Already in DB. Skipping.")
                    continue
                
                # Scrape Content
                print(f"  📖 Reading: {article_url}")
                if page.url != article_url:
                    await page.goto(article_url, timeout=60000, wait_until='domcontentloaded')
                
                content = await page.evaluate("""() => {
                    const article = document.querySelector('article') || document.querySelector('main') || document.body;
                    const clone = article.cloneNode(true);
                    const toRemove = clone.querySelectorAll('nav, footer, script, style, header, aside');
                    toRemove.forEach(el => el.remove());
                    return clone.innerText;
                }""")
                
                if len(content) < 100:
                    print("  ⚠️ Content too short.")
                else:
                    print("  🤖 Gemini Processing...")
                    result = await summarize_article(content)
                    
                    if result:
                        # Final Date Decision
                        # If we found a date via RSS/HTML, use it.
                        # If not, try Gemini's extracted date.
                        # If all fails, use Today.
                        final_date = pub_date
                        if not final_date and result.get('published_date'):
                            try:
                                # Validate extracted date format
                                final_date = parser.parse(result.get('published_date')).strftime("%Y-%m-%d")
                            except: pass
                        
                        if not final_date:
                            final_date = datetime.now().strftime("%Y-%m-%d")

                        cursor.execute('''
                            INSERT INTO articles (source, title_en, title_kr, url, summary_en, summary_kr, published_date, scraped_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            source_name, 
                            result.get('title_en', 'No Title'), 
                            result.get('title_kr', '제목 없음'), 
                            article_url, 
                            "", 
                            result.get('summary_kr', '요약 없음'), 
                            final_date, # Corrected Date
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        ))
                        conn.commit()
                        print(f"  ✅ Saved: {result.get('title_kr')} ({final_date})")
                        
                        print("  💤 Sleeping 30s...")
                        await asyncio.sleep(30)
                
            except Exception as e:
                print(f"  ❌ Error: {e}")
            finally:
                await page.close()
        
        conn.close()
        await browser.close()
    
    generate_report()

def generate_report():
    conn = sqlite3.connect(DB_PATH)
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    try:
        df = pd.read_sql_query("SELECT * FROM articles", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    
    # Create directory if it doesn't exist
    output_dir = "daily_reports"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    filename = os.path.join(output_dir, f"{today_str}_AI_NEWS_DAILY.md")
    
    md_content = f"# 📅 {today_str} AI NEWS DAILY\n\n"
    md_content += "---\n\n"
    
    if df.empty:
        md_content += "수집된 뉴스가 없습니다.\n"
    else:
        # Sort by scraped_at desc
        df = df.sort_values(by='scraped_at', ascending=False)
        # Group by source, take latest
        latest_df = df.groupby('source', as_index=False).head(1)
        
        for _, row in latest_df.iterrows():
            md_content += f"## [{row['source']}] {row['title_kr']}\n"
            md_content += f"**날짜:** {row['published_date']}\n\n"
            md_content += f"> {row['title_en']}\n\n"
            md_content += f"**[원문 보러가기]({row['url']})**\n\n"
            md_content += f"### 📝 핵심 요약\n{row['summary_kr']}\n\n"
            md_content += "---\n\n"
            
    with open(filename, "w") as f:
        f.write(md_content)
    print(f"\n📄 Daily Report Generated: {filename}")

if __name__ == "__main__":
    asyncio.run(scrape_and_process())
