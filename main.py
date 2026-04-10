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




# =========================================================================================
#      █████╗ ██████╗ ██╗  ██╗██╗██╗   ██╗     ██████╗ ██████╗ ███████╗████████╗███████╗
#     ██╔══██╗██╔══██╗╚██╗██╔╝██║██║   ██║     ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝██╔════╝
#     ███████║██████╔╝ ╚███╔╝ ██║██║   ██║     ██████╔╝██║  ██║█████╗     ██║   █████╗  
#     ██╔══██║██╔══██╗ ██╔██╗ ██║╚██╗ ██╔╝     ██╔═══╝ ██║  ██║██╔══╝     ██║   ██╔══╝  
#     ██║  ██║██║  ██║██╔╝ ██╗██║ ╚████╔╝      ██║     ██████╔╝███████╗   ██║   ███████╗
#     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚════╝      ╚═╝     ╚═════╝ ╚══════╝   ╚═╝   ╚══════╝
# =========================================================================================
#
#                    [ THE MULTI-STAGE INTELLIGENT FILTERING FUNNEL ]
#
#   [ arXiv API ] 
#         |  Fetch last 24h window
#         V  (~500+ Papers)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 1: LOCAL HEURISTIC HARD FILTER (fetch_and_local_filter_arxiv)               |
#   |   1. Domain Check  : Must contain 'cs.*' tag.                                     |
#   |   2. Keyword Match : Regex match `must_have_keywords` (e.g., diffusion).          |
#   |   3. Truncation    : Cap at `hard_limit` (e.g., 100) to prevent API bloat.        |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Filtered down to ~100 Candidates)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 2: SEMANTIC TITLE PRE-FILTER (pre_filter_by_titles)                         |
#   |   * LLM Agent: deepseek-chat (High-speed, cost-effective)                         |
#   |   1. Boundary      : Exclude non-medical papers matching NEGATIVE_PROMPT.         |
#   |   2. Alignment     : Prioritize titles aligning with CURRENT_FOCUS.               |
#   |   3. Selection     : Returns indices of Top `pre_filter_k` (e.g., 25).            |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (High-potential Top 25 Titles)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 3: DEEP REASONING & SCORING (evaluate_and_filter_papers)                    |
#   |   * LLM Agent: deepseek-reasoner (Deep logic & Chain-of-Thought)                  |
#   |   1. Read Abstract : Full semantic analysis with Anti-Hallucination rules.        |
#   |   2. Matrix Scoring: Domain (D) + Method (M) + Alignment (A) + Venue Bonus.       |
#   |   3. Extraction    : JSON extract Relevance (0-5) & Novelty Tie-Breaker (1-10).   |
#   |   4. Pruning       : Discard papers with Relevance == 0.                          |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Scored & Parsed JSON Objects)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 4: MULTI-LEVEL TUPLE SORTING (run_job)                                      |
#   |   1. Primary Sort  : Relevance Score (DESC).                                      |
#   |   2. Tie-Breaker   : Novelty Score (DESC).                                        |
#   |   3. Truncation    : Isolate final `top_k_papers` (e.g., 5).                      |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Final Top 5 Papers)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 5: SYNTHESIS & MOBILE DELIVERY (beautify_to_markdown & send_email)          |
#   |   * LLM Agent: deepseek-chat (Styling & Translation)                              |
#   |   1. Synthesis     : Generate Academic Chinese Markdown (Background/Method/Impact)|
#   |   2. Styling       : Map scores to Emojis (🌟), inject mobile-friendly CSS/HTML.  |
#   |   3. Delivery      : PushPlus API -> WeChat Notification.                         |
#   +-----------------------------------------------------------------------------------+
# =========================================================================================




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
    url = config['llm']['base_url']
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['auth']['llm_api_key']}" 
    }
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }

    if expect_json and "reasoner" not in model_name.lower():
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status() 
    return response.json()['choices'][0]['message']['content']


def extract_json_from_text(text):
    """
    Extracts a JSON block from a plain text string using Regular Expressions.
    This is a fallback mechanism in case the LLM outputs extra conversational text.
    """
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return match.group(0)
    
    # Handle array cases like [0, 1, 2] for the pre_filter prompt
    match_array = re.search(r'\[[\s\S]*\]', text)
    if match_array:
        return match_array.group(0)
        
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
    
    raw_window_papers = []  
    all_filtered_papers = [] 

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

        if latest_paper_time is None:
            latest_paper_time = datetime.datetime.fromtimestamp(mktime(feed.entries[0].published_parsed), datetime.timezone.utc)
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


