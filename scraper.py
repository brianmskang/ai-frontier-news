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

# Get the directory where the script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if not API_KEY:
    print("❌ Error: .env 파일에 GEMINI_API_KEY가 없습니다.")
    exit(1)

genai.configure(api_key=API_KEY)

# 2. DB 초기화
DB_PATH = os.path.join(BASE_DIR, "news.db")

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

# 3. Gemini 요약 함수 (국문 요약만)
async def summarize_article(text):
    # 최신 모델 사용 (Gemini 2.5 Flash Lite)
    model = genai.GenerativeModel(
        model_name='models/gemini-2.5-flash-lite',
        generation_config={"response_mime_type": "application/json"}
    )
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
    - MUST provide all fields. DO NOT use null or empty strings.
    - Extract the publication date from the text if available. Format as YYYY-MM-DD.
    - If no date is found, leave "published_date" empty (but still provide the key).
    - Translate the headline and provide a concise 3-point summary in Korean.
    """
    try:
        response = await model.generate_content_async(prompt)
        content = response.text
        # JSON 모드 사용 시 바로 파싱 가능하나 안전을 위해 처리
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content)
        
        # Ensure no None values
        for key in ["title_en", "title_kr", "summary_kr", "published_date"]:
            if data.get(key) is None:
                data[key] = ""
        
        return data
    except Exception as e:
        print(f"  ⚠️ Gemini Error: {e}")
        return None

# 4. 스크래퍼 핵심 로직
async def scrape_and_process():
    print("🚀 Scraper Started...")
    init_db()
    
    try:
        sources_df = pd.read_csv(os.path.join(BASE_DIR, "sources.csv"))
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
                pub_date = None

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
                                
                                pub_date_raw = items[0].find('pubDate')
                                if pub_date_raw:
                                    try:
                                        pub_date = parser.parse(pub_date_raw.text).strftime("%Y-%m-%d")
                                        print(f"  📅 Date found (RSS): {pub_date}")
                                    except: pass
                    except Exception as e:
                        print(f"  ⚠️ RSS Error: {e}")

                # 2. Anthropic: PublicationList & ArticleList Strategy
                if not article_url and "anthropic.com" in url:
                    print("  🕵️‍♂️ Probing Anthropic List...")
                    try:
                        await page.goto(url, timeout=30000, wait_until='domcontentloaded')
                        
                        # Strategy A: Research/News style (PublicationList)
                        anthropic_links = await page.locator("ul[class*='PublicationList'] li a").all()
                        
                        # Strategy B: Engineering style (ArticleList)
                        if not anthropic_links:
                             print("  🕵️‍♂️ Trying ArticleList strategy (Engineering)...")
                             anthropic_links = await page.locator("article[class*='ArticleList'] a").all()

                        if not anthropic_links:
                             # Fallback
                             anthropic_links = await page.locator("main ul li a").all()

                        for link in anthropic_links:
                            href = await link.get_attribute("href")
                            if href and ("/research/" in href or "/news/" in href or "/index/" in href or "/engineering/" in href):
                                article_url = href
                                if not article_url.startswith("http"):
                                    article_url = urljoin(url, article_url)
                                print(f"  🎯 Anthropic Target: {article_url}")
                                
                                # Extract Date
                                try:
                                    # Try 'time' tag first (Research)
                                    date_el = link.locator("time")
                                    if await date_el.count() > 0:
                                        date_text = await date_el.first.inner_text()
                                        pub_date = parser.parse(date_text).strftime("%Y-%m-%d")
                                        print(f"  📅 Date found (HTML time): {pub_date}")
                                    else:
                                        # Try class containing 'date' (Engineering)
                                        date_div = link.locator("div[class*='date']")
                                        if await date_div.count() > 0:
                                            date_text = await date_div.first.inner_text()
                                            pub_date = parser.parse(date_text).strftime("%Y-%m-%d")
                                            print(f"  📅 Date found (HTML div): {pub_date}")
                                except Exception as e: 
                                    print(f"  ⚠️ Date parse debug: {e}")
                                break
                    except Exception as e:
                        print(f"  ⚠️ Anthropic Error: {e}")

                # 3. Google Blog Strategy (Latest Articles Feed)
                if not article_url and "blog.google" in url:
                    print("  🕵️‍♂️ Probing Google Blog (Latest Feed)...")
                    try:
                        if page.url != url:
                            await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                        
                        # Wait for the feed to appear
                        try:
                            await page.wait_for_selector("ul.article-list__feed", timeout=5000)
                        except: pass
                        
                        # Find the first item in the 'All the Latest' feed
                        latest_items = await page.locator("ul.article-list__feed li.article-list__item").all()
                        
                        if latest_items:
                            # Usually the first item is the newest in this specific feed
                            first_item = latest_items[0]
                            
                            # Extract Link
                            link_el = first_item.locator("a.feed-article__overlay")
                            if await link_el.count() > 0:
                                href = await link_el.get_attribute("href")
                                if href:
                                    article_url = href
                                    if not article_url.startswith("http"):
                                        article_url = urljoin(url, article_url)
                                    print(f"  🎯 Google Target (Feed): {article_url}")
                                    
                                    # Extract Date (e.g., "Feb 02", "Dec 2025")
                                    try:
                                        date_el = first_item.locator("span.eyebrow__date")
                                        if await date_el.count() > 0:
                                            date_text = await date_el.inner_text()
                                            # If year is missing (e.g. "Feb 02"), parser assumes current year.
                                            # If today is 2026 and date is "Dec", parser might default to Dec 2026 (future).
                                            # We need to handle this carefully or trust dateutil for now.
                                            # "Dec 2025" works fine. "Feb 02" works fine for current year.
                                            pub_date = parser.parse(date_text).strftime("%Y-%m-%d")
                                            print(f"  📅 Date found (HTML): {pub_date}")
                                    except Exception as e:
                                        print(f"  ⚠️ Google Date parse error: {e}")
                                        
                    except Exception as e:
                        print(f"  ⚠️ Google Error: {e}")

                # 4. General Fallback
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
                    
                    # Ensure result is a dictionary
                    if result and isinstance(result, list) and len(result) > 0:
                        result = result[0]

                    if result and isinstance(result, dict):
                        final_date = pub_date
                        if not final_date and result.get('published_date'):
                            try:
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
                            final_date,
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
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    
    # 발행일(published_date) 기준 필터링: 어제와 오늘(2일치) 기사만 포함
    from datetime import timedelta
    yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    query = f"SELECT * FROM articles WHERE published_date >= '{yesterday_str}'"
    
    try:
        df = pd.read_sql_query(query, conn)
    except:
        df = pd.DataFrame()
    conn.close()
    
    # Ensure daily_reports directory exists
    output_dir = os.path.join(BASE_DIR, "daily_reports")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    # 버전 관리 로직 (v1, v2, v3...)
    version = 1
    while True:
        filename = os.path.join(output_dir, f"{today_str}_AI-NEWS-DAILY_v{version}.md")
        if not os.path.exists(filename):
            break
        version += 1
    
    md_content = f"# 📅 {today_str} AI NEWS DAILY (v{version})\n\n"
    md_content += "---\n\n"
    
    if df.empty:
        md_content += "최근 24시간 내에 수집된 뉴스가 없습니다.\n"
    else:
        # Fill NaN values to avoid 'nan' string in report
        df = df.fillna("")
        df = df.sort_values(by='scraped_at', ascending=False)
        latest_df = df.groupby('source', as_index=False).head(1)
        
        for _, row in latest_df.iterrows():
            title_kr = row['title_kr'] if row['title_kr'] else "제목 없음"
            summary_kr = row['summary_kr'] if row['summary_kr'] else "요약 없음"
            title_en = row['title_en'] if row['title_en'] else "No English Title"
            
            md_content += f"## [{row['source']}] {title_kr}\n"
            md_content += f"**날짜:** {row['published_date']}\n\n"
            md_content += f"> {title_en}\n\n"
            md_content += f"**[원문 보러가기]({row['url']})**\n\n"
            md_content += f"### 📝 핵심 요약\n{summary_kr}\n\n"
            md_content += "---\n\n"
            
    with open(filename, "w") as f:
        f.write(md_content)
    print(f"\n📄 Daily Report Generated: {filename}")

def run_cli():
    asyncio.run(scrape_and_process())

if __name__ == "__main__":
    asyncio.run(scrape_and_process())