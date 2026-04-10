import urllib.request
import urllib.parse
import urllib.error
import feedparser
import json
import requests
import markdown
import time
import re
import datetime
from time import mktime
import os
import yaml  # Used for parsing the external config file

# =====================================================================
# 1. Configuration Loading & Environment Setup
# =====================================================================

# Load the external YAML configuration file
try:
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"❌ Critical Error: Failed to read config.yaml. {e}")
    exit(1)

# Dynamically configure proxy settings if provided in config
if config.get('proxy', {}).get('http'):
    os.environ["http_proxy"] = config['proxy']['http']
if config.get('proxy', {}).get('https'):
    os.environ["https_proxy"] = config['proxy']['https']


# =====================================================================
# 2. Utility Functions
# =====================================================================

def call_deepseek(messages, model_name, temperature, expect_json=False):
    """
    Unified function to call any OpenAI-compatible LLM API.
    
    Args:
        messages (list): The conversation history or prompt payload.
        model_name (str): The specific model to use (e.g., deepseek-chat or deepseek-reasoner).
        temperature (float): Controls the randomness of the output.
        expect_json (bool): If True, forces the model to return a JSON object.
        
    Returns:
        str: The generated content from the LLM.
    """
    # Fetch the dynamically configured base URL from config.yaml
    url = config['llm']['base_url']
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['auth']['llm_api_key']}" # Updated to generic key name
    }
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }

    # Note: Many reasoning models (like deepseek-reasoner or o1) do not support the forced JSON format
    if expect_json and "reasoner" not in model_name.lower():
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status() # Raise an exception for bad HTTP status codes
    return response.json()['choices'][0]['message']['content']


def extract_json_from_text(text):
    """
    Extracts a JSON block from a plain text string using Regular Expressions.
    This is a fallback mechanism in case the LLM outputs extra conversational text.
    """
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return match.group(0)
    return text