def pre_filter_by_titles(papers):
    """
    Step 1.5: Perform a cost-effective pre-filter based solely on titles.
    Uses the reasoning model to pick the most promising candidates.
    """
    if not papers:
        return []

    pre_filter_k = config['filter'].get('pre_filter_k', 20)
    if len(papers) <= pre_filter_k:
        return papers

    model = config['llm']['model_reasoning'] 
    print(f"\n🧠 Pre-filtering {len(papers)} titles to pick top {pre_filter_k} candidates...")

    title_list_str = ""
    for idx, p in enumerate(papers):
        title_list_str += f"{idx}. {p.title.replace(chr(10), ' ')}\n"

    # Extract formalized variables
    core_list = ", ".join(config['criteria']['research_keywords']['core_domains'])
    method_list = ", ".join(config['criteria']['research_keywords']['methodologies'])
    neg_list = ", ".join(config['criteria']['negative_prompt'])
    focus = config['criteria'].get('current_focus', "None") # <--- Focus

    prompt = config['prompts']['pre_filter_prompt']
    prompt = prompt.replace("{RESEARCH_KEYWORDS_CORE}", core_list)
    prompt = prompt.replace("{RESEARCH_KEYWORDS_METHOD}", method_list)
    prompt = prompt.replace("{NEGATIVE_PROMPT}", neg_list)
    prompt = prompt.replace("{CURRENT_FOCUS}", focus)
    prompt = prompt.replace("{PRE_FILTER_K}", str(pre_filter_k))
    prompt = prompt.replace("{TITLE_LIST}", title_list_str)

    try:
        res = call_deepseek([{"role": "user", "content": prompt}], model, 0.1, True)
        clean_res = extract_json_from_text(res)
        selected_indices = json.loads(clean_res)
        
        if isinstance(selected_indices, list):
            selected_papers = [papers[i] for i in selected_indices if isinstance(i, int) and 0 <= i < len(papers)]
            print(f"✅ Pre-filter complete: Kept {len(selected_papers)} candidates.")
            return selected_papers
    except Exception as e:
        print(f"⚠️ Pre-filter failed due to error: {e}. Proceeding with original list (truncated).")
        return papers[:pre_filter_k]

    return papers[:pre_filter_k]


# =====================================================================
# 3. Core Processing Pipeline
# =====================================================================

def evaluate_and_filter_papers(papers):
    """
    Evaluates and filters arXiv papers using a reasoning LLM.
    """
    model = config['llm']['model_reasoning']
    print(f"\n🧠 Calling advanced reasoning model ({model}) to strictly screen relevant literature...")
    filtered_results = []

    # Prepare formalized lists
    core_list = ", ".join(config['criteria']['research_keywords']['core_domains'])
    method_list = ", ".join(config['criteria']['research_keywords']['methodologies'])
    neg_list = ", ".join(config['criteria']['negative_prompt'])
    focus = config['criteria'].get('current_focus', "None")
    
    # Extract the whitelist of top-tier conferences/journals for the venue bonus calculation
    top_venues_list = ", ".join(config['criteria'].get('top_venues', []))

    for idx, p in enumerate(papers, 1):
        clean_title = p.title.replace('\n', ' ')
        print(f"\n 🔍 Evaluating [{idx}/{len(papers)}]:\n    Title: {clean_title}")
        
        authors = ", ".join([author.name for author in p.authors])
        
        # arXiv typically stores metadata (e.g., conference acceptance status) in the 'arxiv_comment' attribute
        arxiv_comment = p.get('arxiv_comment', 'Not specified')
        
        # Inject dynamic variables into the evaluation prompt template
        prompt_template = config['prompts']['evaluate_prompt']
        prompt = (
            prompt_template.replace("{RESEARCH_KEYWORDS_CORE}", core_list)
            .replace("{RESEARCH_KEYWORDS_METHOD}", method_list)
            .replace("{NEGATIVE_PROMPT}", neg_list)
            .replace("{CURRENT_FOCUS}", focus)
            .replace("{TOP_VENUES}", top_venues_list) # Inject the top venue whitelist
            .replace("{TITLE}", p.title)
            .replace("{SUMMARY}", p.summary)
            .replace("{REMARKS}", arxiv_comment)      # Inject arXiv remarks to evaluate the +1 venue bonus
        )

        try:
            response = call_deepseek([{"role": "user", "content": prompt}], model, config['llm']['temp_reasoning'], True)
            
            clean_json_str = extract_json_from_text(response)
            data = json.loads(clean_json_str)

            # Safely extract the primary relevance score.
            # This handles edge cases where the LLM might return a single-item list (e.g., [5]) instead of an integer.
            raw_score = data.get("relevance", 0)
            if isinstance(raw_score, list) and len(raw_score) > 0:
                final_score = raw_score[0]
            else:
                final_score = int(raw_score)

            # Safely extract the novelty tie-breaker score.
            # This score acts as a secondary metric to resolve ranking collisions among papers with identical relevance scores.
            raw_novelty = data.get("novelty", 0)
            if isinstance(raw_novelty, list) and len(raw_novelty) > 0:
                novelty_score = raw_novelty[0]
            else:
                novelty_score = int(raw_novelty)
            
            # Extract the Chain-of-Thought rationale for debugging and transparency.
            rationale = data.get("thought_trace", "No rationale provided.")

            if final_score > 0:
                # Enrich the parsed JSON data with original metadata and the calculated scores
                data.update({
                    "relevance": final_score, 
                    "novelty": novelty_score, # Store the tie-breaker score for Stage 4 sorting
                    "arxiv_url": p.link,
                    "title": p.title,
                    "authors_and_affiliations": authors,
                    "submission_venue": f"arXiv Comment: {arxiv_comment}"
                })
                
                filtered_results.append(data)
                
                # Log the retention event, displaying both the primary score and the tie-breaker
                print(f"    ✅ Kept   [Score: {final_score} | Novelty: {novelty_score}]")
                print(f"    💡 Reason: {rationale}")
            else:
                print(f"    ❌ Discarded [Score: {final_score}]")
                print(f"    💡 Reason: {rationale}")

        except Exception as e:
            print(f"    ⚠️ Evaluation exception for paper: {e}")

    return filtered_results


def beautify_to_markdown(top_papers):
    """
    Step 2: Feeds the top K filtered papers to the advanced reasoning model
    to generate an in-depth, structured, and visually appealing Markdown report.
    """
    if not top_papers:
        return ""

    model = config['llm']['model_reasoning']
    print(f"\n🎨 Calling chat model ({model}) to generate the final Markdown layout for Top {len(top_papers)}...")
    
    raw_content = json.dumps(top_papers, ensure_ascii=False, indent=2)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    prompt = config['prompts']['beautify_prompt']
    prompt = prompt.replace("{TODAY_STR}", today_str)
    prompt = prompt.replace("{TOP_K}", str(len(top_papers)))
    prompt = prompt.replace("{RAW_CONTENT}", raw_content)

    return call_deepseek([{"role": "user", "content": prompt}], model, config['llm']['temp_chat'], False)


def send_beautiful_email(md_content):
    """
    Step 3: Converts the generated Markdown into a responsive HTML structure
    and pushes it to WeChat via the PushPlus API.
    """
    print("📲 Generating HTML and attempting to push to WeChat...")
    
    html_body = markdown.markdown(md_content, extensions=['extra', 'nl2br'])
    
    parts = html_body.split('<h3>')
    processed_html = parts[0] 
    
    for part in parts[1:]:
        processed_html += f'<div class="paper-card"><h3>{part}</div>'

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

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


# =====================================================================
# [4. Main Execution Controller - The Funnel Pipeline]
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
        raw_papers = fetch_and_local_filter_arxiv()

        if not raw_papers:
            print("⚠️ WARNING: Fetched 0 papers. This is abnormal for broad queries; indicating an API or network issue.")
            return -1

        pre_filtered_pool = pre_filter_by_titles(raw_papers)
        scored_papers = evaluate_and_filter_papers(pre_filtered_pool)

        if not scored_papers:
            print("ℹ️ No relevant papers survived the deep LLM evaluation. No push notification needed.")
            return 3

        # --- Stage 4: Post-processing & Delivery ---
        top_k = config['filter']['top_k_papers']
        print(f"\n📊 Stage 4: Sorting by relevance and extracting Top {top_k}...")
        
        # Core Mechanism: Multi-level Tuple Sorting
        # Priority 1: Sort by primary 'relevance' score descending (e.g., 5 > 4).
        # Priority 2: In case of a tie, resolve using the 'novelty' score descending 
        #             (e.g., if both have a relevance of 4, a novelty of 9 beats a novelty of 6).
        scored_papers.sort(key=lambda x: (x.get('relevance', 0), x.get('novelty', 0)), reverse=True)
        
        # Truncate the list to the configured Top-K limit
        top_papers = scored_papers[:top_k]

        # Synthesize the Markdown report and push it to the WeChat client
        md_content = beautify_to_markdown(top_papers)
        send_beautiful_email(md_content)

        print("🏁 Job completed successfully.")
        return 1

    except urllib.error.URLError as e:
        print(f"❌ Network Error: arXiv fetch encountered a severe exception: {e}")
        return -1 
        
    except Exception as e:
        print(f"❌ Critical Error: Main pipeline encountered an unexpected exception: {e}")
        import traceback
        traceback.print_exc() 
        return -2


if __name__ == "__main__":
    run_job()