def fetch_and_local_filter_arxiv():
    """
    Fetches ALL papers within the 24-hour window first, then applies domain 
    and keyword filters, and finally truncates based on the hard limit.
    """
    query = config['arxiv']['query']
    must_have_keywords = config['arxiv']['must_have_keywords']
    limit = config['arxiv']['hard_limit']
    max_retries = config['arxiv']['max_retries']
    
    start_index = 0
    results_per_page = 100
    latest_paper_time = None
    cutoff_time = None
    
    raw_window_papers = []  # To store ALL papers within the time window
    all_filtered_papers = [] # To store papers after CS and keyword filtering

    print("📡 Starting expansive arXiv data fetch (Processing entire 24h window first)...")

    # --- Phase 1: Exhaust the 24-hour Time Window ---
    while True:
        url = f'http://export.arxiv.org/api/query?search_query={urllib.parse.quote(query)}&sortBy=submittedDate&sortOrder=descending&start={start_index}&max_results={results_per_page}'

        feed = None
        for attempt in range(max_retries):
            try:
                response = urllib.request.urlopen(url, timeout=config['arxiv']['timeout'])
                feed = feedparser.parse(response.read())
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(config['arxiv']['retry_delay'])
                else:
                    print(f"❌ Network failed at index {start_index}: {e}")
                    break 

        if not feed or not feed.entries:
            break

        # Set the 24h anchor based on the first paper of the first page
        if latest_paper_time is None:
            latest_paper_time = datetime.datetime.fromtimestamp(mktime(feed.entries[0].published_parsed),
                                                                datetime.timezone.utc)
            cutoff_time = latest_paper_time - datetime.timedelta(hours=24)
            print(f"📅 Window Anchor: {latest_paper_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"⏳ Cutoff Time: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        time_boundary_reached = False
        for p in feed.entries:
            published_dt = datetime.datetime.fromtimestamp(mktime(p.published_parsed), datetime.timezone.utc)
            
            if published_dt < cutoff_time:
                time_boundary_reached = True
                break
            
            raw_window_papers.append(p)

        if time_boundary_reached:
            print(f"🏁 Reached the 24h time boundary. Collected {len(raw_window_papers)} raw entries.")
            break

        start_index += results_per_page
        print(f"  ➡️ Paging... (Total raw papers collected: {len(raw_window_papers)})")

    # --- Phase 2: Domain & Keyword Filtering ---
    print(f"🔍 Applying academic filters to {len(raw_window_papers)} papers...")
    for p in raw_window_papers:
        # 1. Computer Science Category Filter
        is_cs = any(tag.get('term', '').startswith('cs.') for tag in p.get('tags', []))
        if not is_cs:
            continue

        # 2. Keyword Relevance Filter
        text = (p.title + " " + p.summary).lower()
        if any(k.lower() in text for k in must_have_keywords):
            all_filtered_papers.append(p)

    # --- Phase 3: Final Truncation by Hard Limit ---
    final_count = len(all_filtered_papers)
    if final_count > limit:
        print(f"🚧 Truncating: Found {final_count} relevant papers, but limit is {limit}.")
        all_filtered_papers = all_filtered_papers[:limit]
    else:
        print(f"🎯 Filtered down to {final_count} high-quality candidates.")

    return all_filtered_papers


# =====================================================================
# 3. Core Processing Pipeline
# =====================================================================

def evaluate_and_filter_papers(papers):
    """
    Evaluates and filters arXiv papers using a reasoning LLM.
    
    This function injects research-specific keywords and the user's current research 
    focus into a structured prompt, then calls the LLM to score each paper's relevance.
    
    Args:
        papers (list): A list of feedparser entry objects from arXiv.
        
    Returns:
        list: A list of dictionaries containing filtered papers with high relevance scores.
    """
    model = config['llm']['model_reasoning']
    print(f"\n🧠 Calling advanced reasoning model ({model}) to strictly screen relevant literature...")
    filtered_results = []

    # Retrieve current research focus from config; fallback to a general description if empty
    current_focus = config['criteria'].get('current_focus', "General Medical Image Analysis & Generative AI")
    if not current_focus:
        current_focus = "General Medical Image Analysis & Generative AI"

    for idx, p in enumerate(papers, 1):
        # Truncate title for clean console logging
        display_title = p.title.replace('\n', ' ')[:40]
        print(f" 🔍 Evaluating [{idx}/{len(papers)}]: {display_title}...")
        
        authors = ", ".join([author.name for author in p.authors])
        arxiv_comment = p.get('arxiv_comment', 'Not specified')
        
        # Inject dynamic variables into the evaluation prompt template
        prompt_template = config['prompts']['evaluate_prompt']
        prompt = (
            prompt_template.replace("{RESEARCH_KEYWORDS}", config['criteria']['research_keywords'])
            .replace("{NEGATIVE_PROMPT}", config['criteria']['negative_prompt'])
            .replace("{CURRENT_FOCUS}", current_focus)
            .replace("{TITLE}", p.title)
            .replace("{SUMMARY}", p.summary)
        )

        try:
            # Call LLM with strict JSON format enforcement
            response = call_deepseek(
                messages=[{"role": "user", "content": prompt}], 
                model_name=model, 
                temperature=config['llm']['temp_reasoning'], 
                expect_json=True
            )
            
            # Robust JSON extraction from LLM response
            clean_json_str = extract_json_from_text(response)
            data = json.loads(clean_json_str)

            # Filtering logic: only keep papers with relevance > 0
            if data.get("relevance", 0) > 0:
                # Enrich the data object with metadata for the subsequent beautification step
                data.update({
                    "arxiv_url": p.link,
                    "title": p.title,
                    "authors_and_affiliations": authors,
                    "submission_venue": f"arXiv Comment: {arxiv_comment}"
                })
                
                filtered_results.append(data)
                print(f"    ✅ Kept: [Score: {data['relevance']}]")
            else:
                print(f"    ❌ Discarded: Low relevance or Negative Prompt triggered")

        except Exception as e:
            print(f"    ⚠️ Evaluation exception for paper '{display_title}': {e}")

    return filtered_results


def beautify_to_markdown(top_papers):
    """
    Step 2: Feeds the top K filtered papers to the advanced reasoning model
    to generate an in-depth, structured, and visually appealing Markdown report.
    """
    if not top_papers:
        return ""

    model = config['llm']['model_reasoning']
    print(f"\n🧠 Calling advanced reasoning model ({model}) to generate the final Markdown layout for Top {len(top_papers)}...")
    
    raw_content = json.dumps(top_papers, ensure_ascii=False, indent=2)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    prompt = config['prompts']['beautify_prompt']
    prompt = prompt.replace("{TODAY_STR}", today_str)
    prompt = prompt.replace("{TOP_K}", str(len(top_papers)))
    prompt = prompt.replace("{RAW_CONTENT}", raw_content)

    # Do not force JSON here, expect pure Markdown string
    return call_deepseek([{"role": "user", "content": prompt}], model, config['llm']['temp_reasoning'], False)


def send_beautiful_email(md_content):
    """
    Step 3: Converts the generated Markdown into a responsive HTML structure
    and pushes it to WeChat via the PushPlus API.
    """
    print("📲 Generating HTML and attempting to push to WeChat...")
    
    # Convert Markdown to basic HTML
    html_body = markdown.markdown(md_content, extensions=['extra', 'nl2br'])
    
    # Split the HTML to wrap each paper inside a styled CSS card
    parts = html_body.split('<h3>')
    processed_html = parts[0] 
    
    for part in parts[1:]:
        processed_html += f'<div class="paper-card"><h3>{part}</div>'

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Injecting custom CSS designed for optimal mobile viewing on WeChat
    final_html = f"""
    <html>
        <head>
            <style>
                * {{ box-sizing: border-box; }} 
                body {{ font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; color: #333; max-width: 100%; margin: 0; padding: 10px; }}
                h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50; text-align: center; font-size: 1.4em; }}
                h3 {{ color: #2980b9; margin-top: 0; border-left: 4px solid #3498db; padding-left: 10px; font-size: 1.15em; line-height: 1.3; }}
                ul {{ list-style-type: none; padding-left: 0; margin-bottom: 0; }}
                li {{ margin-bottom: 8px; font-size: 0.95em; word-wrap: break-word; }}
                ul ul {{ padding-left: 15px; list-style-type: disc; margin-top: 10px; }}
                a {{ color: #e74c3c; text-decoration: none; font-weight: bold; word-break: break-all; }}
                code {{ background-color: #e8f0fe; color: #1a73e8; padding: 2px 5px; border-radius: 4px; font-size: 0.9em; }}
                .paper-card {{ background-color: #f8f9fa; border: 1px solid #e9ecef; padding: 15px; margin-bottom: 20px; border-radius: 8px; width: 100%; clear: both; }}
            </style>
        </head>
        <body>
            {processed_html} 
            <br><hr>
            <p style="font-size: 12px; color: #7f8c8d; text-align: center;">此报告由洪语AI助手自动驱动渲染</p>
        </body>
    </html>
    """

    payload = {
        "token": config['auth']['pushplus_token'],
        "title": f"[每日 arXiv] 精选论文速递 ({today_str})",
        "content": final_html,
        "template": "html"
    }

    try:
        response = requests.post("http://www.pushplus.plus/send", json=payload, timeout=20)
        res_json = response.json()
        if res_json.get("code") == 200:
            print("🎉 WeChat push successful! Please check your device.")
        else:
            print(f"⚠️ PushPlus API returned an error: {res_json}")
            raise Exception(f"PushPlus Error: {res_json}")
    except Exception as e:
        print(f"❌ Failed to push notification: {e}")
        raise e

def pre_filter_by_titles(papers):
    """
    Step 1.5: Perform a cost-effective pre-filter based solely on titles.
    Uses the reasoning model to pick the most promising candidates.
    """
    if not papers:
        return []

    pre_filter_k = config['filter'].get('pre_filter_k', 20)
    # If the number of papers is already small, skip pre-filtering
    if len(papers) <= pre_filter_k:
        return papers

    model = config['llm']['model_chat'] # Use chat model for speed and cost
    print(f"\n🧠 Pre-filtering {len(papers)} titles to pick top {pre_filter_k} candidates...")

    # Construct the title list for the prompt
    title_list_str = ""
    for idx, p in enumerate(papers):
        title_list_str += f"{idx}. {p.title}\n"

    prompt = config['prompts']['pre_filter_prompt']
    prompt = prompt.replace("{RESEARCH_KEYWORDS}", config['criteria']['research_keywords'])
    prompt = prompt.replace("{PRE_FILTER_K}", str(pre_filter_k))
    prompt = prompt.replace("{TITLE_LIST}", title_list_str)

    try:
        res = call_deepseek([{"role": "user", "content": prompt}], model, 0.1, True)
        selected_indices = json.loads(extract_json_from_text(res))
        
        # Ensure it's a list of integers
        if isinstance(selected_indices, list):
            selected_papers = [papers[i] for i in selected_indices if i < len(papers)]
            print(f"✅ Pre-filter complete: Kept {len(selected_papers)} candidates.")
            return selected_papers
    except Exception as e:
        print(f"⚠️ Pre-filter failed due to error: {e}. Proceeding with original list (truncated).")
        return papers[:pre_filter_k]

    return papers[:pre_filter_k]
    
# =====================================================================
# [4. Main Execution Controller - The Funnel Pipeline]
# =====================================================================
# Rationale:
# This pipeline implements a "Multi-Stage Intelligent Funnel" to process 
# massive academic data under a limited time window and API budget.
#
# Funnel Architecture:
# Level 1 (Hard Filter): Local Python filtering (CS domain + Keywords).
# Level 2 (Pre-Filter): Fast LLM screening based on titles only.
# Level 3 (Deep Analysis): Reasoning LLM scoring based on full abstracts.
# Final (Synthesis): LLM-driven Markdown synthesis and mobile delivery.
# =====================================================================

def run_job():
    """
    Main execution controller implementing a multi-stage filtering funnel.
    
    Status Codes:
      1 : Success - Papers found, analyzed, and pushed to WeChat.
      3 : Termination - No papers passed the LLM relevance filters.
     -1 : Retry Trigger - arXiv API connectivity issues or 0 papers fetched.
     -2 : Fatal Error - Pipeline crash or LLM parsing failures.
    """
    try:
        # --- Stage 1: Expansive Fetch & Local Hard Filter ---
        raw_papers = fetch_and_local_filter_arxiv()

        # Check for abnormal 0-fetch results (likely API/Proxy blocks)
        if not raw_papers:
            print("⚠️ WARNING: Fetched 0 papers. This is abnormal for broad queries; indicating an API or network issue.")
            return -1

        # --- Stage 2: Title-based Pre-filter (Level 2 Funnel) ---
        # Reduces the number of papers before the expensive reasoning stage
        pre_filtered_pool = pre_filter_by_titles(raw_papers)

        # --- Stage 3: Deep Abstract Analysis & Scoring (Level 3 Funnel) ---
        # The reasoning model now only processes the most promising candidates
        scored_papers = evaluate_and_filter_papers(pre_filtered_pool)

        # Check if any papers survived the deep relevance scoring
        if not scored_papers:
            print("ℹ️ No relevant papers survived the deep LLM evaluation. No push notification needed.")
            return 3

        # --- Stage 4: Post-processing & Delivery ---
        top_k = config['filter']['top_k_papers']
        print(f"\n📊 Stage 4: Sorting by relevance and extracting Top {top_k}...")
        
        # Sort papers based on the 'relevance' score provided by the reasoning LLM
        scored_papers.sort(key=lambda x: x.get('relevance', 0), reverse=True)
        top_papers = scored_papers[:top_k]

        # Generate the Markdown brief and push via WeChat (PushPlus)
        md_content = beautify_to_markdown(top_papers)
        send_beautiful_email(md_content)

        print("🏁 Job completed successfully.")
        return 1

    except urllib.error.URLError as e:
        # Specific handling for network-level issues to trigger a retry via caller.py
        print(f"❌ Network Error: arXiv fetch encountered a severe exception: {e}")
        return -1 
        
    except Exception as e:
        # General exception handling for internal logic/parsing crashes
        print(f"❌ Critical Error: Main pipeline encountered an unexpected exception: {e}")
        import traceback
        traceback.print_exc() # Print full stack trace for easier debugging on GitHub/Linux servers
        return -2


if __name__ == "__main__":
    run_job()